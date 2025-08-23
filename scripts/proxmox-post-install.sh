#!/bin/bash
# Proxmox Post-Installation Script
# This script runs on first boot after Proxmox installation
# It configures the system and registers with the provisioning server

set -e

# Configuration
PROVISION_SERVER="10.10.1.1"
LOG_FILE="/var/log/proxmox-post-install.log"
HOSTNAME=$(hostname -s)
IP_ADDRESS=$(ip -4 addr show vmbr0 | grep -oP '(?<=inet\s)\d+(\.\d+){3}' | head -1)

# Logging function
log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_FILE"
}

# Error handling
error_exit() {
    log "ERROR: $1"
    exit 1
}

log "Starting Proxmox post-installation configuration for $HOSTNAME"

# 1. Update system packages
log "Updating package repositories..."
apt-get update || error_exit "Failed to update package repositories"

# 2. Configure Proxmox repository (community edition)
log "Configuring Proxmox repositories..."
if [ -f /etc/apt/sources.list.d/pve-enterprise.list ]; then
    # Comment out enterprise repository if no subscription
    sed -i 's/^deb/#deb/' /etc/apt/sources.list.d/pve-enterprise.list
fi

# Add no-subscription repository if not present
if ! grep -q "pve-no-subscription" /etc/apt/sources.list.d/* 2>/dev/null; then
    echo "deb http://download.proxmox.com/debian/pve trixie pve-no-subscription" > /etc/apt/sources.list.d/pve-no-subscription.list
fi

# 3. Install essential packages
log "Installing essential packages..."
apt-get update
apt-get install -y \
    curl \
    wget \
    vim \
    htop \
    iotop \
    net-tools \
    python3 \
    python3-pip \
    python3-requests \
    jq \
    || error_exit "Failed to install essential packages"

# 4. Configure NTP
log "Configuring NTP..."
timedatectl set-timezone UTC
# Use chrony instead of systemd-timesyncd (which may not be available)
if systemctl is-active --quiet chronyd; then
    log "Chrony is already running"
elif systemctl is-active --quiet systemd-timesyncd; then
    systemctl restart systemd-timesyncd
else
    log "No NTP service found - installing chrony"
    apt-get install -y chrony
    systemctl enable --now chrony
fi

# 5. Configure network optimization
log "Applying network optimizations..."
cat > /etc/sysctl.d/99-proxmox-network.conf <<EOF
# Network optimizations for Proxmox
net.core.netdev_max_backlog = 5000
net.ipv4.tcp_congestion_control = bbr
net.core.default_qdisc = fq
net.ipv4.tcp_mtu_probing = 1
net.ipv4.tcp_fastopen = 3
net.core.rmem_max = 134217728
net.core.wmem_max = 134217728
net.ipv4.tcp_rmem = 4096 87380 134217728
net.ipv4.tcp_wmem = 4096 65536 134217728
EOF
sysctl -p /etc/sysctl.d/99-proxmox-network.conf

# 6. Configure storage
log "Configuring storage pools..."
# Create additional ZFS pools if needed
# This is a placeholder - adjust based on your hardware
if ! zfs list | grep -q "rpool/data"; then
    log "Default ZFS pool already configured"
fi

# 7. Configure firewall rules
log "Configuring firewall..."
cat > /etc/pve/firewall/cluster.fw <<EOF
[OPTIONS]
enable: 1
policy_in: DROP
policy_out: ACCEPT

[RULES]
# Allow SSH from management network
IN ACCEPT -source 10.10.1.0/24 -p tcp -dport 22
# Allow Proxmox Web GUI from management network
IN ACCEPT -source 10.10.1.0/24 -p tcp -dport 8006
# Allow VNC/SPICE from management network
IN ACCEPT -source 10.10.1.0/24 -p tcp -dport 5900:5999
# Allow cluster communication
IN ACCEPT -source 10.10.1.0/24 -p tcp -dport 5404:5405
IN ACCEPT -source 10.10.1.0/24 -p udp -dport 5404:5405
# Allow Ceph communication if needed
IN ACCEPT -source 10.10.2.0/24 -p tcp -dport 6789
IN ACCEPT -source 10.10.2.0/24 -p tcp -dport 6800:7300
EOF

# 8. Create cluster if this is the first node
if [ "$HOSTNAME" == "node1" ]; then
    log "Creating Proxmox cluster..."
    if ! pvecm status >/dev/null 2>&1; then
        pvecm create sddc-cluster --link0 "$IP_ADDRESS" || log "Failed to create cluster"
    else
        log "Node is already part of a cluster"
    fi
else
    log "This node ($HOSTNAME) will need to join the cluster manually"
    # For other nodes, we'll need to join the cluster
    # This requires the cluster join information from node1
fi

# 9. Configure backup schedule
log "Configuring backup schedule..."
cat > /etc/cron.d/proxmox-backup <<EOF
# Proxmox VM backup schedule
0 2 * * * root vzdump --all --compress zstd --mode snapshot --quiet --storage local --mailto admin@sddc.info
EOF

# 10. Performance tuning
log "Applying performance tuning..."
# Enable Intel VT-d or AMD-Vi if available
if grep -q "vmx\|svm" /proc/cpuinfo; then
    log "Hardware virtualization support detected"
    # Enable IOMMU if not already enabled
    if ! grep -q "intel_iommu=on" /proc/cmdline && ! grep -q "amd_iommu=on" /proc/cmdline; then
        log "IOMMU not enabled - will require reboot after enabling in GRUB"
    fi
fi

# 11. Register with provisioning server
log "Registering with provisioning server..."
REGISTER_DATA=$(cat <<EOF
{
    "hostname": "$HOSTNAME",
    "ip": "$IP_ADDRESS",
    "type": "proxmox",
    "status": "ready"
}
EOF
)

curl -X POST \
    -H "Content-Type: application/json" \
    -d "$REGISTER_DATA" \
    "http://$PROVISION_SERVER/api/register-node.php" \
    || log "Failed to register with provisioning server"

# 12. Configure monitoring
log "Setting up monitoring..."
# Install and configure node exporter for Prometheus
wget -q https://github.com/prometheus/node_exporter/releases/latest/download/node_exporter-*linux-amd64.tar.gz -O /tmp/node_exporter.tar.gz
tar -xzf /tmp/node_exporter.tar.gz -C /tmp/
cp /tmp/node_exporter-*/node_exporter /usr/local/bin/
rm -rf /tmp/node_exporter*

