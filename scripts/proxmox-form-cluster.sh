#!/bin/bash
# Proxmox Cluster Formation Script - Stage 2  
# Run this AFTER all nodes have been prepared with Stage 1
# This script:
# - Fixes SSH authentication between all cluster nodes
# - Forms the cluster using the Ceph network for redundancy
# - Includes robust SSH connectivity testing with timeout protection
# 
# Integrated functionality from:
# - fix-cluster-ssh.sh
# - test-ssh-robust.sh
# - test-ssh-timeout.sh
# - test-timeout-protection.sh

set -e

# Configuration
PROVISION_SERVER="10.10.1.1"
CLUSTER_NAME="sddc-cluster"
LOG_FILE="/var/log/proxmox-cluster-formation.log"

# Node configuration
declare -A NODES
NODES[node1]="10.10.1.21:10.10.2.21"  # management:ceph
NODES[node2]="10.10.1.22:10.10.2.22"
NODES[node3]="10.10.1.23:10.10.2.23"  
NODES[node4]="10.10.1.24:10.10.2.24"

# Logging function
log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_FILE"
}

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

# Function to fix SSH authentication between cluster nodes
fix_cluster_ssh() {
    log "=== Fixing SSH Authentication Between Cluster Nodes ==="
    
    # Node IPs extracted from NODES array
    local NODE_IPS=()
    local NODE_NAMES=()
    
    for node in "${!NODES[@]}"; do
        IFS=':' read -r mgmt_ip ceph_ip <<< "${NODES[$node]}"
        NODE_IPS+=("$mgmt_ip")
        NODE_NAMES+=("$node")
    done
    
    # Collect all public keys
    local ALL_KEYS=""
    local MGMT_KEY="ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIPBG18KoYrX7WQA9FQGOZhLgsgpALC2TNGnWxswPJgYZ root@mgmt"
    
    # Add management server key
    ALL_KEYS="$MGMT_KEY"$'\n'
    
    log "Collecting public keys from all nodes..."
    for i in "${!NODE_IPS[@]}"; do
        local node_ip="${NODE_IPS[i]}"
        local node_name="${NODE_NAMES[i]}"
        
        log "Getting public key from $node_name ($node_ip)..."
        if node_key=$(timeout 10 ssh $SSH_OPTS root@"$node_ip" 'cat /root/.ssh/id_rsa.pub' 2>/dev/null); then
            ALL_KEYS="$ALL_KEYS$node_key"$'\n'
            log "[OK] Got key from $node_name"
        else
            log "[WARNING] Could not get key from $node_name - will try to fix"
            # Try using password authentication as fallback
            if orig_key=$(timeout 10 ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=no -o PasswordAuthentication=yes root@"$node_ip" 'cat /root/.ssh/id_rsa.pub' 2>/dev/null); then
                ALL_KEYS="$ALL_KEYS$orig_key"$'\n'
                log "[OK] Got key from $node_name using fallback method"
            else
                log "[ERROR] Could not get key from $node_name at all"
            fi
        fi
    done
    
    log "Distributing all keys to all nodes..."
    for i in "${!NODE_IPS[@]}"; do
        local node_ip="${NODE_IPS[i]}"
        local node_name="${NODE_NAMES[i]}"
        
        log "Updating authorized_keys on $node_name ($node_ip)..."
        
        # Create a temporary authorized_keys file
        local temp_keys_file="/tmp/authorized_keys_$node_name"
        echo "$ALL_KEYS" > "$temp_keys_file"
        
        # Copy to node and set up
        if timeout 15 scp -o ConnectTimeout=5 -o StrictHostKeyChecking=no -o PasswordAuthentication=no -o BatchMode=yes "$temp_keys_file" root@"$node_ip":/tmp/new_authorized_keys 2>/dev/null; then
            # Set up the keys
            timeout 15 ssh $SSH_OPTS root@"$node_ip" '
                # Backup current authorized_keys
                cp /root/.ssh/authorized_keys /root/.ssh/authorized_keys.backup 2>/dev/null || true
                
                # Install new keys
                mkdir -p /root/.ssh
                chmod 700 /root/.ssh
                mv /tmp/new_authorized_keys /root/.ssh/authorized_keys
                chmod 600 /root/.ssh/authorized_keys
                chown root:root /root/.ssh/authorized_keys
                
                # Remove symlink if it exists and create real file
                if [ -L /root/.ssh/authorized_keys ]; then
                    rm /root/.ssh/authorized_keys
                    mv /tmp/new_authorized_keys /root/.ssh/authorized_keys
                    chmod 600 /root/.ssh/authorized_keys
                fi
                
                echo "SSH keys updated successfully"
            ' 2>/dev/null && log "[OK] Updated $node_name successfully"
        else
            log "[ERROR] Could not update $node_name"
        fi
        
        # Clean up temp file
        rm -f "$temp_keys_file"
    done
    
    log "Testing SSH connectivity between all nodes..."
    for i in "${!NODE_IPS[@]}"; do
        local src_ip="${NODE_IPS[i]}"
        local src_name="${NODE_NAMES[i]}"
        
        log "Testing SSH from $src_name..."
        for j in "${!NODE_IPS[@]}"; do
            if [ $i -ne $j ]; then
                local dst_ip="${NODE_IPS[j]}"
                local dst_name="${NODE_NAMES[j]}"
                
                if timeout 10 ssh $SSH_OPTS root@"$src_ip" "ssh -o ConnectTimeout=3 -o StrictHostKeyChecking=no -o PasswordAuthentication=no -o BatchMode=yes root@$dst_ip 'echo SSH-OK'" >/dev/null 2>&1; then
                    log "[OK] $src_name → $dst_name works"
                else
                    log "[FAIL] $src_name → $dst_name failed"
                fi
            fi
        done
    done
    
    log "=== SSH Fix Complete ==="
}

