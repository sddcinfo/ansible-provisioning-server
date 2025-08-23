#!/bin/bash
# Test Script for 2-Stage Proxmox Setup
# Run this to verify the 2-stage approach is working correctly

set -e

# Configuration
NODES=("10.10.1.21" "10.10.1.22" "10.10.1.23" "10.10.1.24")
CEPH_IPS=("10.10.2.21" "10.10.2.22" "10.10.2.23" "10.10.2.24") 
HOSTNAMES=("node1" "node2" "node3" "node4")
PROVISION_SERVER="10.10.1.1"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log() {
    echo -e "${BLUE}[$(date '+%H:%M:%S')] $1${NC}"
}

success() {
    echo -e "${GREEN}✓ $1${NC}"
}

warning() {
    echo -e "${YELLOW}⚠ $1${NC}"
}

error() {
    echo -e "${RED}✗ $1${NC}"
}

# Test Stage 1 Prerequisites
test_stage1_prerequisites() {
    log "Testing Stage 1 Prerequisites..."
    
    # Test API endpoints
    if curl -s "http://$PROVISION_SERVER/api/get-ssh-keys.php?type=proxmox" >/dev/null; then
        success "SSH key API endpoints accessible"
    else
        error "SSH key API endpoints not accessible"
        return 1
    fi
    
    # Test node accessibility
    for i in "${!NODES[@]}"; do
        if ping -c 1 -W 2 "${NODES[$i]}" >/dev/null; then
            success "Node ${HOSTNAMES[$i]} (${NODES[$i]}) reachable"
        else
            error "Node ${HOSTNAMES[$i]} (${NODES[$i]}) not reachable"
            return 1
        fi
    done
}

