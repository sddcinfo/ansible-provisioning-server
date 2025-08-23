#!/bin/bash
# Test script to verify SSH timeout functionality

set -e

log() {
    echo "[$(date '+%H:%M:%S')] $1"
}

# SSH options that prevent hanging
SSH_OPTS="-o ConnectTimeout=5 -o ServerAliveInterval=5 -o ServerAliveCountMax=1 -o StrictHostKeyChecking=no -o PasswordAuthentication=no -o BatchMode=yes -o LogLevel=ERROR"

# Test nodes (some may not exist to test timeout)
TEST_NODES=("10.10.1.21" "10.10.1.22" "10.10.1.23" "10.10.1.24" "10.10.1.99")

log "Testing SSH timeout functionality..."

for node in "${TEST_NODES[@]}"; do
    log "Testing SSH to $node..."
    
    start_time=$(date +%s)
    
    if timeout 10 ssh $SSH_OPTS root@"$node" 'echo SSH-OK' >/dev/null 2>&1; then
        end_time=$(date +%s)
        duration=$((end_time - start_time))
        log "[OK] SSH to $node succeeded in ${duration}s"
    else
        end_time=$(date +%s)
        duration=$((end_time - start_time))
        log "[TIMEOUT] SSH to $node failed/timed out in ${duration}s"
    fi
done

log "SSH timeout test completed"