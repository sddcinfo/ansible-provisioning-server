# Proxmox Cluster Formation Guide

## Overview

This document describes the robust cluster formation methods available for automating Proxmox VE cluster deployment without requiring interactive password entry.

## Problems with Standard Approach

The standard `pvecm add` command has several limitations for automation:

1. **Requires root password authentication** - Cannot use SSH keys alone
2. **Interactive prompts** - Certificate fingerprint confirmation
3. **No native API token support** - Cannot use API tokens for joining
4. **SSH dependency** - Requires root SSH access between nodes

## Improved Solutions

### 1. Proxmox Cluster Manager Script

The `proxmox-cluster-manager.sh` script provides multiple methods for automated cluster formation:

```bash
# Method 1: Automatic (uses expect with temporary password)
./proxmox-cluster-manager.sh --method auto

# Method 2: With pre-set password
./proxmox-cluster-manager.sh --method expect --password "YourPassword"

# Method 3: Using Ansible automation
./proxmox-cluster-manager.sh --method ansible --password "YourPassword"

# Check cluster status
./proxmox-cluster-manager.sh --check

# Reset cluster (clean slate)
./proxmox-cluster-manager.sh --reset
```

### 2. API-Based Automation (Future Enhancement)

While Proxmox doesn't natively support API tokens for cluster joining, we can implement a custom solution:

```python
#!/usr/bin/env python3
import proxmoxer
import paramiko
import json

class ProxmoxClusterAutomation:
    def __init__(self, master_ip, api_user, api_password):
        self.proxmox = proxmoxer.ProxmoxAPI(
            master_ip,
            user=api_user,
            password=api_password,
            verify_ssl=False
        )
    
    def prepare_node_for_joining(self, node_ip, node_name):
        """Prepare a node for cluster joining"""
        # Create temporary join token
        token = self.proxmox.access.users(api_user).token.post(
            tokenid='join-token',
            expire=3600,
            privsep=0
        )
        
        # Distribute token to target node
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(node_ip, username='root', key_filename='/root/.ssh/id_rsa')
        
        # Execute join with token (custom implementation needed)
        stdin, stdout, stderr = ssh.exec_command(
            f'pvecm add {master_ip} --token {token["value"]}'
        )
        
        return stdout.read().decode()
```

### 3. Ansible Playbook Approach

Create a complete Ansible playbook for cluster deployment:

```yaml
---
- name: Deploy Proxmox Cluster
  hosts: proxmox_nodes
  vars:
    cluster_name: "sddc-cluster"
    master_node: "node1"
    
  tasks:
    - name: Install required packages
      apt:
        name: [expect, python3-proxmoxer]
        state: present
    
    - name: Configure node preparation
      script: proxmox-post-install.sh
      when: inventory_hostname != master_node
    
    - name: Create cluster on master
      command: pvecm create {{ cluster_name }}
      when: inventory_hostname == master_node
      run_once: true
    
    - name: Get cluster join information
      command: pvecm status
      register: cluster_status
      when: inventory_hostname == master_node
    
    - name: Join nodes to cluster
      include_tasks: join_node.yml
      when: inventory_hostname != master_node
```

### 4. Pre-Shared Secret Method (Custom Implementation)

Implement a pre-shared secret mechanism:

```bash
#!/bin/bash
# Generate and distribute join secret

# On master node
generate_join_secret() {
    local secret=$(openssl rand -hex 32)
    local expiry=$(date -d "+1 hour" +%s)
    
    # Store secret with expiry
    echo "{\"secret\":\"$secret\",\"expiry\":$expiry}" > /etc/pve/join.secret
    chmod 600 /etc/pve/join.secret
    
    # Create join helper script
    cat > /usr/local/bin/cluster-join-helper << EOF
#!/bin/bash
if [ "\$1" == "$secret" ]; then
    pvecm addnode \$2 --nodeid \$3 --votes 1
else
    echo "Invalid secret"
    exit 1
fi
EOF
    chmod +x /usr/local/bin/cluster-join-helper
    
    echo "$secret"
}

# On joining node
join_with_secret() {
    local master_ip="$1"
    local secret="$2"
    local node_name="$(hostname)"
    
    ssh root@"$master_ip" "/usr/local/bin/cluster-join-helper '$secret' '$node_name' '$(get_next_nodeid)'"
}
```

