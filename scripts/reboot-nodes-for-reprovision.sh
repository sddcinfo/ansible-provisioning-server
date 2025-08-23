#!/bin/bash
# Reboot all nodes to PXE boot and trigger reprovisioning
# This script will:
# 1. Set each node to boot from PXE (EFI boot entry 000E)
# 2. Call the provisioning server API to reset node status
# 3. Reboot each node to start fresh provisioning

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log() {
    echo -e "[$(date '+%Y-%m-%d %H:%M:%S')] $1"
}

# Configuration
SERVER_IP="10.10.1.1"
PROVISIONING_API_URL="http://${SERVER_IP}/index.php"

# Node configuration
NODES=("10.10.1.21" "10.10.1.22" "10.10.1.23" "10.10.1.24")
NODE_NAMES=("node1" "node2" "node3" "node4")
NODE_MACS=("ac:1f:6b:6c:5a:76" "ac:1f:6b:6c:5a:6c" "ac:1f:6b:6c:5a:20" "ac:1f:6b:6c:5a:28")

# SSH options for reliable connections
SSH_OPTS="-o ConnectTimeout=10 -o ServerAliveInterval=5 -o ServerAliveCountMax=1 -o StrictHostKeyChecking=no -o PasswordAuthentication=no -o BatchMode=yes -o LogLevel=ERROR"

log "${BLUE}=== Starting Node Reprovision Process ===${NC}"
log ""

# Function to reset node status via provisioning API
reset_node_status() {
    local node_name="$1"
    local node_mac="$2"
    local node_ip="$3"
    
    log "Resetting provisioning status for $node_name via API..."
    
    # Call the provisioning server API to set status back to NEW (reprovision)
    local api_url="${PROVISIONING_API_URL}?action=reprovision&mac=${node_mac}"
    
    if curl -s -f -X GET "${api_url}" >/dev/null 2>&1; then
        log "${GREEN}[OK]${NC} API call successful for $node_name - status reset to NEW"
        return 0
    else
        log "${YELLOW}[WARNING]${NC} API call failed for $node_name, continuing anyway"
        return 1
    fi
}

# Function to process a single node: API call -> EFI boot -> reboot
process_node() {
    local node_ip="$1"
    local node_name="$2"
    local node_mac="$3"
    
    log "${BLUE}=== Processing $node_name ($node_ip) ===${NC}"
    
    # Step 1: Reset provisioning status via API
    log "Step 1: Resetting provisioning status for $node_name via API..."
    local api_url="${PROVISIONING_API_URL}?action=reprovision&mac=${node_mac}"
    
    if curl -s -f -X GET "${api_url}" >/dev/null 2>&1; then
        log "${GREEN}[OK]${NC} API call successful for $node_name - status reset to NEW"
    else
        log "${RED}[FAIL]${NC} API call failed for $node_name"
        return 1
    fi
    
    # Step 2: Check if node is accessible
    log "Step 2: Testing SSH connectivity to $node_name..."
    if ! timeout 10 ssh $SSH_OPTS root@"$node_ip" 'echo "SSH connection test"' >/dev/null 2>&1; then
        log "${RED}[FAIL]${NC} Cannot SSH to $node_name ($node_ip)"
        return 1
    fi
    log "${GREEN}[OK]${NC} SSH connectivity verified"
    
    # Step 3: Set EFI boot order
    log "Step 3: Setting $node_name to boot from PXE (EFI entry 000E)..."
    if timeout 15 ssh $SSH_OPTS root@"$node_ip" 'efibootmgr -n 000E' >/dev/null 2>&1; then
        log "${GREEN}[OK]${NC} EFI boot order set to PXE for $node_name"
    else
        log "${YELLOW}[WARNING]${NC} Failed to set EFI boot order for $node_name, trying alternative method..."
        # Try setting PXE boot via alternative method
        if timeout 15 ssh $SSH_OPTS root@"$node_ip" 'efibootmgr -n 14' >/dev/null 2>&1; then
            log "${GREEN}[OK]${NC} Alternative EFI boot entry set for $node_name"
        else
            log "${RED}[FAIL]${NC} Could not set PXE boot for $node_name"
            return 1
        fi
    fi
    
    # Step 4: Reboot the node
    log "Step 4: Rebooting $node_name..."
    if timeout 10 ssh $SSH_OPTS root@"$node_ip" 'nohup reboot >/dev/null 2>&1 &' >/dev/null 2>&1; then
        log "${GREEN}[OK]${NC} Reboot command sent to $node_name"
    else
        log "${RED}[FAIL]${NC} Reboot command failed for $node_name"
        return 1
    fi
    
    log "${GREEN}[SUCCESS]${NC} $node_name processed successfully"
    return 0
}

# Confirm action with user
echo ""
log "${YELLOW}WARNING: This will reboot ALL nodes and trigger complete reprovisioning!${NC}"
log "This will process each node in sequence:"
log "  1. Reset provisioning status via API call"
log "  2. Set node to boot from PXE"
log "  3. Reboot node immediately"
log "  4. Wait 15 seconds before processing next node"
echo ""
read -p "Are you sure you want to proceed? (type 'yes' to continue): " -r
if [[ $REPLY != "yes" ]]; then
    log "Operation cancelled by user"
    exit 0
fi

log ""
log "${BLUE}=== Processing Nodes in Sequence ===${NC}"

# Process each node in order: API -> EFI -> reboot
SUCCESS_COUNT=0
FAILED_NODES=()

for i in "${!NODES[@]}"; do
    node_ip="${NODES[i]}"
    node_name="${NODE_NAMES[i]}"
    node_mac="${NODE_MACS[i]}"
    
    if process_node "$node_ip" "$node_name" "$node_mac"; then
        ((SUCCESS_COUNT++))
        log ""
        
        # Wait between nodes to avoid overwhelming the network
        if [ $i -lt $((${#NODES[@]} - 1)) ]; then
            log "${BLUE}Waiting 15 seconds before processing next node...${NC}"
            sleep 15
        fi
    else
        FAILED_NODES+=("$node_name")
        log ""
    fi
done

log ""
log "${BLUE}=== Reprovision Process Summary ===${NC}"
log "Successfully processed: $SUCCESS_COUNT/${#NODES[@]} nodes"

if [ ${#FAILED_NODES[@]} -gt 0 ]; then
    log "${YELLOW}Failed nodes: ${FAILED_NODES[*]}${NC}"
    log "You may need to manually reboot failed nodes or check their status"
else
    log "${GREEN}All nodes processed successfully!${NC}"
fi

log ""
log "${GREEN}=== Next Steps ===${NC}"
log "1. Monitor nodes via console or management interface"
log "2. Nodes should PXE boot and start Proxmox installation"
log "3. Wait for all nodes to complete installation (~15-20 minutes)"
log "4. Check provisioning status: http://$SERVER_IP/index.php"
log "5. Once all nodes are ready, run cluster formation script again"
log ""
log "${BLUE}Reprovision process initiated successfully!${NC}"