#!/bin/bash
# Proxmox Node Join Cluster Script
# This script joins nodes 2-4 to the existing cluster

set -e

# Configuration
PROVISION_SERVER="10.10.1.1"
CLUSTER_NODE1_IP="10.10.1.21"
LOG_FILE="/var/log/proxmox-join-cluster.log"
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

# Define comprehensive SSH options to prevent hanging
SSH_OPTS="-o ConnectTimeout=5 -o ServerAliveInterval=5 -o ServerAliveCountMax=1 -o StrictHostKeyChecking=no -o PasswordAuthentication=no -o BatchMode=yes -o LogLevel=ERROR"

# Function to test SSH with multiple layers of timeout protection
test_ssh_robust() {
    local target_ip="$1"
    local node_name="$2"
    local command="${3:-echo SSH-OK}"
    
    local ssh_pid
    local result=1
    
    # Run SSH in background and capture PID
    (
        exec timeout 10 ssh $SSH_OPTS root@"$target_ip" "$command" >/dev/null 2>&1
    ) &
    ssh_pid=$!
    
    # Wait up to 12 seconds for the process to complete
    local count=0
    while [ $count -lt 12 ]; do
        if ! kill -0 "$ssh_pid" 2>/dev/null; then
            # Process has completed
            wait "$ssh_pid"
            result=$?
            break
        fi
        sleep 1
        ((count++))
    done
    
    # Force kill if still running after 12 seconds
    if kill -0 "$ssh_pid" 2>/dev/null; then
        log "[WARNING] SSH to $node_name ($target_ip) exceeded timeout, force killing process"
        kill -KILL "$ssh_pid" 2>/dev/null
        wait "$ssh_pid" 2>/dev/null
        result=124  # timeout exit code
    fi
    
    return $result
}

log "Starting Proxmox cluster join process for $HOSTNAME"

# 1. Check if already in cluster
if pvecm status >/dev/null 2>&1; then
    log "Node $HOSTNAME is already part of a cluster"
    exit 0
fi

# 2. Basic configuration (similar to post-install but simplified)
log "Applying basic configuration..."

# Configure repositories
if [ -f /etc/apt/sources.list.d/pve-enterprise.list ]; then
    sed -i 's/^deb/#deb/' /etc/apt/sources.list.d/pve-enterprise.list
fi

if ! grep -q "pve-no-subscription" /etc/apt/sources.list.d/* 2>/dev/null; then
    # Use trixie as default since that's what Proxmox 9 uses
    echo "deb http://download.proxmox.com/debian/pve trixie pve-no-subscription" > /etc/apt/sources.list.d/pve-no-subscription.list
fi

# Update and install essential packages
apt-get update
apt-get install -y \
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
    zfsutils-linux

# 3. Configure network optimization
log "Applying network optimizations..."
cat > /etc/sysctl.d/99-proxmox.conf <<EOF
vm.swappiness = 10
net.core.netdev_max_backlog = 5000
net.ipv4.tcp_congestion_control = bbr
net.core.default_qdisc = fq
net.ipv4.tcp_fastopen = 3
net.core.rmem_max = 134217728
net.core.wmem_max = 134217728
EOF
sysctl --system

# 4. Create Ceph network bridge
log "Creating Ceph network bridge..."
NODE_NUM=${HOSTNAME##node}
if ! ip link show vmbr1 &>/dev/null; then
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

# 5. Install Ceph packages
log "Installing Ceph packages..."
apt-get install -y ceph-common || log "Ceph installation failed"

# 6. Wait for node1 to be ready
log "Checking cluster readiness..."
for i in {1..30}; do
    if ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=no root@$CLUSTER_NODE1_IP 'pvecm status' >/dev/null 2>&1; then
        log "Cluster node1 is ready"
        break
    fi
    log "Waiting for cluster node1 to be ready... ($i/30)"
    sleep 10
done

# 7. Join the cluster
log "Joining cluster..."
if ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=no root@$CLUSTER_NODE1_IP 'pvecm status' >/dev/null 2>&1; then
    pvecm add $CLUSTER_NODE1_IP --link0 $IP_ADDRESS || error_exit "Failed to join cluster"
    log "Successfully joined cluster"
else
    error_exit "Cannot connect to cluster node1"
fi

# 8. Verify cluster membership
log "Verifying cluster membership..."
sleep 5
if pvecm status >/dev/null 2>&1; then
    log "Cluster join successful - node is now part of the cluster"
    pvecm status
else
    error_exit "Cluster join verification failed"
fi

# 9. Register with provisioning server
log "Registering with provisioning server..."
REGISTER_DATA=$(cat <<EOF
{
    "hostname": "$HOSTNAME",
    "ip": "$IP_ADDRESS",
    "type": "proxmox",
    "status": "cluster-joined"
}
EOF
)

curl -X POST \
    -H "Content-Type: application/json" \
    -d "$REGISTER_DATA" \
    "http://$PROVISION_SERVER/api/register-node.php" \
    || log "Failed to register with provisioning server"

# 10. Final cleanup
log "Performing cleanup..."
apt-get autoremove -y
apt-get autoclean

# Create completion marker
touch /var/lib/proxmox-cluster-join.done

log "Cluster join process completed successfully!"
exit 0