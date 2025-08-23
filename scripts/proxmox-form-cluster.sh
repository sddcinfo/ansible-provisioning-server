#!/bin/bash
# Proxmox Cluster Formation Script - Stage 2  
# Run this AFTER all nodes have been prepared with Stage 1
# This script forms the cluster using the Ceph network for redundancy

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

log "=== Starting Proxmox Cluster Formation ==="

# 1. Check all nodes are prepared and accessible
log "Checking node preparation status..."
PREPARED_NODES=()
FAILED_NODES=()

for node in "${!NODES[@]}"; do
    IFS=':' read -r mgmt_ip ceph_ip <<< "${NODES[$node]}"
    
    log "Checking $node ($mgmt_ip)..."
    
    # Check if node is prepared
    if timeout 15 ssh -o ConnectTimeout=10 -o StrictHostKeyChecking=no -o BatchMode=yes root@$mgmt_ip \
       'test -f /var/lib/proxmox-node-prepared.done' 2>/dev/null; then
        log "[OK] $node is prepared"
        PREPARED_NODES+=($node)
    else
        log "[FAIL] $node is NOT prepared or not accessible"
        FAILED_NODES+=($node)
    fi
    
    # Check SSH connectivity
    if timeout 10 ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=no -o BatchMode=yes root@$mgmt_ip \
       'echo "SSH OK"' >/dev/null 2>&1; then
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
    
    if ssh -o StrictHostKeyChecking=no root@$mgmt_ip 'pvecm status' >/dev/null 2>&1; then
        CLUSTER_INFO=$(ssh -o StrictHostKeyChecking=no root@$mgmt_ip 'pvecm status' 2>/dev/null || echo "")
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
    ssh -o StrictHostKeyChecking=no root@$mgmt_ip 'pvecm status' || true
    
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
    ssh -o StrictHostKeyChecking=no root@$node1_mgmt \
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
    if ! timeout 15 ssh -o StrictHostKeyChecking=no -o BatchMode=yes root@$node1_mgmt \
         "timeout 10 ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=no -o BatchMode=yes root@$mgmt_ip 'echo SSH-OK'" >/dev/null 2>&1; then
        log "Warning: SSH from node1 to $node failed - unified SSH keys should resolve this"
        # With unified SSH keys, both nodes use the same key so this should work
    fi
    
    # Join node to cluster with dual links
    if ssh -o StrictHostKeyChecking=no root@$mgmt_ip \
       "pvecm add $node1_mgmt --link0 $mgmt_ip --link1 $ceph_ip --use_ssh"; then
        log "[OK] $node successfully joined cluster"
    else
        log "[FAIL] Failed to join $node to cluster"
        
        # Try alternative method
        log "Trying alternative join method for $node..."
        if ssh -o StrictHostKeyChecking=no root@$mgmt_ip \
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

CLUSTER_STATUS=$(ssh -o StrictHostKeyChecking=no root@$node1_mgmt 'pvecm status' 2>/dev/null || echo "FAILED")

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
    RING_STATUS=$(ssh -o StrictHostKeyChecking=no root@$node1_mgmt 'corosync-cfgtool -s' 2>/dev/null || echo "Ring check failed")
    echo "$RING_STATUS"
    
    # Verify each node can see the cluster
    log "Verifying cluster visibility from each node..."
    for node in "${PREPARED_NODES[@]}"; do
        IFS=':' read -r mgmt_ip ceph_ip <<< "${NODES[$node]}"
        
        NODE_STATUS=$(ssh -o StrictHostKeyChecking=no root@$mgmt_ip 'pvecm status 2>/dev/null | grep -c "0x[0-9]"' || echo "0")
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
ssh -o StrictHostKeyChecking=no root@$node1_mgmt 'pvecm set -firewall 1' 2>/dev/null || \
    log "Warning: Could not enable cluster firewall"

# Set migration network to use Ceph network for better performance
log "Configuring migration network..."
ssh -o StrictHostKeyChecking=no root@$node1_mgmt \
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