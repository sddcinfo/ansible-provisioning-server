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
    
    # Find vmbr0 section and add broadcast after address line
    awk '
    /^iface vmbr0 inet static/ { in_vmbr0 = 1; print; next }
    in_vmbr0 && /^[[:space:]]*address.*\/24/ { print; print "	broadcast 10.10.1.255"; broadcast_added = 1; next }
    in_vmbr0 && /^iface|^auto/ && !/vmbr0/ { in_vmbr0 = 0 }
    { print }
    ' /etc/network/interfaces > /tmp/interfaces.tmp
    
    # Verify the change was made correctly
    if grep -q "broadcast 10.10.1.255" /tmp/interfaces.tmp; then
        cp /tmp/interfaces.tmp /etc/network/interfaces
        log "[OK] Added broadcast 10.10.1.255 to vmbr0 configuration"
        
        # Apply the configuration immediately
        log "Applying network configuration changes..."
        systemctl restart networking || log "Warning: Failed to restart networking service"
        
        # Verify the broadcast is actually set
        if ip addr show vmbr0 | grep -q "10.10.1.255"; then
            log "[OK] Broadcast address verified on vmbr0 interface"
        else
            log "[WARNING] Broadcast address not showing on interface, trying manual set"
            ip addr flush dev vmbr0
            ifup vmbr0 || log "Warning: Could not bring up vmbr0"
        fi
    else
        log "[ERROR] Failed to add broadcast configuration - sed command failed"
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

# 7. Set up API user for cluster management
log "Step 7: Setting up API user for cluster management..."

# Create a dedicated user for cluster formation operations
if ! pveum user list | grep -q "automation@pam"; then
    pveum user add automation@pam --comment "Automated cluster formation user"
    log "Created automation@pam user"
else
    log "automation@pam user already exists"
fi

# Create API token for the automation user
TOKEN_NAME="cluster-formation"
if pveum user token list automation@pam | grep -q "$TOKEN_NAME"; then
    log "API token $TOKEN_NAME already exists, removing old token"
    pveum user token remove automation@pam $TOKEN_NAME || true
fi

TOKEN_OUTPUT=$(pveum user token add automation@pam $TOKEN_NAME --privsep 0 2>/dev/null)
if [ $? -eq 0 ]; then
    TOKEN_ID=$(echo "$TOKEN_OUTPUT" | grep "full-tokenid" | cut -d'=' -f2 | tr -d ' ')
    TOKEN_SECRET=$(echo "$TOKEN_OUTPUT" | grep "value" | cut -d'=' -f2 | tr -d ' ')
    log "Created API token: $TOKEN_ID"
else
    log "Warning: Failed to create API token, cluster formation may use root credentials"
    TOKEN_ID=""
    TOKEN_SECRET=""
fi

# Grant necessary permissions for cluster operations
pveum acl modify / --users automation@pam --roles Administrator
log "Granted Administrator role to automation@pam user"

# Store token information for cluster formation script
cat > /etc/proxmox-cluster-token <<EOF
TOKEN_ID=$TOKEN_ID
TOKEN_SECRET=$TOKEN_SECRET
CREATED_AT=$(date -Iseconds)
HOSTNAME=$HOSTNAME
NODE_IP=$IP_ADDRESS
EOF
chmod 600 /etc/proxmox-cluster-token
log "Stored API token configuration in /etc/proxmox-cluster-token"

# Basic SSH setup for management server access only (no inter-node keys)
mkdir -p /root/.ssh
chmod 700 /root/.ssh
touch /root/.ssh/authorized_keys
chmod 600 /root/.ssh/authorized_keys

# Download management server public key for basic management access
if curl -s "http://$PROVISION_SERVER/api/get-ssh-keys.php?type=management&key=public" >> /tmp/mgmt_key 2>/dev/null; then
    if [ -s /tmp/mgmt_key ]; then
        cat /tmp/mgmt_key >> /root/.ssh/authorized_keys
        log "Added management server SSH key for basic access"
    fi
    rm -f /tmp/mgmt_key
else
    log "Warning: Could not download management server SSH key"
fi

log "[OK] API user configured - cluster formation will use Proxmox API"

# 8. Cluster Preparation (API-ready state)
log "Step 8: Preparing node for API-based cluster formation..."

# Test basic network connectivity to other nodes
log "Testing network connectivity to cluster nodes..."
for node in "${!NODE_IPS[@]}"; do
    if [ "$node" != "$HOSTNAME" ]; then
        node_ip="${NODE_IPS[$node]}"
        if ping -c 1 -W 2 "$node_ip" >/dev/null 2>&1; then
            log "[OK] Can reach $node ($node_ip)"
        else
            log "[FAIL] Cannot reach $node ($node_ip)"
        fi
    fi
done

# Test Ceph network connectivity
log "Testing Ceph network connectivity..."
for node in "${!CEPH_IPS[@]}"; do
    if [ "$node" != "$HOSTNAME" ]; then
        ceph_ip="${CEPH_IPS[$node]}"
        if ping -c 1 -W 2 "$ceph_ip" >/dev/null 2>&1; then
            log "[OK] Can reach $node Ceph network ($ceph_ip)"
        else
            log "[FAIL] Cannot reach $node Ceph network ($ceph_ip)"
        fi
    fi
done

# Check if already in cluster
if pvecm status >/dev/null 2>&1; then
    log "[OK] Node is already part of a cluster"
    pvecm status || true
else
    log "[OK] Node is ready for cluster formation via API"
    log ""
    log "TO FORM THE CLUSTER:"
    log "Run the API-based cluster formation script from the management server:"
    log "  ./scripts/proxmox-form-cluster.sh"
    log ""
    log "This will use the Proxmox API (not SSH) to:"
    log "- Create cluster on node1 if it doesn't exist"
    log "- Join all nodes using API tokens"
    log "- Verify cluster health and connectivity"
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
# Management access - internal management network
IN ACCEPT -source 10.10.1.0/24 -p tcp -dport 22    # SSH
IN ACCEPT -source 10.10.1.0/24 -p tcp -dport 8006  # Web GUI
IN ACCEPT -source 10.10.1.0/24 -p tcp -dport 5900:5999  # VNC/SPICE

# Management access - external network (for remote access)
IN ACCEPT -source 192.168.10.0/24 -p tcp -dport 22    # SSH
IN ACCEPT -source 192.168.10.0/24 -p tcp -dport 8006  # Web GUI
IN ACCEPT -source 192.168.10.0/24 -p tcp -dport 5900:5999  # VNC/SPICE

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

# Create completion markers
touch /var/lib/proxmox-post-install.done
touch /var/lib/proxmox-node-prepared.done
log "[OK] Node preparation markers created"
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