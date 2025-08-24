#!/bin/bash
# Enhanced Node Reprovision Script
# This script will:
# 1. Set specified nodes to boot from PXE (EFI boot entry 000E)
# 2. Call the provisioning server API to reset node status
# 3. Reboot specified nodes to start fresh provisioning
#
# Usage:
#   ./reboot-nodes-for-reprovision.sh --all                    # Reboot all nodes
#   ./reboot-nodes-for-reprovision.sh --nodes node1,node3      # Reboot specific nodes
#   ./reboot-nodes-for-reprovision.sh --list                   # List available nodes
#   ./reboot-nodes-for-reprovision.sh --help                   # Show help

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

# Configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NODES_JSON_PATH="${SCRIPT_DIR}/../nodes.json"
SERVER_IP="10.10.1.1"
PROVISIONING_API_URL="http://${SERVER_IP}/index.php"

# SSH options for reliable connections with enhanced timeout handling
SSH_OPTS="-o ConnectTimeout=8 -o ServerAliveInterval=3 -o ServerAliveCountMax=2 -o StrictHostKeyChecking=no -o PasswordAuthentication=no -o BatchMode=yes -o LogLevel=ERROR"

# Global variables
declare -A NODE_DATA
SELECTED_NODES=()
ALL_NODES_MODE=false
DELAY_BETWEEN_NODES=10

log() {
    echo -e "[$(date '+%Y-%m-%d %H:%M:%S')] $1"
}

usage() {
    cat << EOF
Enhanced Node Reprovision Script

USAGE:
    $0 [OPTIONS]

OPTIONS:
    --all                   Reboot all nodes sequentially (with ${DELAY_BETWEEN_NODES}s delay between nodes)
    --nodes NODE1,NODE2     Reboot specific nodes (comma-separated list)
    --list                  List all available nodes and exit
    --delay SECONDS         Set delay between nodes when using --all (default: ${DELAY_BETWEEN_NODES}s)
    --help                  Show this help message

EXAMPLES:
    $0 --all                           # Reboot all nodes with default delay
    $0 --all --delay 15                # Reboot all nodes with 15s delay
    $0 --nodes node1,node3             # Reboot only node1 and node3
    $0 --nodes node2                   # Reboot only node2
    $0 --list                          # Show available nodes

NOTES:
    - Node configuration is read from: ${NODES_JSON_PATH}
    - Each node will be set to PXE boot before rebooting
    - Provisioning API status will be reset for each node
    - Script handles timeout scenarios gracefully
    - Failed nodes will be reported at the end

EOF
}

# Function to load node configuration from JSON file
load_node_config() {
    if [ ! -f "$NODES_JSON_PATH" ]; then
        log "${RED}[ERROR]${NC} Nodes configuration file not found: $NODES_JSON_PATH"
        exit 1
    fi
    
    if ! command -v jq >/dev/null 2>&1; then
        log "${RED}[ERROR]${NC} jq is required but not installed. Please install jq (apt install jq)"
        exit 1
    fi
    
    log "${BLUE}Loading node configuration from: ${NODES_JSON_PATH}${NC}"
    
    # Parse JSON and populate NODE_DATA associative array
    local node_count=0
    while IFS= read -r line; do
        local os_hostname=$(echo "$line" | jq -r '.os_hostname')
        local os_ip=$(echo "$line" | jq -r '.os_ip')
        local os_mac=$(echo "$line" | jq -r '.os_mac')
        local ceph_ip=$(echo "$line" | jq -r '.ceph_ip')
        local console_ip=$(echo "$line" | jq -r '.console_ip // "N/A"')
        
        NODE_DATA["${os_hostname}_ip"]="$os_ip"
        NODE_DATA["${os_hostname}_mac"]="$os_mac"
        NODE_DATA["${os_hostname}_ceph_ip"]="$ceph_ip"
        NODE_DATA["${os_hostname}_console_ip"]="$console_ip"
        
        ((node_count++))
    done < <(jq -c '.nodes[]' "$NODES_JSON_PATH")
    
    log "${GREEN}[OK]${NC} Loaded $node_count nodes from configuration file"
}

# Function to get list of all available nodes
get_all_nodes() {
    local nodes=()
    for key in "${!NODE_DATA[@]}"; do
        if [[ "$key" == *"_ip" ]] && [[ "$key" != *"_ceph_ip" ]] && [[ "$key" != *"_console_ip" ]]; then
            local node_name="${key%_ip}"
            nodes+=("$node_name")
        fi
    done
    # Sort the nodes array
    IFS=$'\n' nodes=($(sort <<<"${nodes[*]}"))
    echo "${nodes[@]}"
}

