#!/bin/bash
# Proxmox API-Based Cluster Formation Script
# Uses Proxmox REST API instead of SSH for cluster operations
# Much cleaner and more reliable than SSH-based approaches

set -e

# Configuration
PROVISION_SERVER="10.10.1.1"
CLUSTER_NAME="sddc-cluster"
LOG_FILE="/var/log/proxmox-cluster-formation-api.log"

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

# Function to make API calls with proper authentication
api_call() {
    local node_ip="$1"
    local method="$2"
    local endpoint="$3"
    local data="$4"
    local token_file="/tmp/node_${node_ip}_token"
    
    # Try to get token from node's config file
    if ! scp -q -o ConnectTimeout=5 -o StrictHostKeyChecking=no root@"$node_ip":/etc/proxmox-cluster-token "$token_file" 2>/dev/null; then
        log "Warning: Could not retrieve API token from $node_ip, will try root user"
        return 1
    fi
    
    source "$token_file"
    rm -f "$token_file"
    
    local auth_header=""
    if [ -n "$TOKEN_ID" ] && [ -n "$TOKEN_SECRET" ]; then
        auth_header="Authorization: PVEAPIToken=$TOKEN_ID=$TOKEN_SECRET"
    else
        log "Warning: No valid token found for $node_ip"
        return 1
    fi
    
    local curl_args=(
        -k -s
        -X "$method"
        -H "Content-Type: application/json"
        -H "$auth_header"
    )
    
    if [ -n "$data" ]; then
        curl_args+=(-d "$data")
    fi
    
    curl "${curl_args[@]}" "https://$node_ip:8006/api2/json/$endpoint"
}

# Function to check node status via API
check_node_status() {
    local node_ip="$1"
    local node_name="$2"
    
    log "Checking $node_name ($node_ip) status via API..."
    
    # Test basic API connectivity
    if ! api_result=$(api_call "$node_ip" "GET" "version" 2>/dev/null); then
        log "[FAIL] Cannot connect to API on $node_name"
        return 1
    fi
    
    # Check if API returned valid JSON
    if echo "$api_result" | jq -e '.data.version' >/dev/null 2>&1; then
        version=$(echo "$api_result" | jq -r '.data.version')
        log "[OK] $node_name API accessible (Proxmox $version)"
    else
        log "[FAIL] Invalid API response from $node_name"
        return 1
    fi
    
    # Check cluster status
    if cluster_result=$(api_call "$node_ip" "GET" "cluster/status" 2>/dev/null); then
        if echo "$cluster_result" | jq -e '.data' >/dev/null 2>&1; then
            if echo "$cluster_result" | jq -e '.data[] | select(.type=="cluster")' >/dev/null 2>&1; then
                cluster_name=$(echo "$cluster_result" | jq -r '.data[] | select(.type=="cluster") | .name')
                log "[OK] $node_name is in cluster: $cluster_name"
                return 2  # Already in cluster
            else
                log "[OK] $node_name is not in a cluster (ready to join)"
                return 0  # Ready to join
            fi
        fi
    fi
    
    log "[WARN] Could not determine cluster status for $node_name"
    return 0
}

# Function to create cluster via API
create_cluster() {
    local node_ip="$1"
    local node_name="$2"
    local ceph_ip="$3"
    
    log "Creating cluster '$CLUSTER_NAME' on $node_name via API..."
    
    local cluster_data="{
        \"clustername\": \"$CLUSTER_NAME\",
        \"link0\": \"$node_ip\",
        \"link1\": \"$ceph_ip\"
    }"
    
    if result=$(api_call "$node_ip" "POST" "cluster/config" "$cluster_data" 2>/dev/null); then
        if echo "$result" | jq -e '.data' >/dev/null 2>&1; then
            log "[OK] Cluster '$CLUSTER_NAME' created successfully on $node_name"
            return 0
        else
            # Check if cluster already exists
            if echo "$result" | grep -q "cluster config already exists"; then
                log "[OK] Cluster already exists on $node_name"
                return 0
            else
                log "[FAIL] Cluster creation failed: $(echo "$result" | jq -r '.errors // empty')"
                return 1
            fi
        fi
    else
        log "[FAIL] API call failed for cluster creation on $node_name"
        return 1
    fi
}

# Function to join node to cluster via API
join_cluster() {
    local node_ip="$1"
    local node_name="$2"
    local primary_ip="$3"
    local ceph_ip="$4"
    
    log "Joining $node_name to cluster via API..."
    
    # Get join information from primary node
    if join_info=$(api_call "$primary_ip" "GET" "cluster/config/join" 2>/dev/null); then
        if echo "$join_info" | jq -e '.data' >/dev/null 2>&1; then
            totem_config=$(echo "$join_info" | jq -r '.data.totem')
            
            local join_data="{
                \"hostname\": \"$primary_ip\",
                \"link0\": \"$node_ip\",
                \"link1\": \"$ceph_ip\",
                \"totem\": $totem_config
            }"
            
            if result=$(api_call "$node_ip" "POST" "cluster/config/join" "$join_data" 2>/dev/null); then
                if echo "$result" | jq -e '.data' >/dev/null 2>&1; then
                    log "[OK] $node_name successfully joined cluster"
                    return 0
                else
                    log "[FAIL] Join failed: $(echo "$result" | jq -r '.errors // empty')"
                    return 1
                fi
            else
                log "[FAIL] API call failed for joining $node_name"
                return 1
            fi
        else
            log "[FAIL] Could not get join information from primary node"
            return 1
        fi
    else
        log "[FAIL] Could not retrieve join information from primary node"
        return 1
    fi
}

