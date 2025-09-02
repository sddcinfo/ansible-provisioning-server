#!/bin/bash
# Proxmox 9 Post-Installation Script - Optimized Version
# Runs on first boot after Proxmox installation
# Fully self-contained - no Ansible dependencies
# Supports node1 as primary and nodes 2-4 as cluster members

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

# Retry function with exponential backoff
retry_with_backoff() {
    local max_attempts=$1
    local delay=$2
    local max_delay=$3
    shift 3
    local command="$@"
    local attempt=1
    
    while [ $attempt -le $max_attempts ]; do
        log "Attempt $attempt/$max_attempts: $command"
        if eval "$command"; then
            log "Command succeeded on attempt $attempt"
            return 0
        else
            if [ $attempt -eq $max_attempts ]; then
                log "ERROR: Command failed after $max_attempts attempts: $command"
                return 1
            fi
            log "Command failed on attempt $attempt, retrying in ${delay}s..."
            sleep $delay
            # Exponential backoff with max delay
            delay=$((delay * 2))
            [ $delay -gt $max_delay ] && delay=$max_delay
            attempt=$((attempt + 1))
        fi
    done
    return 1
}

# Validate Proxmox version
validate_proxmox_version() {
    local pve_version=$(pveversion | grep -oP 'pve-manager/\K[0-9]+' || echo "0")
    if [ "$pve_version" -ne 9 ]; then
        error_exit "This script requires Proxmox 9. Found version: $pve_version"
    fi
    log "Proxmox version $pve_version validated"
}

# Check network connectivity
check_network_connectivity() {
    log "Checking network connectivity..."
    
    # Check DNS resolution
    if ! host download.proxmox.com >/dev/null 2>&1; then
        log "Warning: DNS resolution not working, trying to fix..."
        echo "nameserver 8.8.8.8" >> /etc/resolv.conf
        echo "nameserver 1.1.1.1" >> /etc/resolv.conf
        
        if ! host download.proxmox.com >/dev/null 2>&1; then
            log "ERROR: DNS resolution still not working"
            return 1
        fi
    fi
    
    # Check internet connectivity with retry
    for i in 1 2 3; do
        if ping -c 1 -W 5 8.8.8.8 >/dev/null 2>&1; then
            log "Network connectivity verified"
            return 0
        fi
        log "Network check attempt $i/3 failed, waiting..."
        sleep 5
    done
    
    log "Warning: Network connectivity issues detected"
    return 1
}

log "=========================================="
log "Starting Proxmox 9 post-installation for $HOSTNAME"
log "IP: $IP_ADDRESS, Node Number: $NODE_NUM"
log "=========================================="

# Validate we're running on Proxmox 9
validate_proxmox_version

# Check network connectivity early
if ! check_network_connectivity; then
    log "Warning: Network issues detected - script will attempt to continue with retries"
fi

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

# Retry apt-get update with backoff (5 attempts, starting at 5s delay, max 60s)
if ! retry_with_backoff 5 5 60 "apt-get update"; then
    error_exit "Failed to update package repositories after multiple attempts"
fi

# Define package installation function
install_packages() {
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
        libguestfs-tools \
        pve-edk2-firmware \
        proxmox-backup-client
}

# Try to install packages with retry logic
log "Installing essential packages (with retry on failure)..."
if ! retry_with_backoff 5 10 120 "install_packages"; then
    log "ERROR: Package installation failed after multiple attempts"
    log "Attempting to fix potential dpkg issues..."
    
    # Try to fix common dpkg issues
    dpkg --configure -a
    apt-get install -f -y
    
    # Try one more time after cleanup
    log "Retrying package installation after cleanup..."
    if ! retry_with_backoff 3 5 30 "install_packages"; then
        error_exit "Failed to install essential packages after recovery attempts"
    fi
fi

log "Successfully installed all essential packages"

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
    awk ' \
    /^iface vmbr0 inet static/ { in_vmbr0 = 1; print; next } \
    in_vmbr0 && /^[[:space:]]*address.*\/24/ { print; print "\tbroadcast 10.10.1.255"; broadcast_added = 1; next } \
    in_vmbr0 && /^iface|^auto/ && !/vmbr0/ { in_vmbr0 = 0 } \
    { print } \
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
    log "Configured Ceph network: eno3 -> vmbr1 (${CEPH_IPS[$HOSTNAME]}) with MTU 9000"
    log "Default route remains on management network (vmbr0)"
    
    # Log current network configuration for debugging
    log "Network configuration summary:"
    ip addr show vmbr0 | grep "inet " | awk '{print "  Management (vmbr0): " $2}' | tee -a "$LOG_FILE"
    ip addr show vmbr1 | grep "inet " | awk '{print "  Ceph (vmbr1): " $2}' | tee -a "$LOG_FILE"
    ip route show | grep "default" | tee -a "$LOG_FILE"
