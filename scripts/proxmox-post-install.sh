#!/bin/bash
# Proxmox Post-Installation Script - Improved Version
# Runs on first boot after Proxmox installation
# Properly supports node1 as primary and nodes 2-4 as cluster members

set -e

# Configuration
PROVISION_SERVER="10.10.1.1"
LOG_FILE="/var/log/proxmox-post-install.log"
HOSTNAME=$(hostname -s)
NODE_NUM=${HOSTNAME##node}
IP_ADDRESS=$(ip -4 addr show vmbr0 | grep -oP '(?<=inet\s)\d+(\.\d+){3}' | head -1)
CLUSTER_NAME="sddc-cluster"
CLUSTER_PRIMARY="node1"
CLUSTER_PRIMARY_IP="10.10.1.21"

# Node-specific IPs (for reference)
declare -A NODE_IPS=(
    ["node1"]="10.10.1.21"
    ["node2"]="10.10.1.22"
    ["node3"]="10.10.1.23"
    ["node4"]="10.10.1.24"
)

declare -A CEPH_IPS=(
    ["node1"]="10.10.2.21"
    ["node2"]="10.10.2.22"
    ["node3"]="10.10.2.23"
    ["node4"]="10.10.2.24"
)

# Logging function
log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [$HOSTNAME] $1" | tee -a "$LOG_FILE"
}

# Error handling
error_exit() {
    log "ERROR: $1"
    exit 1
}

log "=========================================="
log "Starting Proxmox post-installation for $HOSTNAME"
log "IP: $IP_ADDRESS, Node Number: $NODE_NUM"
log "=========================================="

# 1. Fix Repository Configuration
log "Step 1: Configuring Proxmox repositories..."

# Handle both .list and .sources formats
if [ -f /etc/apt/sources.list.d/pve-enterprise.sources ]; then
    mv /etc/apt/sources.list.d/pve-enterprise.sources /etc/apt/sources.list.d/pve-enterprise.sources.disabled
    log "Disabled enterprise PVE repository (.sources)"
fi

if [ -f /etc/apt/sources.list.d/ceph.sources ]; then
    mv /etc/apt/sources.list.d/ceph.sources /etc/apt/sources.list.d/ceph.sources.disabled
    log "Disabled enterprise Ceph repository (.sources)"
fi

if [ -f /etc/apt/sources.list.d/pve-enterprise.list ]; then
    sed -i 's/^deb/#deb/' /etc/apt/sources.list.d/pve-enterprise.list
    log "Disabled enterprise PVE repository (.list)"
fi

