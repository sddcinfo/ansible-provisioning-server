#!/bin/bash
# Proxmox Node Preparation Script - Stage 1
# Runs during post-build to prepare each node (NO clustering operations)
# This script handles all the issues we discovered manually

set -e

# Configuration
PROVISION_SERVER="10.10.1.1"
LOG_FILE="/var/log/proxmox-prepare-node.log"
HOSTNAME=$(hostname -s)
NODE_NUM=${HOSTNAME##node}
MANAGEMENT_IP=$(ip -4 addr show vmbr0 | grep -oP '(?<=inet\s)\d+(\.\d+){3}' | head -1)

# Logging function
log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_FILE"
}

# Error handling
error_exit() {
    log "ERROR: $1"
    exit 1
}

log "=== Starting Proxmox Node Preparation for $HOSTNAME ==="

# 1. Fix Repository Configuration (addresses the enterprise repo issues)
log "Configuring Proxmox repositories..."
if [ -f /etc/apt/sources.list.d/pve-enterprise.sources ]; then
    mv /etc/apt/sources.list.d/pve-enterprise.sources /etc/apt/sources.list.d/pve-enterprise.sources.disabled
    log "Disabled enterprise PVE repository"
fi

if [ -f /etc/apt/sources.list.d/ceph.sources ]; then
    mv /etc/apt/sources.list.d/ceph.sources /etc/apt/sources.list.d/ceph.sources.disabled
    log "Disabled enterprise Ceph repository"
fi

# Legacy .list files
if [ -f /etc/apt/sources.list.d/pve-enterprise.list ]; then
    sed -i 's/^deb/#deb/' /etc/apt/sources.list.d/pve-enterprise.list
fi

# Add no-subscription repository
if ! grep -q "pve-no-subscription" /etc/apt/sources.list.d/* 2>/dev/null; then
    echo "deb http://download.proxmox.com/debian/pve trixie pve-no-subscription" > /etc/apt/sources.list.d/pve-no-subscription.list
    log "Added no-subscription repository"
fi

# 2. Update system and install packages
log "Updating package repositories..."
apt-get update || error_exit "Failed to update package repositories"

log "Installing essential packages..."
DEBIAN_FRONTEND=noninteractive apt-get install -y \
    vim \
    htop \
    iotop \
    net-tools \
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
    || error_exit "Failed to install essential packages"

# 3. Configure NTP (fixes the systemd-timesyncd issue)
log "Configuring NTP..."
timedatectl set-timezone UTC
if systemctl is-active --quiet chronyd; then
    log "Chrony is already running"
elif systemctl is-active --quiet systemd-timesyncd; then
    systemctl restart systemd-timesyncd
else
    log "Installing and starting chrony"
    apt-get install -y chrony
    systemctl enable --now chrony
fi

# 4. Configure Network Interfaces (including Ceph on eno3 with MTU 9000)
log "Configuring network interfaces..."

# Backup existing interfaces file
cp /etc/network/interfaces /etc/network/interfaces.backup.$(date +%Y%m%d-%H%M%S)

# Configure Ceph network on eno3 (10Gbit) with MTU 9000
if ! grep -q "# Ceph storage network" /etc/network/interfaces; then
    cat >> /etc/network/interfaces <<EOF

# Ceph storage network (10Gbit, MTU 9000)
auto eno3
iface eno3 inet static
    address 10.10.2.2${NODE_NUM}/24
    mtu 9000
    post-up ip link set eno3 mtu 9000

# Ceph bridge (optional, for VM access to storage network)
auto vmbr1
iface vmbr1 inet static
    address 10.10.2.10${NODE_NUM}/24
    bridge-ports none
    bridge-stp off
    bridge-fd 0
    mtu 9000
    post-up ip link set vmbr1 mtu 9000
EOF
    log "Added Ceph network configuration"
    
    # Bring up the interfaces
    ifup eno3 || log "Warning: Failed to bring up eno3"
    ifup vmbr1 || log "Warning: Failed to bring up vmbr1"
fi

# 5. Apply Performance Tuning
log "Applying performance tuning..."
cat > /etc/sysctl.d/99-proxmox-performance.conf <<EOF
# Performance tuning for Proxmox
vm.swappiness = 10
net.core.netdev_max_backlog = 5000
net.ipv4.tcp_congestion_control = bbr
net.core.default_qdisc = fq
net.ipv4.tcp_fastopen = 3
net.core.rmem_max = 134217728
net.core.wmem_max = 134217728

# Network optimizations for 10Gbit
net.core.rmem_default = 262144
net.core.rmem_max = 134217728
net.core.wmem_default = 262144
net.core.wmem_max = 134217728
net.core.optmem_max = 134217728
net.ipv4.tcp_rmem = 4096 262144 134217728
net.ipv4.tcp_wmem = 4096 262144 134217728
net.ipv4.tcp_window_scaling = 1
net.ipv4.tcp_timestamps = 1
net.ipv4.tcp_sack = 1

# High-speed network optimizations
net.core.netdev_max_backlog = 30000
net.core.netdev_budget = 600
net.ipv4.tcp_congestion_control = bbr
net.core.default_qdisc = fq

# For 9000 MTU
net.ipv4.tcp_mtu_probing = 1
EOF

sysctl --system

# 6. Configure ZFS ARC (50% of RAM)
log "Configuring ZFS ARC..."
cat > /etc/modprobe.d/zfs.conf <<EOF
# Limit ZFS ARC to 50% of RAM
options zfs zfs_arc_max=$(($(grep MemTotal /proc/meminfo | awk '{print $2}') * 1024 / 2))
EOF

# 7. Generate and manage SSH keys
log "Setting up SSH keys..."
if [ ! -f /root/.ssh/id_ed25519 ]; then
    ssh-keygen -t ed25519 -f /root/.ssh/id_ed25519 -N "" -C "root@${HOSTNAME}"
    log "Generated SSH key for ${HOSTNAME}"
fi

# Send public key to provisioning server for collection
PUBLIC_KEY=$(cat /root/.ssh/id_ed25519.pub)
SSH_KEY_DATA=$(cat <<EOF
{
    "hostname": "$HOSTNAME",
    "ip": "$MANAGEMENT_IP",
    "public_key": "$PUBLIC_KEY",
    "node_type": "proxmox"
}
EOF
)

curl -X POST \
    -H "Content-Type: application/json" \
    -d "$SSH_KEY_DATA" \
    "http://$PROVISION_SERVER/api/collect-ssh-key.php" \
    || log "Warning: Failed to send SSH key to provisioning server"

# 8. Retrieve and install all collected SSH keys
log "Retrieving SSH keys from other nodes..."
sleep 5  # Give time for key to be processed
KEYS_RESPONSE=$(curl -s "http://$PROVISION_SERVER/api/get-ssh-keys.php?type=proxmox" || echo "")

if [ -n "$KEYS_RESPONSE" ] && echo "$KEYS_RESPONSE" | jq -e .success >/dev/null 2>&1; then
    mkdir -p /root/.ssh
    echo "$KEYS_RESPONSE" | jq -r '.keys[].public_key' > /root/.ssh/authorized_keys
    chmod 600 /root/.ssh/authorized_keys
    log "Updated authorized_keys with $(echo "$KEYS_RESPONSE" | jq -r '.keys | length') keys"
fi

# 9. Configure storage directories
log "Configuring storage directories..."
mkdir -p /var/lib/vz/template/iso
mkdir -p /var/lib/vz/template/cache
mkdir -p /var/lib/vz/dump
chmod 755 /var/lib/vz/template/iso
chmod 755 /var/lib/vz/template/cache
chmod 755 /var/lib/vz/dump

# 10. Configure backup schedule (but don't start yet)
log "Configuring backup schedule..."
cat > /etc/cron.d/proxmox-backup <<EOF
# Proxmox VM backup schedule (disabled until cluster is ready)
# 0 2 * * * root vzdump --all --compress zstd --mode snapshot --quiet --storage local
EOF

# 11. GRUB configuration for IOMMU
log "Configuring GRUB for IOMMU..."
if grep -q "vmx\|svm" /proc/cpuinfo; then
    if ! grep -q "intel_iommu=on\|amd_iommu=on" /proc/cmdline; then
        # Backup GRUB config
        cp /etc/default/grub /etc/default/grub.backup.$(date +%Y%m%d-%H%M%S)
        
        # Add IOMMU parameters
        if grep -q "Intel" /proc/cpuinfo; then
            sed -i 's/GRUB_CMDLINE_LINUX_DEFAULT="[^"]*/& intel_iommu=on iommu=pt/' /etc/default/grub
        else
            sed -i 's/GRUB_CMDLINE_LINUX_DEFAULT="[^"]*/& amd_iommu=on iommu=pt/' /etc/default/grub
        fi
        
        update-grub
        log "Updated GRUB with IOMMU settings - reboot required"
    fi