# Function to list all available nodes
list_nodes() {
    local all_nodes=($(get_all_nodes))
    
    echo ""
    log "${CYAN}=== Available Nodes ===${NC}"
    
    for node in "${all_nodes[@]}"; do
        local ip="${NODE_DATA[${node}_ip]}"
        local mac="${NODE_DATA[${node}_mac]}"
        local ceph_ip="${NODE_DATA[${node}_ceph_ip]}"
        
        printf "  %-8s IP: %-15s MAC: %-17s Ceph: %s\n" \
            "$node" "$ip" "$mac" "$ceph_ip"
    done
    echo ""
    log "Total nodes available: ${#all_nodes[@]}"
    echo ""
}

# Function to validate node exists
validate_node() {
    local node_name="$1"
    if [ -z "${NODE_DATA[${node_name}_ip]}" ]; then
        log "${RED}[ERROR]${NC} Node '$node_name' not found in configuration"
        return 1
    fi
    return 0
}

# Function to reset node status via provisioning API with enhanced error handling
reset_node_status() {
    local node_name="$1"
    local node_mac="$2"
    local node_ip="$3"
    
    log "Resetting provisioning status for $node_name via API..."
    
    # Call the provisioning server API to set status back to NEW (reprovision)
    local api_url="${PROVISIONING_API_URL}?action=reprovision&mac=${node_mac}"
    
    # Multiple attempts with different timeout strategies
    for attempt in 1 2 3; do
        if curl --connect-timeout 10 --max-time 15 -s -f -X GET "${api_url}" >/dev/null 2>&1; then
            log "${GREEN}[OK]${NC} API call successful for $node_name - status reset to NEW"
            return 0
        else
            if [ $attempt -lt 3 ]; then
                log "${YELLOW}[WARNING]${NC} API call attempt $attempt failed for $node_name, retrying..."
                sleep 2
            fi
        fi
    done
    
    log "${RED}[FAIL]${NC} All API call attempts failed for $node_name"
    return 1
}

# Enhanced function to check if a node is accessible with better timeout handling
check_node_accessibility() {
    local node_name="$1"
    local node_ip="$2"
    
    log "Testing accessibility of $node_name ($node_ip)..."
    
    # Quick ping test first (faster than SSH)
    if timeout 3 ping -c 1 -W 1 "$node_ip" >/dev/null 2>&1; then
        log "${GREEN}[OK]${NC} $node_name responds to ping"
        
        # Now test SSH connectivity
        if timeout 8 ssh $SSH_OPTS root@"$node_ip" 'echo "SSH connection test"' >/dev/null 2>&1; then
            log "${GREEN}[OK]${NC} SSH connectivity verified for $node_name"
            return 0
        else
            log "${YELLOW}[INFO]${NC} $node_name responds to ping but SSH is unreachable (possibly rebooting)"
            return 1
        fi
    else
        log "${YELLOW}[INFO]${NC} $node_name is not responding to ping (unreachable or already rebooting)"
        return 2
    fi
}

# Function to set EFI boot order with enhanced error handling
set_pxe_boot() {
    local node_name="$1"
    local node_ip="$2"
    
    log "Setting $node_name to boot from PXE..."
    
    # Try primary EFI boot entry (000E)
    if timeout 12 ssh $SSH_OPTS root@"$node_ip" 'efibootmgr -n 000E' >/dev/null 2>&1; then
        log "${GREEN}[OK]${NC} EFI boot order set to PXE (000E) for $node_name"
        return 0
    fi
    
    log "${YELLOW}[WARNING]${NC} Primary PXE boot entry (000E) failed for $node_name, trying alternatives..."
    
    # Try alternative EFI boot entries
    for boot_entry in "14" "0014" "000F" "0E"; do
        if timeout 10 ssh $SSH_OPTS root@"$node_ip" "efibootmgr -n $boot_entry" >/dev/null 2>&1; then
            log "${GREEN}[OK]${NC} Alternative EFI boot entry ($boot_entry) set for $node_name"
            return 0
        fi
    done
    
    log "${YELLOW}[WARNING]${NC} Could not set any PXE boot entry for $node_name"
    log "${YELLOW}[INFO]${NC} Node may still boot to PXE if configured as default boot option"
    return 1
}

