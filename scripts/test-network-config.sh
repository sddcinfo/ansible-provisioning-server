#!/bin/bash
# Network Configuration Test Script
# Tests the Proxmox network configuration for proper routing and connectivity

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log() {
    echo -e "${BLUE}[$(date '+%H:%M:%S')] $1${NC}"
}

success() {
    echo -e "${GREEN}[OK] $1${NC}"
}

warning() {
    echo -e "${YELLOW} $1${NC}"
}

error() {
    echo -e "${RED}[FAIL] $1${NC}"
}

test_network_config() {
    local hostname=$1
    
    log "Testing network configuration for $hostname..."
    
    # Test basic interface existence
    if ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=no root@$hostname 'ip link show vmbr0' >/dev/null 2>&1; then
        success "Management bridge (vmbr0) exists"
    else
        error "Management bridge (vmbr0) missing"
        return 1
    fi
    
    if ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=no root@$hostname 'ip link show vmbr1' >/dev/null 2>&1; then
        success "Ceph bridge (vmbr1) exists"
    else
        error "Ceph bridge (vmbr1) missing"
        return 1
    fi
    
    if ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=no root@$hostname 'ip link show eno3' >/dev/null 2>&1; then
        success "Physical interface (eno3) exists"
    else
        warning "Physical interface (eno3) missing (may be different name)"
    fi
    
    # Test IP configuration
    local mgmt_ip=$(ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=no root@$hostname 'ip addr show vmbr0 | grep "inet " | awk "{print \$2}" | cut -d/ -f1' 2>/dev/null)
    local ceph_ip=$(ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=no root@$hostname 'ip addr show vmbr1 | grep "inet " | awk "{print \$2}" | cut -d/ -f1' 2>/dev/null)
    
    if [ -n "$mgmt_ip" ]; then
        success "Management IP: $mgmt_ip"
    else
        error "No management IP configured"
        return 1
    fi
    
    if [ -n "$ceph_ip" ]; then
        success "Ceph IP: $ceph_ip"
    else
        error "No Ceph IP configured"
        return 1
    fi
    
    # Test MTU configuration
    local vmbr1_mtu=$(ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=no root@$hostname 'ip link show vmbr1 | grep -o "mtu [0-9]*" | cut -d" " -f2' 2>/dev/null)
    if [ "$vmbr1_mtu" = "9000" ]; then
        success "Ceph bridge MTU: $vmbr1_mtu"
    else
        warning "Ceph bridge MTU: $vmbr1_mtu (expected 9000)"
    fi
    
    # Test bridge configuration
    local bridge_ports=$(ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=no root@$hostname 'brctl show vmbr1 2>/dev/null | tail -n +2 | awk "{print \$4}"' 2>/dev/null || echo "")
    if echo "$bridge_ports" | grep -q "eno3"; then
        success "eno3 is bridge member of vmbr1"
    else
        warning "eno3 may not be properly bridged to vmbr1 (found: $bridge_ports)"
    fi
    
    # Test routing
    local default_route=$(ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=no root@$hostname 'ip route | grep "default"' 2>/dev/null || echo "")
    if echo "$default_route" | grep -q "vmbr0"; then
        success "Default route via management network (vmbr0)"
    elif echo "$default_route" | grep -q "vmbr1"; then
        error "Default route via Ceph network (vmbr1) - should be management only"
        return 1
    else
        warning "Default route configuration unclear: $default_route"
    fi
    
    # Test broadcast configuration
    local broadcast=$(ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=no root@$hostname 'ip addr show vmbr1 | grep "inet " | grep -o "brd [0-9.]*" | cut -d" " -f2' 2>/dev/null)
    if [ "$broadcast" = "10.10.2.255" ]; then
        success "Correct broadcast address: $broadcast"
    else
        warning "Broadcast address: $broadcast (expected 10.10.2.255)"
    fi
    
    # Test connectivity
    if ping -c 1 -W 2 "$ceph_ip" >/dev/null 2>&1; then
        success "Ceph network connectivity test passed"
    else
        error "Cannot reach Ceph IP: $ceph_ip"
        return 1
    fi
    
    return 0
}

# Test all nodes
NODES=("10.10.1.21:node1" "10.10.1.22:node2" "10.10.1.23:node3" "10.10.1.24:node4")
FAILED_NODES=()

echo "=========================================="
echo "  Proxmox Network Configuration Test"
echo "=========================================="
echo

for node_info in "${NODES[@]}"; do
    IFS=':' read -r ip hostname <<< "$node_info"
    
    log "Testing $hostname ($ip)..."
    
    if ping -c 1 -W 2 "$ip" >/dev/null 2>&1; then
        if test_network_config "$ip"; then
            success "[OK] $hostname network configuration passed"
        else
            error "[FAIL] $hostname network configuration failed"
            FAILED_NODES+=("$hostname")
        fi
    else
        error "[FAIL] $hostname not reachable at $ip"
        FAILED_NODES+=("$hostname")
    fi
    echo
done

echo "=========================================="
echo "           Test Summary"
echo "=========================================="

if [ ${#FAILED_NODES[@]} -eq 0 ]; then
    success "All nodes passed network configuration tests!"
    echo
    echo "Network Configuration Summary:"
    echo "- Management Network: 10.10.1.x/24 (vmbr0, default route)"
    echo "- Ceph Network: 10.10.2.x/24 (vmbr1 ← eno3, MTU 9000, no default route)"
    echo "- Routing: Management handles internet, Ceph handles storage"
    echo
    exit 0
else
    error "Failed nodes: ${FAILED_NODES[*]}"
    echo
    echo "Check the following on failed nodes:"
    echo "1. Interface configuration: ip addr show"
    echo "2. Bridge configuration: brctl show"
    echo "3. Routing table: ip route show"
    echo "4. MTU settings: ip link show | grep mtu"
    echo
    exit 1
fi