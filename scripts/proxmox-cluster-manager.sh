#!/bin/bash
# Proxmox Cluster Manager - Robust Cluster Formation Script
# This script provides multiple methods for cluster formation with proper error handling

set -e

# Configuration
CLUSTER_NAME="${CLUSTER_NAME:-sddc-cluster}"
API_USER="automation@pve"
API_REALM="pve"
LOG_FILE="/var/log/proxmox-cluster-manager.log"

# Node configuration (loaded from nodes.json if available)
declare -A NODES
declare -A CEPH_IPS
NODES[node1]="10.10.1.21"
NODES[node2]="10.10.1.22"
NODES[node3]="10.10.1.23"
NODES[node4]="10.10.1.24"
CEPH_IPS[node1]="10.10.2.21"
CEPH_IPS[node2]="10.10.2.22"
CEPH_IPS[node3]="10.10.2.23"
CEPH_IPS[node4]="10.10.2.24"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# Logging
log() {
    echo -e "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_FILE"
}

error_exit() {
    log "${RED}ERROR: $1${NC}"
    exit 1
}

# Load configuration from nodes.json if available
load_nodes_config() {
    if [ -f "/var/www/html/nodes.json" ]; then
        log "Loading node configuration from nodes.json..."
        # Parse nodes.json and populate arrays
        while IFS= read -r line; do
            if echo "$line" | grep -q '"hostname"'; then
                hostname=$(echo "$line" | sed 's/.*"hostname":\s*"\([^"]*\)".*/\1/')
            elif echo "$line" | grep -q '"ip"'; then
                ip=$(echo "$line" | sed 's/.*"ip":\s*"\([^"]*\)".*/\1/')
                if [ -n "$hostname" ] && [ -n "$ip" ]; then
                    NODES[$hostname]=$ip
                    # Calculate Ceph IP (assuming .2. subnet)
                    ceph_ip=$(echo "$ip" | sed 's/\.1\./\.2\./')
                    CEPH_IPS[$hostname]=$ceph_ip
                fi
            fi
        done < "/var/www/html/nodes.json"
    fi
}

# Method 1: API-based cluster joining (requires pre-configured API user)
setup_api_automation_user() {
    local master_node="$1"
    
    log "Setting up automation user for API-based joining..."
    
    # Create automation user with restricted permissions
    ssh root@"$master_node" "
        # Create user if not exists
        pveum user add ${API_USER} --comment 'Automation user for cluster operations' 2>/dev/null || true
        
        # Create role with minimal required permissions
        pveum role add ClusterJoin -privs 'Sys.Console,Sys.Modify' 2>/dev/null || true
        
        # Assign role to user
        pveum acl modify / -user ${API_USER} -role ClusterJoin
        
        # Generate API token
        pveum user token add ${API_USER} cluster-join --expire 3600 --privsep 0
    " 2>/dev/null | grep "value" | awk '{print $NF}'
}

# Method 2: Pre-shared secret approach (custom implementation)
generate_cluster_secret() {
    local secret_file="/etc/pve/cluster-join.secret"
    
    # Generate a secure random secret
    openssl rand -hex 32 > "$secret_file"
    chmod 600 "$secret_file"
    
    log "Generated cluster join secret at $secret_file"
    cat "$secret_file"
}

distribute_cluster_secret() {
    local secret="$1"
    local target_node="$2"
    
    log "Distributing cluster secret to $target_node..."
    echo "$secret" | ssh root@"$target_node" "cat > /etc/pve/cluster-join.secret && chmod 600 /etc/pve/cluster-join.secret"
}

# Method 3: Certificate-based trust (using pre-shared certificates)
setup_certificate_trust() {
    local master_node="$1"
    local target_node="$2"
    
    log "Setting up certificate trust between $master_node and $target_node..."
    
    # Get master certificate fingerprint
    local fingerprint=$(ssh root@"$master_node" \
        'openssl x509 -in /etc/pve/nodes/$(hostname)/pve-ssl.pem -noout -fingerprint -sha256' \
        | cut -d= -f2)
    
    # Pre-approve the certificate on target
    ssh root@"$target_node" "
        mkdir -p /etc/pve/priv/
        echo '$master_node:$fingerprint' >> /etc/pve/priv/known_hosts
    "
    
    echo "$fingerprint"
}

# Method 4: Expect-based automation (fallback)
install_expect_if_needed() {
    local node="$1"
    
    ssh root@"$node" "
        if ! command -v expect &> /dev/null; then
            apt-get update && apt-get install -y expect
        fi
    " 2>/dev/null
}