log "=== Starting Proxmox Cluster Formation ==="

# 0. Fix SSH authentication between all nodes first
fix_cluster_ssh

# 1. Check all nodes are prepared and accessible
# This is where all SSH connectivity testing happens - post-install script only sets up keys
log "Checking node preparation status and SSH connectivity..."
PREPARED_NODES=()
FAILED_NODES=()

for node in "${!NODES[@]}"; do
    IFS=':' read -r mgmt_ip ceph_ip <<< "${NODES[$node]}"
    
    log "Checking $node ($mgmt_ip)..."
    
    # Check if node is prepared
    if test_ssh_robust "$mgmt_ip" "$node" 'test -f /var/lib/proxmox-node-prepared.done'; then
        log "[OK] $node is prepared"
        PREPARED_NODES+=($node)
    else
        log "[FAIL] $node is NOT prepared or not accessible"
        FAILED_NODES+=($node)
    fi
    
    # Check SSH connectivity
    if test_ssh_robust "$mgmt_ip" "$node"; then
        log "[OK] $node SSH connectivity OK"
    else
        log "[FAIL] $node SSH connectivity FAILED"
        FAILED_NODES+=($node)
    fi
    
    # Check Ceph network connectivity
    if ping -c 1 -W 2 $ceph_ip >/dev/null 2>&1; then
        log "[OK] $node Ceph network ($ceph_ip) reachable"
    else
        log "[FAIL] $node Ceph network ($ceph_ip) NOT reachable"
    fi
done