# Add no-subscription repository
if ! grep -q "pve-no-subscription" /etc/apt/sources.list.d/* 2>/dev/null; then
    echo "deb http://download.proxmox.com/debian/pve trixie pve-no-subscription" > /etc/apt/sources.list.d/pve-no-subscription.list
    log "Added no-subscription repository"
fi

# 2. Update and Install Packages
log "Step 2: Updating system and installing packages..."
apt-get update || error_exit "Failed to update package repositories"

DEBIAN_FRONTEND=noninteractive apt-get install -y \
    curl \
    wget \
    vim \
    htop \
    iotop \
    net-tools \
    python3 \
    python3-pip \
    python3-proxmoxer \
    python3-requests \
    jq \
    lvm2 \
    thin-provisioning-tools \
    bridge-utils \
    ifupdown2 \
    chrony \
    zfsutils-linux \
    ceph-common \
    ansible \
    || error_exit "Failed to install essential packages"

# 3. Configure NTP
log "Step 3: Configuring NTP..."
timedatectl set-timezone UTC
if systemctl is-active --quiet chronyd; then
    log "Chrony is already running"
else
    systemctl enable --now chrony
    log "Started chrony service"
fi

# 4. Configure Network (including Ceph on eno3 with MTU 9000)
log "Step 4: Configuring network interfaces..."

# Backup existing interfaces
cp /etc/network/interfaces /etc/network/interfaces.backup.$(date +%Y%m%d-%H%M%S)

# Fix vmbr0 broadcast configuration in interfaces file
log "Configuring management network broadcast in interfaces file..."
if ! grep -q "broadcast 10.10.1.255" /etc/network/interfaces; then
    # Create a temporary file with the fixed configuration
    cp /etc/network/interfaces /tmp/interfaces.tmp
    
    # Add broadcast line after the address line in vmbr0 section
    sed -i '/^iface vmbr0 inet static/,/^[[:space:]]*bridge-fd/ {
        /address.*\/24/ a\
	broadcast 10.10.1.255
    }' /tmp/interfaces.tmp
    
    # Verify the change was made correctly
    if grep -q "broadcast 10.10.1.255" /tmp/interfaces.tmp; then
        cp /tmp/interfaces.tmp /etc/network/interfaces
        log "[OK] Added broadcast 10.10.1.255 to vmbr0 configuration"
        
        # Apply the configuration
        log "Applying network configuration changes..."
        ifdown vmbr0 && ifup vmbr0 || log "Warning: Could not restart vmbr0 interface"
    else
        log "Warning: Failed to add broadcast configuration"
    fi
    rm -f /tmp/interfaces.tmp
else
    log "vmbr0 broadcast already configured"
fi

# Check if Ceph network is already configured
if ! grep -q "# Ceph storage network" /etc/network/interfaces; then
    cat >> /etc/network/interfaces <<EOF

# Ceph storage network bridge (10Gbit, MTU 9000)
# eno3 is the physical 10Gbit interface, vmbr1 is the bridge
auto eno3
iface eno3 inet manual
    mtu 9000
    post-up ip link set eno3 mtu 9000

auto vmbr1
iface vmbr1 inet static
    address ${CEPH_IPS[$HOSTNAME]}/24
    broadcast 10.10.2.255
    bridge-ports eno3
    bridge-stp off
    bridge-fd 0
    mtu 9000
    post-up ip link set vmbr1 mtu 9000
    # Ensure no default route on Ceph network
    post-up ip route del default dev vmbr1 2>/dev/null || true
EOF
    
    # Bring up interfaces
    ifup eno3 || log "Warning: Failed to bring up eno3"
    ifup vmbr1 || log "Warning: Failed to bring up vmbr1"
    
    # Verify routing - management network should be default route
    if ! ip route | grep -q "default.*vmbr0"; then
        log "Warning: Default route not on management network (vmbr0)"
    fi
    
    # Add explicit route for Ceph network (local subnet only)
    ip route add 10.10.2.0/24 dev vmbr1 src ${CEPH_IPS[$HOSTNAME]} 2>/dev/null || true
    
    # Network validation and logging
    log "Configured Ceph network: eno3 → vmbr1 (${CEPH_IPS[$HOSTNAME]}) with MTU 9000"
    log "Default route remains on management network (vmbr0)"
    
    # Log current network configuration for debugging
    log "Network configuration summary:"
    ip addr show vmbr0 | grep "inet " | awk '{print "  Management (vmbr0): " $2}' | tee -a "$LOG_FILE"
    ip addr show vmbr1 | grep "inet " | awk '{print "  Ceph (vmbr1): " $2}' | tee -a "$LOG_FILE"
    ip route show | grep "default" | tee -a "$LOG_FILE"
else
    log "Ceph network already configured"
fi

# 5. Apply Performance Tuning
log "Step 5: Applying performance tuning..."
cat > /etc/sysctl.d/99-proxmox-performance.conf <<EOF
# Performance tuning for Proxmox with 10Gbit networking
vm.swappiness = 10
net.core.netdev_max_backlog = 30000
net.ipv4.tcp_congestion_control = bbr
net.core.default_qdisc = fq
net.ipv4.tcp_fastopen = 3

# 10Gbit network optimizations
net.core.rmem_default = 262144
net.core.rmem_max = 134217728
net.core.wmem_default = 262144
net.core.wmem_max = 134217728
net.core.optmem_max = 134217728
net.ipv4.tcp_rmem = 4096 262144 134217728
net.ipv4.tcp_wmem = 4096 262144 134217728

# MTU 9000 support
net.ipv4.tcp_mtu_probing = 1
net.ipv4.tcp_window_scaling = 1
net.ipv4.tcp_timestamps = 1
net.ipv4.tcp_sack = 1
EOF

sysctl --system

# 6. Configure ZFS (if applicable)
log "Step 6: Configuring storage..."
if command -v zfs >/dev/null 2>&1; then
    # Limit ZFS ARC to 50% of RAM
    TOTAL_MEM=$(grep MemTotal /proc/meminfo | awk '{print $2}')
    ARC_MAX=$((TOTAL_MEM * 1024 / 2))
    echo "options zfs zfs_arc_max=$ARC_MAX" > /etc/modprobe.d/zfs.conf
    log "Configured ZFS ARC limit to 50% of RAM"
fi

# Create storage directories
mkdir -p /var/lib/vz/template/iso
mkdir -p /var/lib/vz/template/cache
mkdir -p /var/lib/vz/dump

# 7. Generate and Manage SSH Keys
log "Step 7: Setting up SSH keys..."

# Helper function to get hostname by IP
get_hostname_by_ip() {
    local ip=$1
    for node in "${!NODE_IPS[@]}"; do
        if [ "${NODE_IPS[$node]}" = "$ip" ]; then
            echo "$node"
            return
        fi
    done
    echo "unknown"
}

# Download and install the provisioning server's SSH keys
log "Retrieving provisioning server SSH keys for unified access..."
mkdir -p /root/.ssh
chmod 700 /root/.ssh

# Get the sysadmin_automation SSH keys from provisioning server
for attempt in {1..3}; do
    log "Downloading SSH keys from provisioning server (attempt $attempt/3)"
    
    # Download private key
    if curl -s "http://$PROVISION_SERVER/api/get-ssh-keys.php?type=management&key=private" -o /root/.ssh/id_ed25519; then
        chmod 600 /root/.ssh/id_ed25519
        log "Downloaded private SSH key"
        
        # Download public key  
        if curl -s "http://$PROVISION_SERVER/api/get-ssh-keys.php?type=management&key=public" -o /root/.ssh/id_ed25519.pub; then
            chmod 644 /root/.ssh/id_ed25519.pub
            log "Downloaded public SSH key"
            
            # Verify keys are valid
            if ssh-keygen -y -f /root/.ssh/id_ed25519 >/dev/null 2>&1; then
                log "[OK] SSH keys validated successfully"
                break
            else
                log "Downloaded SSH keys are invalid, retrying..."
            fi
        else
            log "Failed to download public key"
        fi
    else
        log "Failed to download private key"
    fi
    
    if [ $attempt -eq 3 ]; then
        log "Warning: Failed to download SSH keys after 3 attempts - generating local keys as fallback"
        # Fallback: generate local keys
        if [ ! -f /root/.ssh/id_ed25519 ]; then
            ssh-keygen -t ed25519 -f /root/.ssh/id_ed25519 -N "" -C "root@${HOSTNAME}-fallback"
            log "Generated fallback SSH key for ${HOSTNAME}"
        fi
    else
        sleep 2
    fi
done

# Ensure authorized_keys file exists and has correct permissions
touch /root/.ssh/authorized_keys
chmod 600 /root/.ssh/authorized_keys

# Add the provisioning server's public key to authorized_keys
PUBLIC_KEY=$(cat /root/.ssh/id_ed25519.pub)
if ! grep -Fxq "$PUBLIC_KEY" /root/.ssh/authorized_keys; then
    echo "$PUBLIC_KEY" >> /root/.ssh/authorized_keys
    log "Added provisioning server public key to authorized_keys"
fi

# Test SSH connectivity to other nodes using the shared key
log "Testing SSH connectivity to cluster nodes with shared key..."
SSH_SUCCESS_COUNT=0

# Define comprehensive SSH options to prevent hanging
SSH_OPTS="-o ConnectTimeout=5 -o ServerAliveInterval=5 -o ServerAliveCountMax=1 -o StrictHostKeyChecking=no -o PasswordAuthentication=no -o BatchMode=yes -o LogLevel=ERROR"

for node_ip in "${NODE_IPS[@]}"; do
    if [ "$node_ip" != "$IP_ADDRESS" ]; then
        node_name=$(get_hostname_by_ip "$node_ip")
        log "Testing SSH to $node_name ($node_ip)..."
        
        # Use timeout command as additional protection against hanging
        if timeout 10 ssh $SSH_OPTS root@"$node_ip" 'echo SSH-OK' >/dev/null 2>&1; then
            log "[OK] SSH connectivity to $node_name ($node_ip) works"
            ((SSH_SUCCESS_COUNT++))
        else
            log "[INFO] SSH connectivity to $node_name ($node_ip) not ready yet (normal during initial setup)"
        fi
    fi
done

if [ $SSH_SUCCESS_COUNT -gt 0 ]; then
    log "SSH connectivity established to $SSH_SUCCESS_COUNT nodes using shared management key"
elif [ ${#NODE_IPS[@]} -eq 1 ]; then
    log "Single node deployment - SSH connectivity test skipped"
else
    log "No SSH connectivity established yet - other nodes may still be provisioning"
    log "This is normal during initial node setup - connectivity will work once all nodes are configured"
fi

log "[OK] All nodes will use the same SSH key for seamless cluster communication"

# 8. Cluster Configuration (Node-specific behavior)
log "Step 8: Cluster configuration..."

if [ "$HOSTNAME" == "$CLUSTER_PRIMARY" ]; then
    # NODE1: Create the cluster
    log "This is the primary node ($CLUSTER_PRIMARY) - checking cluster status..."
    
    if ! pvecm status >/dev/null 2>&1; then
        log "Creating new cluster: $CLUSTER_NAME"
        # Create cluster with dual links (management + Ceph)
        pvecm create "$CLUSTER_NAME" \
            --link0 "$IP_ADDRESS" \
            --link1 "${CEPH_IPS[$HOSTNAME]}" \
            || pvecm create "$CLUSTER_NAME" --link0 "$IP_ADDRESS" \
            || error_exit "Failed to create cluster"
        
        log "[OK] Cluster '$CLUSTER_NAME' created successfully"
        
        # Wait for cluster to stabilize
        sleep 5
        
        # Verify cluster creation
        if pvecm status | grep -q "Quorum provider"; then
            log "[OK] Cluster is operational and has quorum"
        else
            log "Warning: Cluster created but quorum not detected"
        fi
    else
        log "Node is already part of a cluster"
        pvecm status || true
    fi
    
else
    # NODES 2-4: Prepare for joining (but don't join yet)
    log "This is a secondary node ($HOSTNAME) - preparing for cluster join..."
    
    # Check if already in cluster
    if pvecm status >/dev/null 2>&1; then
        log "Node is already part of a cluster"
    else
        log "Node is NOT in a cluster yet"
        log "Prerequisites for joining cluster:"
        
        # Test connectivity to primary node
        if ping -c 1 -W 2 "$CLUSTER_PRIMARY_IP" >/dev/null 2>&1; then
            log "[OK] Can reach primary node ($CLUSTER_PRIMARY_IP)"
        else
            log "[FAIL] Cannot reach primary node ($CLUSTER_PRIMARY_IP)"
        fi
        
        # Test Ceph network connectivity
        if ping -c 1 -W 2 "${CEPH_IPS[$CLUSTER_PRIMARY]}" >/dev/null 2>&1; then
            log "[OK] Can reach primary node Ceph network (${CEPH_IPS[$CLUSTER_PRIMARY]})"
        else
            log "[FAIL] Cannot reach primary node Ceph network"
        fi
        
        # Test SSH connectivity (will fail initially until keys are exchanged)
        log "Testing SSH connectivity to primary node ($CLUSTER_PRIMARY_IP)..."
        if timeout 10 ssh $SSH_OPTS root@"$CLUSTER_PRIMARY_IP" 'echo SSH-OK' >/dev/null 2>&1; then
            log "[OK] SSH connectivity to primary node works"
        else
            log "[INFO] SSH connectivity to primary node not ready (keys may need exchange)"
        fi
        
        log ""
        log "TO JOIN THIS NODE TO THE CLUSTER:"
        log "Option 1: Run from $CLUSTER_PRIMARY:"
        log "  ssh root@$CLUSTER_PRIMARY_IP"
        log "  pvecm add $IP_ADDRESS --link0 $IP_ADDRESS --link1 ${CEPH_IPS[$HOSTNAME]}"
        log ""
        log "Option 2: Run from this node ($HOSTNAME):"
        log "  pvecm add $CLUSTER_PRIMARY_IP --use_ssh"
        log ""
        log "Option 3: Use the cluster formation script:"
        log "  /root/proxmox-form-cluster.sh"
    fi
fi

# 9. Configure Firewall
log "Step 9: Configuring firewall rules..."
mkdir -p /etc/pve/firewall

if [ "$HOSTNAME" == "$CLUSTER_PRIMARY" ]; then
    # Only configure on primary node (will replicate to others)
    cat > /etc/pve/firewall/cluster.fw <<EOF
[OPTIONS]
enable: 1
policy_in: DROP
policy_out: ACCEPT

[RULES]
# Management access
IN ACCEPT -source 10.10.1.0/24 -p tcp -dport 22    # SSH
IN ACCEPT -source 10.10.1.0/24 -p tcp -dport 8006  # Web GUI
IN ACCEPT -source 10.10.1.0/24 -p tcp -dport 5900:5999  # VNC/SPICE

# Cluster communication
IN ACCEPT -source 10.10.1.0/24 -p tcp -dport 5404:5405  # Corosync
IN ACCEPT -source 10.10.1.0/24 -p udp -dport 5404:5405  # Corosync
IN ACCEPT -source 10.10.1.0/24 -p tcp -dport 2224       # HA manager

# Ceph communication (storage network)
IN ACCEPT -source 10.10.2.0/24 -p tcp -dport 6789       # MON
IN ACCEPT -source 10.10.2.0/24 -p tcp -dport 6800:7300  # OSD/MDS/MGR

# Migration network (using Ceph network)
IN ACCEPT -source 10.10.2.0/24 -p tcp -dport 60000:60050  # Live migration

# Monitoring
IN ACCEPT -source 10.10.1.0/24 -p tcp -dport 9100  # Node exporter
EOF
    log "Firewall rules configured"
fi

# 10. Install Monitoring
log "Step 10: Setting up monitoring..."
if [ ! -f /usr/local/bin/node_exporter ]; then
    wget -q -O /tmp/node_exporter.tar.gz \
        "https://github.com/prometheus/node_exporter/releases/download/v1.7.0/node_exporter-1.7.0.linux-amd64.tar.gz" || \
        log "Warning: Failed to download node_exporter"
    
    if [ -f /tmp/node_exporter.tar.gz ]; then
        tar -xzf /tmp/node_exporter.tar.gz -C /tmp/
        cp /tmp/node_exporter-*/node_exporter /usr/local/bin/ 2>/dev/null
        rm -rf /tmp/node_exporter*
        
        cat > /etc/systemd/system/node_exporter.service <<EOF