else
    log "Ceph network already configured"
fi

# Configure RSS/RPS for 10Gbit optimization
log "Configuring RSS/RPS for 10Gbit network optimization..."
for iface in eno3 vmbr1; do
    if [ -d /sys/class/net/$iface ]; then
        # Get number of CPUs
        CPUS=$(nproc)
        # Set RPS to use all CPUs
        echo $(printf '%x' $((2**CPUS-1))) > /sys/class/net/$iface/queues/rx-0/rps_cpus 2>/dev/null || true
        log "Configured RPS for $iface"
    fi
done

# 5. Apply Performance Tuning
log "Step 5: Applying performance tuning..."
cat > /etc/sysctl.d/99-proxmox-performance.conf <<EOF
# Performance tuning for Proxmox 9 with 10Gbit networking
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

# Additional optimizations for virtualization
kernel.pid_max = 4194304
fs.aio-max-nr = 1048576
EOF

sysctl --system

# Set CPU governor to performance
log "Setting CPU governor to performance mode..."
for gov in /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor; do
    echo "performance" > "$gov" 2>/dev/null || true
done

# NVMe optimization if present
if lsblk | grep -q nvme; then
    log "Optimizing NVMe storage..."
    for nvme in /sys/block/nvme*; do
        if [ -d "$nvme" ]; then
            echo "none" > "$nvme/queue/scheduler" 2>/dev/null || true
            echo "1024" > "$nvme/queue/nr_requests" 2>/dev/null || true
        fi
    done
fi

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

# 7. Prepare for Cluster Formation
log "Step 7: Preparing node for API-based cluster formation..."

# Check if already in cluster
if pvecm status >/dev/null 2>&1; then
    log "[OK] Node is already part of a cluster"
    pvecm status || true
else
    log "[OK] Node is ready for cluster formation via API"
    log ""
    log "TO FORM THE CLUSTER:"
    log "Run the API-based cluster formation script from the management server:"
    log "  ./scripts/proxmox-form-cluster.py"
    log ""
    log "This will use the Proxmox API (not SSH) to create the cluster."
fi

# 8. Configure Firewall and Security
log "Step 8: Configuring firewall rules and security..."
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

# Proxmox Backup Server
IN ACCEPT -source 10.10.1.0/24 -p tcp -dport 8007  # PBS API
EOF
    log "Firewall rules configured"
fi

# fail2ban removed - can interfere with cluster communication

# 9. Install Monitoring
log "Step 9: Setting up monitoring (optional)..."
if [ ! -f /usr/local/bin/node_exporter ]; then
    # Check if GitHub is reachable first
    if ! wget --timeout=5 --tries=1 --spider https://github.com 2>/dev/null; then
        log "Warning: GitHub is not reachable, skipping node_exporter installation"
    else
        # Function to download node_exporter
        download_node_exporter() {
            wget --timeout=30 --tries=1 -O /tmp/node_exporter.tar.gz \
                "https://github.com/prometheus/node_exporter/releases/download/v1.7.0/node_exporter-1.7.0.linux-amd64.tar.gz"
        }
        
        # Try downloading with retry logic (but with shorter retries)
        if retry_with_backoff 2 5 15 "download_node_exporter"; then
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
        else
            log "Warning: Node exporter download failed after retries - monitoring will not be available"
        fi
    fi
else
    log "Node exporter already installed"
fi

# 10. Configure Backup Schedule
log "Step 10: Configuring backup schedule..."
if [ "$HOSTNAME" == "$CLUSTER_PRIMARY" ]; then
    cat > /etc/cron.d/proxmox-backup <<EOF
# Proxmox VM backup schedule
0 2 * * * root vzdump --all --compress zstd --mode snapshot --quiet --storage local
EOF
    log "Backup schedule configured"
fi