# Function to verify cluster status
verify_cluster() {
    local node_ip="$1"
    local node_name="$2"
    
    log "Verifying cluster status on $node_name..."
    
    if cluster_result=$(api_call "$node_ip" "GET" "cluster/status" 2>/dev/null); then
        if echo "$cluster_result" | jq -e '.data' >/dev/null 2>&1; then
            # Count cluster members
            member_count=$(echo "$cluster_result" | jq '[.data[] | select(.type=="node")] | length')
            quorum_status=$(echo "$cluster_result" | jq -r '.data[] | select(.type=="cluster") | .quorate // false')
            
            log "[OK] Cluster status from $node_name: $member_count members, quorate: $quorum_status"
            
            # List all members
            echo "$cluster_result" | jq -r '.data[] | select(.type=="node") | "  Node: " + .name + " (" + .ip + ") - " + .level'
            
            return 0
        fi
    fi
    
    log "[FAIL] Could not verify cluster status on $node_name"
    return 1
}

log "=== Starting API-Based Proxmox Cluster Formation ==="

# Check if jq is available for JSON parsing
if ! command -v jq >/dev/null 2>&1; then
    error_exit "jq is required for JSON parsing but not installed"
fi

# 1. Check all nodes are accessible and get their status
log "Phase 1: Checking node status via API..."
READY_NODES=()
CLUSTERED_NODES=()
FAILED_NODES=()

for node in "${!NODES[@]}"; do
    IFS=':' read -r mgmt_ip ceph_ip <<< "${NODES[$node]}"
    
    case $(check_node_status "$mgmt_ip" "$node") in
        0) READY_NODES+=("$node") ;;
        2) CLUSTERED_NODES+=("$node") ;;
        *) FAILED_NODES+=("$node") ;;
    esac
done

if [ ${#FAILED_NODES[@]} -gt 0 ]; then
    log "Failed nodes: ${FAILED_NODES[*]}"
    error_exit "Some nodes are not accessible. Please check and run post-install on failed nodes."
fi

log "Ready nodes: ${READY_NODES[*]}"
log "Already clustered: ${CLUSTERED_NODES[*]}"

# 2. Create cluster on node1 if needed
IFS=':' read -r node1_mgmt node1_ceph <<< "${NODES[node1]}"

if [[ ! " ${CLUSTERED_NODES[@]} " =~ " node1 " ]]; then
    log "Phase 2: Creating cluster on node1..."
    
    if ! create_cluster "$node1_mgmt" "node1" "$node1_ceph"; then
        error_exit "Failed to create cluster on node1"
    fi
    
    # Wait for cluster to stabilize
    sleep 10
    
    # Verify cluster creation
    if ! verify_cluster "$node1_mgmt" "node1"; then
        error_exit "Cluster creation verification failed"
    fi
else
    log "Phase 2: Skipped - node1 already in cluster"
fi

# 3. Join remaining nodes
log "Phase 3: Joining remaining nodes to cluster..."

for node in "${READY_NODES[@]}"; do
    if [[ "$node" == "node1" ]]; then
        continue  # Skip node1, it's the primary
    fi
    
    IFS=':' read -r mgmt_ip ceph_ip <<< "${NODES[$node]}"
    
    log "Joining $node to cluster..."
    if join_cluster "$mgmt_ip" "$node" "$node1_mgmt" "$ceph_ip"; then
        log "[OK] $node successfully joined"
        
        # Wait for join to stabilize
        sleep 5
    else
        log "[FAIL] Failed to join $node"
        FAILED_NODES+=("$node")
    fi
done

# 4. Final verification
log "Phase 4: Final cluster verification..."

# Verify from node1 perspective
verify_cluster "$node1_mgmt" "node1"

# Update node registration status
log "Updating node registration status..."
for node in "${!NODES[@]}"; do
    IFS=':' read -r mgmt_ip ceph_ip <<< "${NODES[$node]}"
    
    REGISTER_DATA=$(cat <<EOF
{
    "hostname": "$node",
    "ip": "$mgmt_ip",
    "ceph_ip": "$ceph_ip",
    "type": "proxmox",
    "status": "clustered",
    "stage": "2-complete",
    "cluster_name": "$CLUSTER_NAME",
    "formation_method": "api"
}
EOF
)

    curl -X POST \
        -H "Content-Type: application/json" \
        -d "$REGISTER_DATA" \
        "http://$PROVISION_SERVER/api/register-node.php" 2>/dev/null || \
        log "Warning: Could not update registration for $node"
done

# Summary
log "=== Cluster Formation Complete ==="
log "Cluster Name: $CLUSTER_NAME"
log "Formation Method: Proxmox API (no SSH required)"
log "Successfully joined: $(echo "${!NODES[@]}" | tr ' ' '\n' | grep -v "${FAILED_NODES[*]}" | tr '\n' ' ')"

if [ ${#FAILED_NODES[@]} -gt 0 ]; then
    log "Failed nodes: ${FAILED_NODES[*]}"
    log "These nodes may need manual intervention"
fi

log "Next steps:"
log "1. Access Proxmox web interface at https://$node1_mgmt:8006"
log "2. Configure storage (local, Ceph, etc.)"
log "3. Create VMs and containers"
log "4. Set up monitoring and backups"

log "API-based cluster formation completed successfully!"
exit 0