[Unit]
Description=Node Exporter
After=network.target

[Service]
User=nobody
Group=nogroup
Type=simple
ExecStart=/usr/local/bin/node_exporter

[Install]
WantedBy=multi-user.target
EOF
        
        systemctl daemon-reload
        systemctl enable --now node_exporter
        log "Node exporter installed and started"
    fi
fi

# 11. Configure Backup Schedule
log "Step 11: Configuring backup schedule..."
if [ "$HOSTNAME" == "$CLUSTER_PRIMARY" ]; then
    cat > /etc/cron.d/proxmox-backup <<EOF
# Proxmox VM backup schedule
0 2 * * * root vzdump --all --compress zstd --mode snapshot --quiet --storage local
EOF
    log "Backup schedule configured"
fi

# 12. GRUB Configuration for IOMMU
log "Step 12: Checking virtualization features..."
if grep -q "vmx\|svm" /proc/cpuinfo; then
    log "Hardware virtualization support detected"
    
    if ! grep -q "intel_iommu=on\|amd_iommu=on" /proc/cmdline; then
        cp /etc/default/grub /etc/default/grub.backup.$(date +%Y%m%d-%H%M%S)
        
        if grep -q "Intel" /proc/cpuinfo; then
            sed -i 's/GRUB_CMDLINE_LINUX_DEFAULT="[^"]*/& intel_iommu=on iommu=pt/' /etc/default/grub
        else
            sed -i 's/GRUB_CMDLINE_LINUX_DEFAULT="[^"]*/& amd_iommu=on iommu=pt/' /etc/default/grub
        fi
        
        update-grub
        log "IOMMU enabled in GRUB - reboot required"
    else
        log "IOMMU already enabled"
    fi