# Test Stage 1 Results
test_stage1_results() {
    log "Testing Stage 1 Results..."
    
    local failed_nodes=()
    
    for i in "${!NODES[@]}"; do
        local node_ip="${NODES[$i]}"
        local hostname="${HOSTNAMES[$i]}"
        local ceph_ip="${CEPH_IPS[$i]}"
        
        log "Testing $hostname..."
        
        # Test preparation marker
        if ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=no root@$node_ip \
           'test -f /var/lib/proxmox-node-prepared.done' 2>/dev/null; then
            success "$hostname - Stage 1 preparation complete"
        else
            error "$hostname - Stage 1 NOT complete"
            failed_nodes+=($hostname)
            continue
        fi
        
        # Test SSH key setup
        if ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=no root@$node_ip \
           'test -f /root/.ssh/id_ed25519' 2>/dev/null; then
            success "$hostname - SSH key generated"
        else
            warning "$hostname - SSH key not found"
        fi
        
        # Test Ceph network
        if ping -c 1 -W 2 $ceph_ip >/dev/null 2>&1; then
            success "$hostname - Ceph network ($ceph_ip) reachable"
        else
            error "$hostname - Ceph network ($ceph_ip) not reachable"
        fi
        
        # Test MTU 9000
        local mtu=$(ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=no root@$node_ip \
                   'ip link show eno3 | grep -o "mtu [0-9]*" | cut -d" " -f2' 2>/dev/null || echo "0")
        if [ "$mtu" = "9000" ]; then
            success "$hostname - eno3 MTU 9000 configured"
        else
            warning "$hostname - eno3 MTU is $mtu (expected 9000)"
        fi
        
        # Test repository configuration
        if ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=no root@$node_ip \
           'grep -q "pve-no-subscription" /etc/apt/sources.list.d/*.list' 2>/dev/null; then
            success "$hostname - Repository configuration fixed"
        else
            warning "$hostname - Repository configuration may need attention"
        fi
    done
    
    if [ ${#failed_nodes[@]} -gt 0 ]; then
        error "Failed nodes: ${failed_nodes[*]}"
        return 1
    fi
    
    success "All nodes passed Stage 1 tests"
}

# Test SSH Key Distribution
test_ssh_keys() {
    log "Testing SSH Key Distribution..."
    
    # Test API endpoint
    local key_data=$(curl -s "http://$PROVISION_SERVER/api/get-ssh-keys.php?type=proxmox")
    local key_count=$(echo "$key_data" | jq -r '.total_keys' 2>/dev/null || echo "0")
    
    if [ "$key_count" -ge "2" ]; then
        success "SSH Key API has $key_count keys"
    else
        error "SSH Key API has insufficient keys ($key_count)"
        return 1
    fi
    
    # Test bidirectional SSH connectivity
    for i in "${!NODES[@]}"; do
        local source_node="${NODES[$i]}"
        local source_hostname="${HOSTNAMES[$i]}"
        
        for j in "${!NODES[@]}"; do
            if [ $i -eq $j ]; then continue; fi
            
            local target_node="${NODES[$j]}"
            local target_hostname="${HOSTNAMES[$j]}"
            
            if ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=no root@$source_node \
               "ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=no root@$target_node 'echo SSH-OK'" \
               >/dev/null 2>&1; then
                success "$source_hostname → $target_hostname SSH working"
            else
                error "$source_hostname → $target_hostname SSH failed"
                return 1
            fi
        done
    done
}

# Test Stage 2 Prerequisites  
test_stage2_prerequisites() {
    log "Testing Stage 2 Prerequisites..."
    
    # All Stage 1 tests must pass
    test_stage1_results || return 1
    test_ssh_keys || return 1
    
    # Test cluster formation script exists
    if [ -f "./proxmox-form-cluster.sh" ]; then
        success "Cluster formation script found"
    else
        error "Cluster formation script not found"
        return 1
    fi
    
    success "Stage 2 prerequisites met"
}

# Test Stage 2 Results (if cluster exists)
test_stage2_results() {
    log "Testing Stage 2 Results (if cluster exists)..."
    
    # Check if node1 is in a cluster
    local cluster_status=$(ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=no root@${NODES[0]} \
                          'pvecm status' 2>/dev/null || echo "")
    
    if echo "$cluster_status" | grep -q "Quorum provider"; then
        success "Cluster is operational"
        
        # Count members
        local member_count=$(echo "$cluster_status" | grep -c "0x[0-9]" || echo "0")
        log "Cluster has $member_count members"
        
        # Test each node's cluster visibility
        for i in "${!NODES[@]}"; do
            local hostname="${HOSTNAMES[$i]}"
            local node_ip="${NODES[$i]}"
            
            local node_status=$(ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=no root@$node_ip \
                               'pvecm status 2>/dev/null | grep -c "0x[0-9]"' 2>/dev/null || echo "0")
            
            if [ "$node_status" -gt 0 ]; then
                success "$hostname can see cluster ($node_status members)"
            else
                warning "$hostname cannot see cluster properly"
            fi
        done
        
        # Test corosync rings
        local ring_status=$(ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=no root@${NODES[0]} \
                           'corosync-cfgtool -s' 2>/dev/null | grep -c "RING ID" || echo "0")
        
        if [ "$ring_status" -gt 0 ]; then
            success "Corosync rings operational ($ring_status rings)"
        else
            warning "Corosync ring status unclear"
        fi
        
    else
        warning "No cluster detected (this is normal before Stage 2)"
        return 0
    fi
    
    success "Stage 2 cluster tests passed"
}

# Main test function
run_tests() {
    local test_type="$1"
    
    echo "=========================================="
    echo "  Proxmox 2-Stage Setup Test Suite"
    echo "=========================================="
    echo
    
    case "$test_type" in
        "stage1-pre")
            test_stage1_prerequisites
            ;;
        "stage1-post")
            test_stage1_results
            ;;
        "ssh")
            test_ssh_keys
            ;;
        "stage2-pre")
            test_stage2_prerequisites
            ;;
        "stage2-post") 
            test_stage2_results
            ;;
        "all")
            log "Running comprehensive test suite..."
            test_stage1_prerequisites && \
            test_stage1_results && \
            test_ssh_keys && \
            test_stage2_prerequisites && \
            test_stage2_results
            ;;
        *)
            echo "Usage: $0 [stage1-pre|stage1-post|ssh|stage2-pre|stage2-post|all]"
            echo ""
            echo "Test Options:"
            echo "  stage1-pre  - Test prerequisites for Stage 1"
            echo "  stage1-post - Test Stage 1 results"
            echo "  ssh         - Test SSH key distribution"
            echo "  stage2-pre  - Test prerequisites for Stage 2"
            echo "  stage2-post - Test Stage 2 results (cluster)"
            echo "  all         - Run all tests"
            exit 1
            ;;
    esac
    
    local exit_code=$?
    echo
    if [ $exit_code -eq 0 ]; then
        success "All tests passed!"
    else
        error "Some tests failed!"
    fi
    
    return $exit_code
}

# Run tests
run_tests "${1:-all}"