# Function to reboot a node with enhanced error handling
reboot_node() {
    local node_name="$1"
    local node_ip="$2"
    
    log "Initiating reboot for $node_name..."
    
    # Try graceful reboot first
    if timeout 8 ssh $SSH_OPTS root@"$node_ip" 'systemctl reboot' >/dev/null 2>&1; then
        log "${GREEN}[OK]${NC} Graceful reboot command sent to $node_name"
        return 0
    fi
    
    # Fall back to immediate reboot
    if timeout 8 ssh $SSH_OPTS root@"$node_ip" 'nohup reboot >/dev/null 2>&1 &' >/dev/null 2>&1; then
        log "${GREEN}[OK]${NC} Immediate reboot command sent to $node_name"
        return 0
    fi
    
    # Try emergency reboot
    if timeout 8 ssh $SSH_OPTS root@"$node_ip" 'echo b > /proc/sysrq-trigger' >/dev/null 2>&1; then
        log "${YELLOW}[WARNING]${NC} Emergency reboot triggered for $node_name"
        return 0
    fi
    
    log "${RED}[FAIL]${NC} All reboot methods failed for $node_name"
    return 1
}

# Function to process a single node: API call -> accessibility check -> EFI boot -> reboot
process_node() {
    local node_name="$1"
    local node_ip="${NODE_DATA[${node_name}_ip]}"
    local node_mac="${NODE_DATA[${node_name}_mac]}"
    local start_time=$(date +%s)
    
    log "${BLUE}=== Processing $node_name ($node_ip) ===${NC}"
    
    # Step 1: Reset provisioning status via API
    log "Step 1: Resetting provisioning status for $node_name via API..."
    if ! reset_node_status "$node_name" "$node_mac" "$node_ip"; then
        log "${RED}[FAIL]${NC} Could not reset API status for $node_name"
        return 1
    fi
    
    # Step 2: Check node accessibility
    log "Step 2: Checking accessibility of $node_name..."
    local accessibility_result
    check_node_accessibility "$node_name" "$node_ip"
    accessibility_result=$?
    
    if [ $accessibility_result -eq 2 ]; then
        log "${YELLOW}[INFO]${NC} $node_name is unreachable - possibly already rebooting or powered off"
        log "${GREEN}[OK]${NC} $node_name API status reset completed (node unreachable)"
        return 0
    elif [ $accessibility_result -eq 1 ]; then
        log "${YELLOW}[INFO]${NC} $node_name SSH is unreachable but responds to ping - likely shutting down"
        log "${GREEN}[OK]${NC} $node_name API status reset completed (SSH unreachable)"
        return 0
    fi
    
    # Step 3: Set EFI boot order (best effort)
    log "Step 3: Configuring PXE boot for $node_name..."
    set_pxe_boot "$node_name" "$node_ip"  # Continue even if this fails
    
    # Step 4: Reboot the node
    log "Step 4: Rebooting $node_name..."
    if ! reboot_node "$node_name" "$node_ip"; then
        log "${RED}[FAIL]${NC} Failed to reboot $node_name"
        return 1
    fi
    
    local end_time=$(date +%s)
    local duration=$((end_time - start_time))
    log "${GREEN}[SUCCESS]${NC} $node_name processed successfully (took ${duration}s)"
    return 0
}