create_expect_join_script() {
    cat << 'EOF'
#!/usr/bin/expect -f
set timeout 60
set node [lindex $argv 0]
set password [lindex $argv 1]
set local_ip [lindex $argv 2]
set ceph_ip [lindex $argv 3]
set fingerprint [lindex $argv 4]

spawn pvecm add $node --link0 $local_ip --link1 $ceph_ip --fingerprint $fingerprint

expect {
    "password:" {
        send "$password\r"
        exp_continue
    }
    "Are you sure you want to continue connecting" {
        send "yes\r"
        exp_continue
    }
    "successfully added node" {
        exit 0
    }
    timeout {
        exit 1
    }
    eof {
        exit 0
    }
}
EOF
}

# Method 5: Ansible-based automation
create_ansible_playbook() {
    cat << 'EOF'
---
- name: Join Proxmox Cluster
  hosts: "{{ target_node }}"
  vars:
    cluster_master: "{{ master_node }}"
    cluster_password: "{{ master_password }}"
    local_ip: "{{ ansible_default_ipv4.address }}"
    ceph_ip: "{{ local_ip | regex_replace('\.1\.', '.2.') }}"
  
  tasks:
    - name: Install expect
      apt:
        name: expect
        state: present
    
    - name: Get certificate fingerprint from master
      delegate_to: "{{ cluster_master }}"
      shell: |
        openssl x509 -in /etc/pve/nodes/$(hostname)/pve-ssl.pem \
        -noout -fingerprint -sha256 | cut -d= -f2
      register: cert_fingerprint
    
    - name: Create join script
      copy:
        content: |
          #!/usr/bin/expect -f
          spawn pvecm add {{ cluster_master }} \
            --link0 {{ local_ip }} \
            --link1 {{ ceph_ip }} \
            --fingerprint {{ cert_fingerprint.stdout }}
          expect "password:"
          send "{{ cluster_password }}\r"
          expect eof
        dest: /tmp/join-cluster.exp
        mode: '0700'
    
    - name: Execute join script
      command: /tmp/join-cluster.exp
      register: join_result
    
    - name: Verify cluster membership
      command: pvecm status
      register: cluster_status
    
    - name: Cleanup
      file:
        path: /tmp/join-cluster.exp
        state: absent
EOF
}