if [ ${#FAILED_NODES[@]} -gt 0 ]; then
    log "Failed nodes: ${FAILED_NODES[*]}"
    log "Please run Stage 1 preparation on failed nodes first"
    exit 1
fi

log "All ${#PREPARED_NODES[@]} nodes are prepared and ready"

# 2. Check if any nodes are already in a cluster
log "Checking existing cluster status..."
ALREADY_CLUSTERED=()

for node in "${PREPARED_NODES[@]}"; do
    IFS=':' read -r mgmt_ip ceph_ip <<< "${NODES[$node]}"
    
    if test_ssh_robust "$mgmt_ip" "$node" 'pvecm status'; then
        CLUSTER_INFO=$(timeout 10 ssh $SSH_OPTS root@$mgmt_ip 'pvecm status' 2>/dev/null || echo "")
        if echo "$CLUSTER_INFO" | grep -q "Quorum provider"; then
            log "$node is already in a cluster"
            ALREADY_CLUSTERED+=($node)
        fi
    fi
done

if [ ${#ALREADY_CLUSTERED[@]} -gt 0 ]; then
    log "Found existing cluster members: ${ALREADY_CLUSTERED[*]}"
    log "Current cluster status:"
    IFS=':' read -r mgmt_ip ceph_ip <<< "${NODES[${ALREADY_CLUSTERED[0]}]}"
    timeout 10 ssh $SSH_OPTS root@$mgmt_ip 'pvecm status' || true
    
    read -p "Do you want to continue and join remaining nodes? (y/N): " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        log "Cluster formation cancelled"
        exit 0
    fi
else
    log "No existing cluster found - will create new cluster"
fi

# 3. Create cluster on node1 (if not already exists)
IFS=':' read -r node1_mgmt node1_ceph <<< "${NODES[node1]}"

if [[ ! " ${ALREADY_CLUSTERED[@]} " =~ " node1 " ]]; then
    log "Creating cluster on node1..."
    
    # Create cluster with dual network links
    timeout 30 ssh $SSH_OPTS root@$node1_mgmt \
        "pvecm create $CLUSTER_NAME --link0 $node1_mgmt --link1 $node1_ceph" || \
        error_exit "Failed to create cluster on node1"
    
    log "[OK] Cluster '$CLUSTER_NAME' created on node1"
    sleep 5  # Allow cluster to stabilize
else
    log "node1 already in cluster, skipping creation"
fi

# 4. Join remaining nodes to cluster
log "Joining remaining nodes to cluster..."

for node in "${PREPARED_NODES[@]}"; do
    if [[ "$node" == "node1" ]] || [[ " ${ALREADY_CLUSTERED[@]} " =~ " $node " ]]; then
        log "Skipping $node (already in cluster)"
        continue
    fi
    
    IFS=':' read -r mgmt_ip ceph_ip <<< "${NODES[$node]}"
    
    log "Joining $node to cluster..."
    
    # First verify SSH connectivity from node1 to this node
    if ! test_ssh_robust "$node1_mgmt" "node1" "timeout 10 ssh $SSH_OPTS root@$mgmt_ip 'echo SSH-OK'"; then
        log "Warning: SSH from node1 to $node failed - unified SSH keys should resolve this"
        # With unified SSH keys, both nodes use the same key so this should work
    fi
    
    # Join node to cluster with dual links
    if timeout 60 ssh $SSH_OPTS root@$mgmt_ip \
       "pvecm add $node1_mgmt --link0 $mgmt_ip --link1 $ceph_ip --use_ssh"; then
        log "[OK] $node successfully joined cluster"
    else
        log "[FAIL] Failed to join $node to cluster"
        
        # Try alternative method
        log "Trying alternative join method for $node..."
        if timeout 60 ssh $SSH_OPTS root@$mgmt_ip \
           "pvecm add $node1_mgmt --use_ssh"; then
            log "[OK] $node joined cluster (single link)"
        else
            log "[FAIL] $node join failed completely"
            FAILED_NODES+=($node)
        fi
    fi
    
    # Wait for cluster to stabilize
    sleep 10
done

# 5. Verify cluster formation
log "Verifying cluster formation..."
sleep 5

CLUSTER_STATUS=$(timeout 15 ssh $SSH_OPTS root@$node1_mgmt 'pvecm status' 2>/dev/null || echo "FAILED")

if echo "$CLUSTER_STATUS" | grep -q "Quorum provider"; then
    log "[OK] Cluster is operational"
    
    # Count cluster members
    MEMBER_COUNT=$(echo "$CLUSTER_STATUS" | grep -c "0x[0-9]" || echo "0")
    log "Cluster has $MEMBER_COUNT members"
    
    # Show detailed status
    log "Cluster Status:"
    echo "$CLUSTER_STATUS"
    
    # Check corosync ring status
    log "Checking corosync rings..."
    RING_STATUS=$(timeout 15 ssh $SSH_OPTS root@$node1_mgmt 'corosync-cfgtool -s' 2>/dev/null || echo "Ring check failed")
    echo "$RING_STATUS"
    
    # Verify each node can see the cluster
    log "Verifying cluster visibility from each node..."
    for node in "${PREPARED_NODES[@]}"; do
        IFS=':' read -r mgmt_ip ceph_ip <<< "${NODES[$node]}"
        
        NODE_STATUS=$(timeout 15 ssh $SSH_OPTS root@$mgmt_ip 'pvecm status 2>/dev/null | grep -c "0x[0-9]"' || echo "0")
        if [ "$NODE_STATUS" -gt 0 ]; then
            log "[OK] $node can see cluster ($NODE_STATUS members)"
        else
            log "[FAIL] $node cannot see cluster properly"
        fi
    done
    
else
    error_exit "Cluster formation failed - no quorum detected"
fi

# 6. Configure cluster settings
log "Configuring cluster settings..."

# Enable firewall on cluster level
timeout 15 ssh $SSH_OPTS root@$node1_mgmt 'pvecm set -firewall 1' 2>/dev/null || \
    log "Warning: Could not enable cluster firewall"

# Set migration network to use Ceph network for better performance
log "Configuring migration network..."
timeout 15 ssh $SSH_OPTS root@$node1_mgmt \
    'pvecm mtunnel -migration_network 10.10.2.0/24' 2>/dev/null || \
    log "Warning: Could not set migration network"

# 7. Update node registration status
log "Updating node registration status..."
for node in "${PREPARED_NODES[@]}"; do
    IFS=':' read -r mgmt_ip ceph_ip <<< "${NODES[$node]}"
    
    REGISTER_DATA=$(cat <<EOF
{
    "hostname": "$node",
    "ip": "$mgmt_ip",
    "ceph_ip": "$ceph_ip",
    "type": "proxmox",
    "status": "clustered",
    "stage": "2-complete",
    "cluster_name": "$CLUSTER_NAME"
}
EOF
)

    curl -X POST \
        -H "Content-Type: application/json" \
        -d "$REGISTER_DATA" \
        "http://$PROVISION_SERVER/api/register-node.php" 2>/dev/null || \
        log "Warning: Could not update registration for $node"
done

# 8. Final verification and summary
log "=== Cluster Formation Complete ==="
log "Cluster Name: $CLUSTER_NAME"
log "Members: ${PREPARED_NODES[*]}"

if [ ${#FAILED_NODES[@]} -gt 0 ]; then
    log "Failed to join: ${FAILED_NODES[*]}"
    log "You may need to join these nodes manually"
fi

log "Next steps:"
log "1. Access Proxmox web interface at https://$node1_mgmt:8006"
log "2. Configure storage (local, Ceph, etc.)"
log "3. Create VMs and containers"
log "4. Set up monitoring and backups"

log "Cluster formation completed successfully!"
exit 0