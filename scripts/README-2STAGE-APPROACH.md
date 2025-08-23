# Proxmox 2-Stage Cluster Setup

This approach splits the Proxmox cluster setup into two distinct stages to handle all the issues discovered during manual configuration.

## Stage 1: Node Preparation (Individual Nodes)

Run `proxmox-prepare-node.sh` on each node during the post-build process.

### What Stage 1 Does:
- ✅ Fixes repository configuration (disables enterprise repos, adds no-subscription)
- ✅ Installs all required packages 
- ✅ Configures Ceph network on eno3 (10Gbit, MTU 9000)
- ✅ Sets up performance tuning for high-speed networks
- ✅ Generates and distributes SSH keys via provisioning server
- ✅ Configures storage directories and backup schedules
- ✅ Applies GRUB settings for IOMMU
- ✅ Installs monitoring tools
- ❌ **NO cluster operations** - nodes remain standalone

### Network Configuration:
- **Management**: vmbr0 (existing) - 10.10.1.x/24
- **Ceph Storage**: eno3 - 10.10.2.2x/24 (MTU 9000, 10Gbit)  
- **Ceph Bridge**: vmbr1 - 10.10.2.10x/24 (for VM access)

### SSH Key Management:
- Each node generates SSH keys
- Keys are collected via `/api/collect-ssh-key.php`
- All nodes retrieve complete key set via `/api/get-ssh-keys.php`
- Enables passwordless SSH between all nodes

## Stage 2: Cluster Formation (After All Nodes Ready)

Run `proxmox-form-cluster.sh` from the provisioning server after all nodes complete Stage 1.

### What Stage 2 Does:
- ✅ Verifies all nodes are prepared and accessible
- ✅ Checks existing cluster status
- ✅ Creates cluster on node1 with dual network links
- ✅ Joins remaining nodes using SSH keys
- ✅ Configures cluster to use Ceph network for communication
- ✅ Verifies cluster health and quorum
- ✅ Updates node registration status

### Cluster Configuration:
- **Cluster Name**: sddc-cluster
- **Primary Link**: Management network (10.10.1.x)
- **Secondary Link**: Ceph network (10.10.2.x) - for redundancy and performance
- **Migration Network**: Ceph network (10Gbit for fast VM migrations)

## Usage Instructions

### 1. Deploy Stage 1 Script
```bash
# Copy to all nodes during post-build
scp proxmox-prepare-node.sh root@node1:/tmp/
scp proxmox-prepare-node.sh root@node2:/tmp/
scp proxmox-prepare-node.sh root@node3:/tmp/
scp proxmox-prepare-node.sh root@node4:/tmp/

# Run on each node
ssh root@node1 'bash /tmp/proxmox-prepare-node.sh'
ssh root@node2 'bash /tmp/proxmox-prepare-node.sh'  
ssh root@node3 'bash /tmp/proxmox-prepare-node.sh'
ssh root@node4 'bash /tmp/proxmox-prepare-node.sh'
```

### 2. Verify Node Preparation
```bash
# Check preparation status
for i in {1..4}; do
    echo "=== Node$i Status ==="
    ssh root@10.10.1.2$i 'test -f /var/lib/proxmox-node-prepared.done && echo "PREPARED" || echo "NOT READY"'
    ssh root@10.10.1.2$i 'ip addr show eno3 | grep "inet 10.10.2"'
    ssh root@10.10.1.2$i 'ip link show eno3 | grep mtu'
done
```

### 3. Form Cluster
```bash
# Run cluster formation (after all nodes prepared)
./proxmox-form-cluster.sh
```

## Key Improvements Over Single Script

### 🚀 **Stage 1 Benefits:**
1. **Repository Issues Fixed** - Properly handles both .list and .sources formats
2. **SSH Key Distribution** - Automated bidirectional key exchange
3. **Network Performance** - 10Gbit Ceph network with MTU 9000
4. **No Clustering Conflicts** - Each node prepared independently
5. **Better Error Handling** - Script addresses all manually discovered issues

### 🚀 **Stage 2 Benefits:**
1. **Verified Prerequisites** - Ensures all nodes ready before clustering
2. **Dual Network Links** - Management + Ceph for redundancy
3. **Proper Join Process** - Uses SSH keys, handles join failures
4. **Health Verification** - Confirms cluster formation success
5. **Performance Configuration** - Uses Ceph network for migrations

## Files Created

### Scripts:
- `proxmox-prepare-node.sh` - Stage 1 node preparation
- `proxmox-form-cluster.sh` - Stage 2 cluster formation

### APIs (Web Role Templates):
- `collect-ssh-key.php.j2` - SSH key collection endpoint
- `get-ssh-keys.php.j2` - SSH key retrieval endpoint

### Log Files:
- `/var/log/proxmox-prepare-node.log` - Stage 1 logs
- `/var/log/proxmox-cluster-formation.log` - Stage 2 logs  
- `/var/log/ssh-key-management.log` - SSH key activity

### Marker Files:
- `/var/lib/proxmox-node-prepared.done` - Stage 1 completion
- `/var/lib/proxmox-node-stage` - Current stage status

## Troubleshooting

### Stage 1 Issues:
```bash
# Check preparation log
tail -f /var/log/proxmox-prepare-node.log

# Verify SSH keys
curl -s http://10.10.1.1/api/get-ssh-keys.php?type=proxmox | jq

# Check Ceph network
ip addr show eno3
ping 10.10.2.21  # from any node
```

### Stage 2 Issues:
```bash  
# Check cluster formation log
tail -f /var/log/proxmox-cluster-formation.log

# Manual cluster status check
ssh root@10.10.1.21 'pvecm status'

# Check corosync rings
ssh root@10.10.1.21 'corosync-cfgtool -s'
```

This 2-stage approach ensures reliable, automated Proxmox cluster deployment with proper high-performance networking and error handling.