# Main cluster formation function
form_cluster() {
    local method="${1:-auto}"
    local master_password="${2:-}"
    
    log "${BLUE}=== Starting Robust Cluster Formation ===${NC}"
    log "Method: $method"
    
    # Load configuration
    load_nodes_config
    
    # Determine master node (first node)
    local master_node=""
    local master_ip=""
    for node in node1 node2 node3 node4; do
        if [ -n "${NODES[$node]}" ]; then
            master_node="$node"
            master_ip="${NODES[$node]}"
            break
        fi
    done
    
    if [ -z "$master_node" ]; then
        error_exit "No master node found in configuration"
    fi
    
    log "Master node: $master_node ($master_ip)"
    
    # Check if cluster already exists
    if ssh root@"$master_ip" 'pvecm status 2>/dev/null | grep -q "Quorum provider"'; then
        log "${YELLOW}Cluster already exists on $master_node${NC}"
        local existing_nodes=$(ssh root@"$master_ip" 'pvecm nodes 2>/dev/null | grep -c "^[[:space:]]*[0-9]"' || echo "1")
        log "Existing cluster has $existing_nodes nodes"
    else
        log "Creating new cluster on $master_node..."
        ssh root@"$master_ip" "pvecm create $CLUSTER_NAME --link0 $master_ip --link1 ${CEPH_IPS[$master_node]}"
        log "${GREEN}[OK] Cluster created${NC}"
    fi
    
    # Get certificate fingerprint
    local fingerprint=$(ssh root@"$master_ip" \
        'openssl x509 -in /etc/pve/nodes/$(hostname)/pve-ssl.pem -noout -fingerprint -sha256' \
        | cut -d= -f2)
    
    log "Master certificate fingerprint: $fingerprint"
    
    # Join remaining nodes based on selected method
    case "$method" in
        "api")
            log "Using API-based method..."
            local api_token=$(setup_api_automation_user "$master_ip")
            log "API token: $api_token"
            # Implementation would require custom API client
            ;;
            
        "secret")
            log "Using pre-shared secret method..."
            local secret=$(ssh root@"$master_ip" generate_cluster_secret)
            for node in "${!NODES[@]}"; do
                if [ "$node" != "$master_node" ]; then
                    distribute_cluster_secret "$secret" "${NODES[$node]}"
                fi
            done
            ;;
            
        "cert")
            log "Using certificate trust method..."
            for node in "${!NODES[@]}"; do
                if [ "$node" != "$master_node" ]; then
                    setup_certificate_trust "$master_ip" "${NODES[$node]}"
                fi
            done
            ;;
            
        "ansible")
            log "Using Ansible automation..."
            create_ansible_playbook > /tmp/join-cluster.yml
            for node in "${!NODES[@]}"; do
                if [ "$node" != "$master_node" ]; then
                    ansible-playbook -i "${NODES[$node]}," /tmp/join-cluster.yml \
                        -e "target_node=${NODES[$node]}" \
                        -e "master_node=$master_ip" \
                        -e "master_password=$master_password"
                fi
            done
            ;;
            
        "expect"|"auto"|*)
            log "Using expect-based automation (fallback)..."
            if [ -z "$master_password" ]; then
                # Set a temporary password if not provided
                master_password="TempCluster$(date +%s)"
                ssh root@"$master_ip" "echo 'root:$master_password' | chpasswd"
                log "${YELLOW}Set temporary password on master${NC}"
            fi
            
            # Create expect script
            create_expect_join_script > /tmp/join-cluster.exp
            chmod +x /tmp/join-cluster.exp
            
            # Join each node
            for node in "${!NODES[@]}"; do
                if [ "$node" != "$master_node" ]; then
                    local node_ip="${NODES[$node]}"
                    local ceph_ip="${CEPH_IPS[$node]}"
                    
                    log "Joining $node ($node_ip)..."
                    
                    # Install expect on target node
                    install_expect_if_needed "$node_ip"
                    
                    # Copy and execute join script
                    scp /tmp/join-cluster.exp root@"$node_ip":/tmp/
                    
                    if ssh root@"$node_ip" "/tmp/join-cluster.exp $master_ip '$master_password' $node_ip $ceph_ip '$fingerprint'"; then
                        log "${GREEN}[OK] $node joined successfully${NC}"
                    else
                        log "${RED}[FAIL] Failed to join $node${NC}"
                    fi
                fi
            done
            
            # Reset password if temporary was used
            if [[ "$master_password" == TempCluster* ]]; then
                ssh root@"$master_ip" "passwd -l root"
                log "${YELLOW}Locked temporary password${NC}"
            fi
            ;;
    esac
    
    # Verify final cluster status
    log ""
    log "${BLUE}=== Cluster Formation Summary ===${NC}"
    
    local final_nodes=$(ssh root@"$master_ip" 'pvecm nodes 2>/dev/null | grep -c "^[[:space:]]*[0-9]"' || echo "0")
    log "Cluster nodes: $final_nodes"
    
    ssh root@"$master_ip" 'pvecm status' 2>/dev/null || true
    
    if [ "$final_nodes" -eq "${#NODES[@]}" ]; then
        log "${GREEN}[SUCCESS] All nodes joined successfully!${NC}"
        return 0
    else
        log "${YELLOW}[PARTIAL] $final_nodes of ${#NODES[@]} nodes joined${NC}"
        return 1
    fi
}

# Parse command line arguments
METHOD="auto"
PASSWORD=""
ACTION="form"

while [[ $# -gt 0 ]]; do
    case $1 in
        --method)
            METHOD="$2"
            shift 2
            ;;
        --password)
            PASSWORD="$2"
            shift 2
            ;;
        --check)
            ACTION="check"
            shift
            ;;
        --reset)
            ACTION="reset"
            shift
            ;;
        --help)
            echo "Usage: $0 [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --method <auto|api|secret|cert|ansible|expect>  Cluster join method (default: auto)"
            echo "  --password <password>                            Master node root password"
            echo "  --check                                          Check cluster status only"
            echo "  --reset                                          Reset cluster on all nodes"
            echo "  --help                                           Show this help message"
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            exit 1
            ;;
    esac
done

# Execute requested action
case "$ACTION" in
    "form")
        form_cluster "$METHOD" "$PASSWORD"
        ;;
    "check")
        load_nodes_config
        for node in "${!NODES[@]}"; do
            echo "=== $node (${NODES[$node]}) ==="
            ssh root@"${NODES[$node]}" 'pvecm status 2>/dev/null | head -20' || echo "Not in cluster"
            echo ""
        done
        ;;
    "reset")
        load_nodes_config
        for node in "${!NODES[@]}"; do
            log "Resetting cluster on $node..."
            ssh root@"${NODES[$node]}" '
                systemctl stop pve-cluster corosync
                pmxcfs -l
                rm -rf /etc/corosync/*
                rm -rf /etc/pve/corosync.conf
                killall pmxcfs 2>/dev/null || true
                systemctl start pve-cluster
            ' 2>/dev/null || true
        done
        log "Cluster reset complete"
        ;;
esac