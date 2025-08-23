#!/bin/bash
# Test timeout protection with hosts that will cause hanging

set -e

# Colors for logging
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log() {
    echo -e "[$(date '+%Y-%m-%d %H:%M:%S')] $1"
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

log "${GREEN}=== Testing Timeout Protection ===${NC}"
log ""

# Test cases that should timeout gracefully
TEST_CASES=(
    "10.10.1.99:nonexistent-node"
    "192.168.255.254:unreachable-ip"
    "10.10.1.100:another-nonexistent"
)

for test_case in "${TEST_CASES[@]}"; do
    IFS=':' read -r ip name <<< "$test_case"
    
    log "Testing timeout protection with $name ($ip)..."
    start_time=$(date +%s)
    
    if test_ssh_robust "$ip" "$name"; then
        end_time=$(date +%s)
        duration=$((end_time - start_time))
        log "${YELLOW}[UNEXPECTED]${NC} Connection succeeded to $name ($ip) in ${duration}s"
    else
        end_time=$(date +%s)
        duration=$((end_time - start_time))
        if [ $duration -le 15 ]; then
            log "${GREEN}[OK]${NC} Timeout protection worked for $name ($ip) - failed gracefully in ${duration}s"
        else
            log "${RED}[FAIL]${NC} Timeout took too long for $name ($ip) - ${duration}s (should be ≤15s)"
        fi
    fi
    log ""
done

log "${GREEN}=== Timeout Protection Test Complete ===${NC}"