### 5. SSH Key-Based Automation with Sudo

Create a dedicated user for cluster operations:

```bash
# On all nodes
useradd -m -s /bin/bash clusteradmin
echo "clusteradmin ALL=(root) NOPASSWD: /usr/sbin/pvecm" >> /etc/sudoers.d/cluster

# Generate SSH keys
su - clusteradmin -c "ssh-keygen -t ed25519 -N '' -f ~/.ssh/id_ed25519"

# Distribute keys
for node in node1 node2 node3 node4; do
    ssh-copy-id clusteradmin@$node
done

# Join cluster as non-root
su - clusteradmin -c "sudo pvecm add master-node"
```

## Best Practices

### 1. Security Considerations

- **Never hardcode passwords** in scripts
- **Use temporary passwords** that are immediately revoked
- **Implement secret rotation** for any pre-shared secrets
- **Use certificate pinning** to prevent MITM attacks
- **Audit all cluster join operations**

### 2. Network Requirements

```bash
# Ensure all required ports are open
for port in 22 8006 5404 5405 111 2049 3128 83 85; do
    iptables -A INPUT -p tcp --dport $port -j ACCEPT
done

# Verify connectivity between nodes
for node in 10.10.1.21 10.10.1.22 10.10.1.23 10.10.1.24; do
    nc -zv $node 8006
done
```

### 3. Pre-Flight Checks

```bash
#!/bin/bash
# Comprehensive pre-flight check script

check_prerequisites() {
    local errors=0
    
    # Check time synchronization
    if ! timedatectl status | grep -q "synchronized: yes"; then
        echo "ERROR: Time not synchronized"
        ((errors++))
    fi
    
    # Check hostname resolution
    for node in node1 node2 node3 node4; do
        if ! getent hosts $node > /dev/null; then
            echo "ERROR: Cannot resolve $node"
            ((errors++))
        fi
    done
    
    # Check Proxmox installation
    if ! command -v pvecm > /dev/null; then
        echo "ERROR: Proxmox not installed"
        ((errors++))
    fi
    
    # Check network interfaces
    if ! ip link show vmbr0 > /dev/null 2>&1; then
        echo "ERROR: vmbr0 bridge not configured"
        ((errors++))
    fi
    
    return $errors
}
```

### 4. Post-Formation Validation

```bash
#!/bin/bash
# Validate cluster after formation

validate_cluster() {
    local expected_nodes=4
    
    # Check node count
    local actual_nodes=$(pvecm nodes | grep -c "^[[:space:]]*[0-9]")
    if [ "$actual_nodes" -ne "$expected_nodes" ]; then
        echo "ERROR: Expected $expected_nodes nodes, found $actual_nodes"
        return 1
    fi
    
    # Check quorum
    if ! pvecm status | grep -q "Quorate:[[:space:]]*Yes"; then
        echo "ERROR: Cluster does not have quorum"
        return 1
    fi
    
    # Check corosync rings
    if ! corosync-cfgtool -s | grep -q "status.*=.*OK"; then
        echo "ERROR: Corosync rings not healthy"
        return 1
    fi
    
    # Check from each node
    for node in node1 node2 node3 node4; do
        if ! ssh root@$node "pvecm status" > /dev/null 2>&1; then
            echo "ERROR: Cannot verify cluster from $node"
            return 1
        fi
    done
    
    echo "Cluster validation successful!"
    return 0
}
```

## Alternative: Kubernetes-Style Join Tokens

