#!/bin/bash
# Test script for the robust SSH connectivity function
# This extracts and tests just the SSH connectivity portion

set -e

# Colors for logging
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log() {
    echo -e "[$(date '+%Y-%m-%d %H:%M:%S')] $1"
}

# Node configuration (from nodes.json)
NODE_IPS=("10.10.1.21" "10.10.1.22" "10.10.1.23" "10.10.1.24")
HOSTNAMES=("node1" "node2" "node3" "node4")

# Get hostname by IP (simplified version)
get_hostname_by_ip() {
    local ip="$1"
    case "$ip" in
        "10.10.1.21") echo "node1" ;;
        "10.10.1.22") echo "node2" ;;
        "10.10.1.23") echo "node3" ;;
        "10.10.1.24") echo "node4" ;;
        *) echo "unknown" ;;
    esac
}

# Define comprehensive SSH options to prevent hanging
SSH_OPTS="-o ConnectTimeout=5 -o ServerAliveInterval=5 -o ServerAliveCountMax=1 -o StrictHostKeyChecking=no -o PasswordAuthentication=no -o BatchMode=yes -o LogLevel=ERROR"

# Function to test SSH with multiple layers of timeout protection
test_ssh_robust() {
    local target_ip="$1"
    local node_name="$2"
    
    # Layer 1: Process timeout with SIGKILL after 12 seconds
    # Layer 2: timeout command with 10 second limit  
    # Layer 3: SSH built-in timeouts (ConnectTimeout=5)
    # Layer 4: Background process with manual kill if needed
    
    local ssh_pid
    local result=1
    
    # Run SSH in background and capture PID
    (
        exec timeout 10 ssh $SSH_OPTS root@"$target_ip" 'echo SSH-OK' >/dev/null 2>&1
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

# Get current node's IP - check management network first
CURRENT_IP=$(ip addr show | grep "inet.*10\.10\.1\." | head -1 | awk '{print $2}' | cut -d'/' -f1)
if [[ -z "$CURRENT_IP" ]]; then
    CURRENT_IP=$(ip route get 1 | head -1 | cut -d' ' -f7)
    CURRENT_NODE="mgmt-server"
elif [[ "$CURRENT_IP" == "10.10.1.1" ]]; then
    CURRENT_NODE="mgmt-server"
else
    CURRENT_NODE=$(get_hostname_by_ip "$CURRENT_IP")
fi

log "${GREEN}=== Testing Robust SSH Connectivity from $CURRENT_NODE ($CURRENT_IP) ===${NC}"
log ""

SSH_SUCCESS_COUNT=0
TOTAL_TESTS=0

for node_ip in "${NODE_IPS[@]}"; do
    if [ "$node_ip" != "$CURRENT_IP" ]; then
        node_name=$(get_hostname_by_ip "$node_ip")
        ((TOTAL_TESTS++))
        
        log "Testing SSH to $node_name ($node_ip)..."
        
        start_time=$(date +%s)
        if test_ssh_robust "$node_ip" "$node_name"; then
            end_time=$(date +%s)
            duration=$((end_time - start_time))
            log "${GREEN}[OK]${NC} SSH connectivity to $node_name ($node_ip) works (${duration}s)"
            ((SSH_SUCCESS_COUNT++))
        else
            end_time=$(date +%s)
            duration=$((end_time - start_time))
            log "${RED}[FAIL]${NC} SSH connectivity to $node_name ($node_ip) failed (${duration}s)"
        fi
        log ""
    fi
done

log "${GREEN}=== Test Results ===${NC}"
log "Successful connections: $SSH_SUCCESS_COUNT/$TOTAL_TESTS"

if [ $SSH_SUCCESS_COUNT -eq $TOTAL_TESTS ]; then
    log "${GREEN}[SUCCESS] All SSH connections working properly${NC}"
    exit 0
elif [ $SSH_SUCCESS_COUNT -gt 0 ]; then
    log "${YELLOW}[PARTIAL] Some SSH connections working${NC}"
    exit 1
else
    log "${RED}[FAIL] No SSH connections working${NC}"
    exit 2
fi