# Create systemd service for node exporter
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

# 13. Configure SSH for cluster communication
log "Configuring SSH for cluster communication..."
# Generate SSH key for root if not exists
if [ ! -f /root/.ssh/id_ed25519 ]; then
    ssh-keygen -t ed25519 -f /root/.ssh/id_ed25519 -N ""
fi

# 14. Setup Ceph preparation (if needed)
log "Preparing for Ceph installation..."
apt-get install -y ceph-common || log "Ceph common packages installation failed"

# Create network bridge for Ceph if not exists
if ! ip link show vmbr1 &>/dev/null; then
    log "Creating Ceph network bridge (vmbr1)..."
    NODE_NUM=${HOSTNAME##node}
    cat >> /etc/network/interfaces <<EOF

# Ceph storage network
auto vmbr1
iface vmbr1 inet static
    address 10.10.2.2${NODE_NUM}/24
    bridge-ports none
    bridge-stp off
    bridge-fd 0
EOF
    ifup vmbr1 || log "Failed to bring up vmbr1 interface"
else
    log "Ceph network bridge (vmbr1) already exists"
fi

# 15. Download and run Ansible pull for additional configuration
log "Running Ansible pull for additional configuration..."
curl -s "http://$PROVISION_SERVER/scripts/proxmox-ansible-pull.sh" | bash || log "Ansible pull failed"

# 16. Final cleanup
log "Performing cleanup..."
apt-get autoremove -y
apt-get autoclean

# 17. Send completion notification
COMPLETION_DATA=$(cat <<EOF
{
    "hostname": "$HOSTNAME",
    "ip": "$IP_ADDRESS",
    "status": "post-install-complete",
    "timestamp": "$(date -Iseconds)"
}
EOF
)

curl -X POST \
    -H "Content-Type: application/json" \
    -d "$COMPLETION_DATA" \
    "http://$PROVISION_SERVER/api/node-status.php" \
    || log "Failed to send completion notification"

log "Post-installation configuration completed successfully!"

# Create a marker file to indicate post-install has run
touch /var/lib/proxmox-post-install.done

# If this is not node1, provide cluster join information
if [ "$HOSTNAME" != "node1" ]; then
    log "To join this node to the cluster, run on node1:"
    log "pvecm add $IP_ADDRESS"
fi

exit 0