Implement a Kubernetes-style token-based joining:

```python
#!/usr/bin/env python3
"""
Proxmox Cluster Token Manager
Implements Kubernetes-style join tokens for Proxmox clusters
"""

import secrets
import time
import json
import hashlib
from datetime import datetime, timedelta

class ClusterTokenManager:
    def __init__(self):
        self.tokens_file = "/etc/pve/cluster-tokens.json"
        self.load_tokens()
    
    def load_tokens(self):
        try:
            with open(self.tokens_file, 'r') as f:
                self.tokens = json.load(f)
        except:
            self.tokens = {}
    
    def save_tokens(self):
        with open(self.tokens_file, 'w') as f:
            json.dump(self.tokens, f)
    
    def create_token(self, ttl_hours=24):
        """Create a new join token with TTL"""
        token = f"proxmox.{secrets.token_hex(8)}.{secrets.token_hex(16)}"
        expiry = (datetime.now() + timedelta(hours=ttl_hours)).isoformat()
        
        self.tokens[token] = {
            "created": datetime.now().isoformat(),
            "expires": expiry,
            "used": False
        }
        
        self.save_tokens()
        return token
    
    def validate_token(self, token):
        """Validate a join token"""
        if token not in self.tokens:
            return False
        
        token_data = self.tokens[token]
        
        # Check if already used
        if token_data["used"]:
            return False
        
        # Check expiry
        if datetime.fromisoformat(token_data["expires"]) < datetime.now():
            return False
        
        # Mark as used
        token_data["used"] = True
        self.save_tokens()
        
        return True
    
    def cleanup_expired(self):
        """Remove expired tokens"""
        now = datetime.now()
        expired = []
        
        for token, data in self.tokens.items():
            if datetime.fromisoformat(data["expires"]) < now:
                expired.append(token)
        
        for token in expired:
            del self.tokens[token]
        
        self.save_tokens()
```

## Deployment Workflow

### Recommended Automated Workflow:

1. **Provision nodes** via PXE/automated installation
2. **Run post-install script** on all nodes
3. **Execute cluster manager** with preferred method
4. **Validate cluster** formation
5. **Configure Ceph** storage (if needed)
6. **Set up HA** and monitoring

### Example Full Automation:

```bash
#!/bin/bash
# Complete cluster deployment

# 1. Trigger PXE boot on all nodes
./scripts/reboot-nodes-for-reprovision.sh

# 2. Wait for installation (monitor via API)
while true; do
    ready_count=$(curl -s http://10.10.1.1/api/status | jq '[.nodes[] | select(.status == "ready")] | length')
    if [ "$ready_count" -eq 4 ]; then
        break
    fi
    sleep 30
done

# 3. Form cluster
./scripts/proxmox-cluster-manager.sh --method auto

# 4. Configure Ceph
ansible-playbook ceph-setup.yml

# 5. Deploy monitoring
ansible-playbook monitoring.yml

echo "Cluster deployment complete!"
```

## Troubleshooting

### Common Issues and Solutions:

1. **SSH Key Issues**
   - Ensure unified SSH keys are deployed
   - Check `/root/.ssh/known_hosts` for conflicts
   
2. **Network Problems**
   - Verify all nodes can ping each other on both networks
   - Check firewall rules aren't blocking cluster ports
   
3. **Timing Issues**
   - Ensure NTP/chrony is synchronized
   - Allow sufficient time between operations
   
4. **Certificate Problems**
   - Regenerate certificates if needed: `pvecm updatecerts --force`
   - Clear old certificates: `rm -rf /etc/pve/nodes/*/pve-ssl.*`

## Summary

The improved cluster formation approach provides:

- **Multiple automation methods** to suit different environments
- **No interactive password prompts** during automation
- **Better error handling** and recovery
- **Security best practices** implementation
- **Validation and verification** at each step

Choose the method that best fits your security requirements and infrastructure constraints.