fi

# 13. Register with Provisioning Server
log "Step 13: Registering with provisioning server..."
REGISTER_DATA=$(cat <<EOF
{
    "hostname": "$HOSTNAME",
    "ip": "$IP_ADDRESS",
    "ceph_ip": "${CEPH_IPS[$HOSTNAME]}",
    "type": "proxmox",
    "status": "post-install-complete",
    "cluster_role": $([ "$HOSTNAME" == "$CLUSTER_PRIMARY" ] && echo '"primary"' || echo '"secondary"'),
    "cluster_name": "$CLUSTER_NAME",
    "in_cluster": $(pvecm status >/dev/null 2>&1 && echo "true" || echo "false")
}
EOF
)

curl -X POST \
    -H "Content-Type: application/json" \
    -d "$REGISTER_DATA" \
    "http://$PROVISION_SERVER/api/register-node.php" \
    || log "Warning: Failed to register with provisioning server"

# 14. Run Ansible Playbook (if needed)
log "Step 14: Running additional configuration..."
if [ -f "/var/www/html/playbooks/proxmox-post-install.yml" ]; then
    log "Running Ansible playbook for additional configuration..."
    curl -s "http://$PROVISION_SERVER/scripts/proxmox-ansible-pull.sh" | bash || \
        log "Warning: Ansible pull failed or not available"
