#!/bin/bash
# Fix SSH authentication between cluster nodes for Proxmox cluster formation

set -e

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1"
}

# Node IPs
NODES=("10.10.1.21" "10.10.1.22" "10.10.1.23" "10.10.1.24")
NODE_NAMES=("node1" "node2" "node3" "node4")

log "=== Fixing SSH Authentication Between Cluster Nodes ==="

# Collect all public keys
ALL_KEYS=""
MGMT_KEY="ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIPBG18KoYrX7WQA9FQGOZhLgsgpALC2TNGnWxswPJgYZ root@mgmt"

# Add management server key
ALL_KEYS="$MGMT_KEY"$'\n'

log "Collecting public keys from all nodes..."
for i in "${!NODES[@]}"; do
    node_ip="${NODES[i]}"
    node_name="${NODE_NAMES[i]}"
    
    log "Getting public key from $node_name ($node_ip)..."
    if node_key=$(timeout 10 ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=no -o PasswordAuthentication=no -o BatchMode=yes root@"$node_ip" 'cat /root/.ssh/id_rsa.pub' 2>/dev/null); then
        ALL_KEYS="$ALL_KEYS$node_key"$'\n'
        log "[OK] Got key from $node_name"
    else
        log "[WARNING] Could not get key from $node_name - will try to fix"
        # Try using the original SSH key from post-install script
        if orig_key=$(timeout 10 ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=no -o PasswordAuthentication=yes root@"$node_ip" 'cat /root/.ssh/id_rsa.pub' 2>/dev/null); then
            ALL_KEYS="$ALL_KEYS$orig_key"$'\n'
            log "[OK] Got key from $node_name using fallback method"
        else
            log "[ERROR] Could not get key from $node_name at all"
        fi
    fi
done

log "Distributing all keys to all nodes..."
for i in "${!NODES[@]}"; do
    node_ip="${NODES[i]}"
    node_name="${NODE_NAMES[i]}"
    
    log "Updating authorized_keys on $node_name ($node_ip)..."
    
    # Create a temporary authorized_keys file
    temp_keys_file="/tmp/authorized_keys_$node_name"
    echo "$ALL_KEYS" > "$temp_keys_file"
    
    # Copy to node and set up
    if timeout 15 scp -o ConnectTimeout=5 -o StrictHostKeyChecking=no -o PasswordAuthentication=no -o BatchMode=yes "$temp_keys_file" root@"$node_ip":/tmp/new_authorized_keys 2>/dev/null; then
        # Set up the keys
        timeout 15 ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=no -o PasswordAuthentication=no -o BatchMode=yes root@"$node_ip" '
            # Backup current authorized_keys
            cp /root/.ssh/authorized_keys /root/.ssh/authorized_keys.backup 2>/dev/null || true
            
            # Install new keys
            mkdir -p /root/.ssh
            chmod 700 /root/.ssh
            mv /tmp/new_authorized_keys /root/.ssh/authorized_keys
            chmod 600 /root/.ssh/authorized_keys
            chown root:root /root/.ssh/authorized_keys
            
            # Remove symlink if it exists and create real file
            if [ -L /root/.ssh/authorized_keys ]; then
                rm /root/.ssh/authorized_keys
                mv /tmp/new_authorized_keys /root/.ssh/authorized_keys
                chmod 600 /root/.ssh/authorized_keys
            fi
            
            echo "SSH keys updated successfully"
        ' 2>/dev/null && log "[OK] Updated $node_name successfully"
    else
        log "[ERROR] Could not update $node_name"
    fi
    
    # Clean up temp file
    rm -f "$temp_keys_file"
done

log "Testing SSH connectivity between all nodes..."
for i in "${!NODES[@]}"; do
    src_ip="${NODES[i]}"
    src_name="${NODE_NAMES[i]}"
    
    log "Testing SSH from $src_name..."
    for j in "${!NODES[@]}"; do
        if [ $i -ne $j ]; then
            dst_ip="${NODES[j]}"
            dst_name="${NODE_NAMES[j]}"
            
            if timeout 10 ssh -o ConnectTimeout=3 -o StrictHostKeyChecking=no -o PasswordAuthentication=no -o BatchMode=yes root@"$src_ip" "ssh -o ConnectTimeout=3 -o StrictHostKeyChecking=no -o PasswordAuthentication=no -o BatchMode=yes root@$dst_ip 'echo SSH-OK'" >/dev/null 2>&1; then
                log "[OK] $src_name → $dst_name works"
            else
                log "[FAIL] $src_name → $dst_name failed"
            fi
        fi
    done
done

log "=== SSH Fix Complete ==="