# Parse command line arguments
parse_arguments() {
    while [[ $# -gt 0 ]]; do
        case $1 in
            --all)
                ALL_NODES_MODE=true
                shift
                ;;
            --nodes)
                if [ -z "$2" ]; then
                    log "${RED}[ERROR]${NC} --nodes requires a comma-separated list of node names"
                    exit 1
                fi
                IFS=',' read -ra SELECTED_NODES <<< "$2"
                shift 2
                ;;
            --delay)
                if [ -z "$2" ] || ! [[ "$2" =~ ^[0-9]+$ ]]; then
                    log "${RED}[ERROR]${NC} --delay requires a numeric value"
                    exit 1
                fi
                DELAY_BETWEEN_NODES="$2"
                shift 2
                ;;
            --list)
                load_node_config
                list_nodes
                exit 0
                ;;
            --help|-h)
                usage
                exit 0
                ;;
            *)
                log "${RED}[ERROR]${NC} Unknown option: $1"
                usage
                exit 1
                ;;
        esac
    done
    
    # Validate arguments
    if [ "$ALL_NODES_MODE" = false ] && [ ${#SELECTED_NODES[@]} -eq 0 ]; then
        log "${RED}[ERROR]${NC} You must specify either --all or --nodes"
        echo ""
        usage
        exit 1
    fi
    
    if [ "$ALL_NODES_MODE" = true ] && [ ${#SELECTED_NODES[@]} -gt 0 ]; then
        log "${RED}[ERROR]${NC} Cannot use --all and --nodes together"
        exit 1
    fi
}

# Main execution function
main() {
    # Show script header
    log "${CYAN}=== Enhanced Node Reprovision Script ===${NC}"
    log "Script location: $0"
    log "Nodes config: $NODES_JSON_PATH"
    log "Provisioning API: $PROVISIONING_API_URL"
    echo ""
    
    # Load node configuration
    load_node_config
    
    # Determine which nodes to process
    local nodes_to_process=()
    if [ "$ALL_NODES_MODE" = true ]; then
        nodes_to_process=($(get_all_nodes))
        log "${YELLOW}Mode: Rebooting ALL nodes with ${DELAY_BETWEEN_NODES}s delay between nodes${NC}"
    else
        nodes_to_process=("${SELECTED_NODES[@]}")
        log "${YELLOW}Mode: Rebooting selected nodes: ${SELECTED_NODES[*]}${NC}"
    fi
    
    # Validate all specified nodes exist
    local validation_failed=false
    for node in "${nodes_to_process[@]}"; do
        if ! validate_node "$node"; then
            validation_failed=true
        fi
    done
    
    if [ "$validation_failed" = true ]; then
        echo ""
        log "${CYAN}Available nodes:${NC}"
        list_nodes
        exit 1
    fi
    
    # Show what will happen
    echo ""
    log "${YELLOW}This will process the following nodes:${NC}"
    for node in "${nodes_to_process[@]}"; do
        local ip="${NODE_DATA[${node}_ip]}"
        local mac="${NODE_DATA[${node}_mac]}"
        printf "  %-8s IP: %-15s MAC: %s\n" "$node" "$ip" "$mac"
    done
    
    echo ""
    log "${YELLOW}Process for each node:${NC}"
    log "  1. Reset provisioning status via API call"
    log "  2. Check node accessibility (ping + SSH)"
    log "  3. Set node to boot from PXE (if accessible)"
    log "  4. Initiate node reboot"
    if [ "$ALL_NODES_MODE" = true ]; then
        log "  5. Wait ${DELAY_BETWEEN_NODES} seconds before processing next node"
    fi
    echo ""
    
    # Confirmation for all nodes mode
    if [ "$ALL_NODES_MODE" = true ]; then
        read -p "Are you sure you want to reboot ALL ${#nodes_to_process[@]} nodes? (y/N): " -n 1 -r
        echo
        if [[ ! $REPLY =~ ^[Yy]$ ]]; then
            log "${YELLOW}Operation cancelled by user${NC}"
            exit 0
        fi
    fi
    
    # Process nodes
    log ""
    log "${BLUE}=== Starting Node Processing ===${NC}"
    
    local success_count=0
    local failed_nodes=()
    local total_start_time=$(date +%s)
    
    for i in "${!nodes_to_process[@]}"; do
        local node="${nodes_to_process[i]}"
        
        if process_node "$node"; then
            ((success_count++))
        else
            failed_nodes+=("$node")
        fi
        
        # Add delay between nodes if processing all nodes and not the last node
        if [ "$ALL_NODES_MODE" = true ] && [ $i -lt $((${#nodes_to_process[@]} - 1)) ]; then
            log ""
            log "${BLUE}Waiting ${DELAY_BETWEEN_NODES} seconds before processing next node...${NC}"
            sleep "$DELAY_BETWEEN_NODES"
            echo ""
        else
            log ""
        fi
    done
    
    # Final summary
    local total_end_time=$(date +%s)
    local total_duration=$((total_end_time - total_start_time))
    
    log "${BLUE}=== Reprovision Process Summary ===${NC}"
    log "Total processing time: ${total_duration} seconds"
    log "Successfully processed: $success_count/${#nodes_to_process[@]} nodes"
    
    if [ ${#failed_nodes[@]} -gt 0 ]; then
        log "${YELLOW}Failed nodes: ${failed_nodes[*]}${NC}"
        log "${YELLOW}Failed nodes may need manual attention:${NC}"
        log "  - Check network connectivity"
        log "  - Verify SSH access"
        log "  - Check node power status"
        log "  - Manual reboot via console/IPMI"
    else
        log "${GREEN}All nodes processed successfully!${NC}"
    fi
    
    log ""
    log "${GREEN}=== Next Steps ===${NC}"
    log "1. Monitor nodes via console or management interface"
    log "2. Nodes should PXE boot and start OS installation"
    log "3. Wait for all nodes to complete installation (~15-20 minutes)"
    log "4. Check provisioning status: http://$SERVER_IP/index.php"
    log "5. Once all nodes are ready, run cluster formation script"
    echo ""
    
    # Exit with appropriate code
    if [ ${#failed_nodes[@]} -gt 0 ]; then
        log "${YELLOW}Script completed with ${#failed_nodes[@]} failed node(s)${NC}"
        exit 1
    else
        log "${GREEN}Script completed successfully!${NC}"
        exit 0
    fi
}

# Script entry point
if [ "${BASH_SOURCE[0]}" = "${0}" ]; then
    # Parse arguments
    parse_arguments "$@"
    
    # Run main function
    main
fi