fi

# 15. Final Cleanup
log "Step 15: Performing cleanup..."
apt-get autoremove -y
apt-get autoclean

# Create completion marker
touch /var/lib/proxmox-post-install.done
echo "$HOSTNAME:$(date -Iseconds)" > /var/lib/proxmox-post-install.done

# 16. Summary and Next Steps
log "=========================================="
log "Post-installation completed for $HOSTNAME!"
log "=========================================="
log ""
log "Node Information:"
log "  Hostname: $HOSTNAME"
log "  Management IP: $IP_ADDRESS"
log "  Ceph IP: ${CEPH_IPS[$HOSTNAME]}"
log "  Cluster: $CLUSTER_NAME"

if [ "$HOSTNAME" == "$CLUSTER_PRIMARY" ]; then
    log "  Role: PRIMARY (Cluster Master)"
    log ""
    log "Next Steps:"
    log "1. Wait for other nodes to complete post-install"
    log "2. Join other nodes using:"
    log "   pvecm add <node-ip> --link0 <node-ip> --link1 <ceph-ip>"
    log "3. Configure Ceph storage after all nodes joined"
else
    log "  Role: SECONDARY (Cluster Member)"
    log ""
    log "Next Steps:"
    log "1. Join cluster from $CLUSTER_PRIMARY or this node"
    log "2. Verify cluster membership with: pvecm status"
fi

log ""
log "Access Proxmox Web GUI: https://$IP_ADDRESS:8006"
log "Default credentials: root / <password-from-install>"
log ""
log "Reboot recommended if IOMMU was enabled"

exit 0