fi

# 12. Install monitoring (simplified, no background downloads)
log "Installing monitoring tools..."
if command -v wget >/dev/null 2>&1; then
    wget -q -O /tmp/node_exporter.tar.gz \
        "https://github.com/prometheus/node_exporter/releases/download/v1.7.0/node_exporter-1.7.0.linux-amd64.tar.gz" || \
        log "Warning: Failed to download node_exporter"
    
    if [ -f /tmp/node_exporter.tar.gz ]; then
        tar -xzf /tmp/node_exporter.tar.gz -C /tmp/
        cp /tmp/node_exporter-*/node_exporter /usr/local/bin/ 2>/dev/null || log "Warning: node_exporter installation failed"
        rm -rf /tmp/node_exporter*
        
        # Create systemd service
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
        systemctl enable node_exporter
        log "Node exporter service configured (will start after reboot)"
    fi
fi

# 13. Register node as prepared
log "Registering node as prepared..."
REGISTER_DATA=$(cat <<EOF
{
    "hostname": "$HOSTNAME",
    "ip": "$MANAGEMENT_IP",
    "ceph_ip": "10.10.2.2${NODE_NUM}",
    "type": "proxmox",
    "status": "prepared",
    "stage": "1-complete",
    "interfaces": {
        "management": "vmbr0:$MANAGEMENT_IP",
        "ceph": "eno3:10.10.2.2${NODE_NUM}",
        "ceph_bridge": "vmbr1:10.10.2.10${NODE_NUM}"
    },
    "features": {
        "ssh_keys": true,
        "performance_tuned": true,
        "repositories_fixed": true,
        "ceph_network": true,
        "mtu_9000": true
    }
}
EOF
)

curl -X POST \
    -H "Content-Type: application/json" \
    -d "$REGISTER_DATA" \
    "http://$PROVISION_SERVER/api/register-node.php" \
    || log "Warning: Failed to register with provisioning server"

# 14. Final cleanup
log "Performing cleanup..."
apt-get autoremove -y
apt-get autoclean

# Create completion marker
touch /var/lib/proxmox-node-prepared.done
echo "prepared" > /var/lib/proxmox-node-stage

log "=== Node preparation completed successfully! ==="
log "Node $HOSTNAME is ready for Stage 2 (cluster formation)"
log "Reboot recommended to apply GRUB changes and ensure all services start properly"

exit 0