# 11. Download VM Templates (optional but useful)
log "Step 11: Downloading common VM templates..."
if [ "$HOSTNAME" == "$CLUSTER_PRIMARY" ]; then
    # Download templates in background to not block script
    (
        cd /var/lib/vz/template/iso/ 
        
        # Function to download with retry
        download_with_retry() {
            local url=$1
            local output=$2
            local description=$3
            local attempts=3
            local delay=5
            
            for i in $(seq 1 $attempts); do
                if wget --timeout=30 --tries=1 -q -O "$output.tmp" "$url"; then
                    mv "$output.tmp" "$output"
                    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [$HOSTNAME] Successfully downloaded $description" >> "$LOG_FILE"
                    return 0
                else
                    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [$HOSTNAME] Attempt $i/$attempts failed for $description" >> "$LOG_FILE"
                    rm -f "$output.tmp"
                    [ $i -lt $attempts ] && sleep $delay
                fi
            done
            echo "[$(date '+%Y-%m-%d %H:%M:%S')] [$HOSTNAME] Failed to download $description after $attempts attempts" >> "$LOG_FILE"
            return 1
        }
        
        # Ubuntu 24.04 cloud image
        if [ ! -f ubuntu-24.04-cloudimg.img ]; then
            download_with_retry \
                "https://cloud-images.ubuntu.com/noble/current/noble-server-cloudimg-amd64.img" \
                "ubuntu-24.04-cloudimg.img" \
                "Ubuntu 24.04 cloud image"
        fi
        
        # Debian 12 cloud image
        if [ ! -f debian-12-cloudimg.qcow2 ]; then
            download_with_retry \
                "https://cloud.debian.org/images/cloud/bookworm/latest/debian-12-generic-amd64.qcow2" \
                "debian-12-cloudimg.qcow2" \
                "Debian 12 cloud image"
        fi
    ) &
    log "VM template downloads started in background with retry logic"
fi

# 12. GRUB Configuration for IOMMU
log "Step 12: Checking virtualization features..."
if grep -q "vmx\|svm" /proc/cpuinfo; then
    log "Hardware virtualization support detected"
    
    if ! grep -q "intel_iommu=on\|amd_iommu=on" /proc/cmdline; then
        cp /etc/default/grub /etc/default/grub.backup.$(date +%Y%m%d-%H%M%S)
        
        if grep -q "Intel" /proc/cpuinfo;
        then
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
    "in_cluster": $(pvecm status >/dev/null 2>&1 && echo "true" || echo "false"),
    "pve_version": "9"
}
EOF
)

# Function to register with provisioning server
register_node() {
    curl -X POST \
        -H "Content-Type: application/json" \
        -d "$REGISTER_DATA" \
        --connect-timeout 10 \
        --max-time 30 \
        "http://$PROVISION_SERVER/api/register-node.php"
}

# Try registration with retry logic
if retry_with_backoff 3 5 20 "register_node"; then
    log "Successfully registered with provisioning server"
else
    log "Warning: Failed to register with provisioning server after retries - continuing anyway"
fi

# 14. Final Cleanup
log "Step 14: Performing cleanup..."
apt-get autoremove -y
apt-get autoclean

# Create completion markers
touch /var/lib/proxmox-post-install.done
touch /var/lib/proxmox-node-prepared.done
log "[OK] Node preparation markers created"
echo "$HOSTNAME:$(date -Iseconds):pve9" > /var/lib/proxmox-post-install.done

# 15. Summary and Next Steps
log "Step 15: Summary and Next Steps..."
log "=========================================="
log "Post-installation completed for $HOSTNAME!"
log "=========================================="
log ""
log "Node Information:"
log "  Hostname: $HOSTNAME"
log "  Management IP: $IP_ADDRESS"
log "  Ceph IP: ${CEPH_IPS[$HOSTNAME]}"
log "  Cluster: $CLUSTER_NAME"
log "  Proxmox Version: 9"

if [ "$HOSTNAME" == "$CLUSTER_PRIMARY" ]; then
    log "  Role: PRIMARY (Cluster Master)"
    log ""
    log "Next Steps:"
    log "1. Wait for other nodes to complete post-install"
    log "2. Run cluster formation script from management server:"
    log "   ./scripts/proxmox-form-cluster.py"
    log "3. Configure Ceph storage after all nodes joined"
else
    log "  Role: SECONDARY (Cluster Member)"
    log ""
    log "Next Steps:"
    log "1. Wait for primary node to complete post-install"
    log "2. Cluster will be formed via API from management server"
    log "3. Verify cluster membership with: pvecm status"
fi

log ""
log "Access Proxmox Web GUI: https://$IP_ADDRESS:8006"
log "Default credentials: root / <password-from-install>"
log ""

if grep -q "intel_iommu=on\|amd_iommu=on" /etc/default/grub && ! grep -q "intel_iommu=on\|amd_iommu=on" /proc/cmdline; then
    log "WARNING: REBOOT REQUIRED for IOMMU changes to take effect"
fi

log "SUCCESS: Post-installation script completed successfully!"

exit 0
