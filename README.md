# Bare-Metal Provisioning Server

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Proxmox](https://img.shields.io/badge/Proxmox-9.x-orange.svg)](https://www.proxmox.com/)
[![Ubuntu](https://img.shields.io/badge/ubuntu-24.04%20LTS%20only-orange.svg)](https://ubuntu.com/)

Enterprise-grade bare-metal provisioning infrastructure for Ubuntu servers and Proxmox VE 9 clusters.

A comprehensive automation solution that deploys and manages a provisioning infrastructure for zero-touch deployment of Ubuntu servers and Proxmox VE 9 clusters on bare-metal hardware using iPXE, cloud-init, and automated cluster formation via the Proxmox API.

## Table of Contents

- [Overview](#overview)
- [Features](#features)
- [Architecture](#architecture)
- [Prerequisites](#prerequisites)
- [Quick Start](#quick-start)
- [Ubuntu Server Provisioning](#ubuntu-server-provisioning)
- [Proxmox VE Cluster Provisioning](#proxmox-ve-cluster-provisioning)
- [Configuration](#configuration)
- [Web Management Interface](#web-management-interface)
- [SSH Key Management](#ssh-key-management)
- [API Endpoints](#api-endpoints)
- [Testing & Validation](#testing--validation)
- [Troubleshooting](#troubleshooting)
- [Contributing](#contributing)
- [License](#license)

## Overview

The Bare-Metal Provisioning Server automates the deployment of a complete provisioning infrastructure, enabling organizations to perform zero-touch installations of Ubuntu servers and Proxmox VE 9 clusters at scale. This solution combines industry-standard technologies including DHCP, DNS, TFTP, iPXE, and cloud-init to provide a robust, enterprise-ready provisioning platform.

**Note**: Ansible is used only for initial setup of the provisioning server infrastructure. Proxmox node configuration is handled by self-contained Python and shell scripts that run directly on the nodes after installation.

## Features

### Core Infrastructure Services
- **Network Services**: Integrated DHCP, DNS, and TFTP server using dnsmasq
- **Boot Management**: iPXE-based network booting with EFI support
- **Cloud-Init Integration**: Automated Ubuntu server configuration via autoinstall
- **Web Dashboard**: Real-time provisioning status monitoring and management
- **Hardware Management**: Redfish API integration for server power and boot control
- **Multi-OS Support**: Ubuntu 24.04 and Proxmox VE 9 automated installation

### Enterprise Capabilities  
- **Security**: Hardened input validation, path sanitization, and encrypted credential management
- **Scalability**: Multi-node provisioning with dynamic network interface detection
- **Flexibility**: Support for multiple Ubuntu versions and hardware platforms
- **Observability**: Comprehensive logging, health monitoring, and status tracking

### Proxmox VE Cluster Features
- **API-Driven Cluster Formation**: Reliable, sequential cluster creation and node joining using the Proxmox API.
- **Enhanced Reprovision Workflow**: Complete automated reprovision with monitoring and cluster formation
- **High-Performance Networking**: 10Gbit Ceph network with MTU 9000 optimization
- **Dual Network Links**: Management and Ceph networks for redundancy and performance
- **Automated Repository Configuration**: Enterprise/community repository management
- **VM Template Management**: Create and manage Proxmox VM templates

## Architecture

### Network Design
```
Internet -> WAN (enp1s0) -> NAT -> Provisioning Network (10.10.1.0/24)
                                  |
                                  +-- Management Server (10.10.1.1)
                                  +-- Node1 (10.10.1.21) <-> Ceph Network (10.10.2.21)
                                  +-- Node2 (10.10.1.22) <-> Ceph Network (10.10.2.22) 
                                  +-- Node3 (10.10.1.23) <-> Ceph Network (10.10.2.23)
                                  +-- Node4 (10.10.1.24) <-> Ceph Network (10.10.2.24)
```

### Service Stack
- **Base OS**: Ubuntu 24.04 LTS
- **Web Server**: Nginx + PHP-FPM
- **Network Services**: dnsmasq (DHCP/DNS/TFTP)
- **Boot Loader**: iPXE with EFI support
- **Configuration Management**: cloud-init/autoinstall
- **Monitoring**: Node Exporter + Health checks

## Prerequisites

### Hardware Requirements
- **Management Server**: 2 CPU cores, 4GB RAM, 50GB storage
- **Network**: Dedicated provisioning VLAN/network
- **Target Nodes**: UEFI boot, network boot capability

### Software Requirements
- Ubuntu 24.04 LTS (management server) - **Required**, other versions not supported
- Ansible >= 2.9
- Internet connectivity for ISO downloads

### Network Requirements
- Isolated provisioning network (recommended: 10.10.1.0/24)
- DHCP range available for target nodes
- Management server with static IP

## Quick Start

### 1. Clone Repository
```bash
git clone https://github.com/sddcinfo/ansible-provisioning-server.git
cd ansible-provisioning-server
```

### 2. Configure Network Settings
Edit `group_vars/all/main.yml`:
```yaml
# Network configuration
external_interface: "enp1s0"  # WAN interface
provisioning_network: "10.10.1.0/24"
server_ip: "10.10.1.1"
dhcp_range_start: "10.10.1.100"
dhcp_range_end: "10.10.1.199"
gateway_ip: "10.10.1.1"
```

### 3. Configure Node Inventory
Edit `nodes.json`:
```json
{
  "nodes": [
    {
      "mac": "ac:1f:6b:6c:5a:76",
      "os_ip": "10.10.1.21",
      "os_hostname": "node1",
      "ceph_ip": "10.10.2.21"
    }
  ]
}
```

### 4. Deploy Infrastructure
```bash
ansible-playbook -i inventory site.yml
```

## Ubuntu Server Provisioning

### Supported Versions
- Ubuntu 24.04 LTS (only supported version)

### Node Configuration
Nodes are configured via the `nodes.json` file with MAC address to IP mapping.

### Installation Process
1. Node boots via PXE/UEFI
2. iPXE loads and contacts provisioning server
3. Autoinstall configuration is generated dynamically
4. Ubuntu installs with cloud-init configuration
5. Post-install hooks configure services

## Proxmox VE Cluster Provisioning

### Unified Cluster Setup

The system uses a comprehensive approach that handles both node preparation and intelligent cluster formation automatically.

### Network Architecture
- **Management Network**: vmbr0 - 10.10.1.x/24 (default route, broadcast 10.10.1.255)
- **Ceph Storage Network**: vmbr1 - 10.10.2.x/24 (MTU 9000, bridged to eno3, broadcast 10.10.2.255)
- **Physical Interface**: eno3 - Bridge member (10Gbit, MTU 9000, no IP)
- **Routing**: Management handles internet, Ceph handles storage (no default route)

### Automatic Installation Process

#### 1. Boot and Initial Configuration
- Node boots with Proxmox auto-installer ISO
- `answer.php` generates node-specific configuration
- First boot automatically runs `proxmox-post-install.sh`

#### 2. Node Preparation
The unified script performs:
- Repository configuration (disables enterprise repos, adds no-subscription)
- Package installation and system updates
- High-performance network configuration (Ceph network with MTU 9000)
- Proper broadcast address configuration for both networks
- Performance tuning for high-speed networks
- Storage and backup configuration
- GRUB settings for IOMMU
- Monitoring tools installation

### Cluster Formation

#### Automated Reprovision Workflow (Recommended)

The enhanced reprovision workflow provides complete end-to-end automation from reprovision trigger to cluster formation:

```bash
# Complete automated reprovision and cluster formation
./scripts/coordinated-proxmox-reprovision.py

# With custom timeout (90 minutes)  
./scripts/coordinated-proxmox-reprovision.py --timeout 90
```

**Features:**
- Automatically reprovisioners all nodes
- Monitors reprovision progress in real-time
- Waits for ALL nodes to complete before cluster formation
- Triggers cluster formation automatically when ready
- Comprehensive logging and error handling
- No manual intervention required

#### Manual Cluster Formation

For manual cluster formation, the process is handled by a Python script that runs on the management server and uses the Proxmox API:

```bash
./scripts/proxmox-form-cluster.py
```

The script will:
1.  Check the status of all nodes.
2.  Create the cluster on the primary node (`node1`).
3.  Join the remaining nodes to the cluster one by one.
4.  Verify that each node has successfully joined the cluster.

#### Manual Fallback
If the automated script fails, you can still form the cluster manually using `pvecm` commands from the nodes themselves.

From `node1` to add `node2`:
```bash
pvecm add 10.10.1.22 --link0 10.10.1.22 --link1 10.10.2.22
```

From `node2` to join the cluster:
```bash
pvecm add 10.10.1.21 --use_ssh
```

### Cluster Configuration
- **Cluster Name**: sddc-cluster
- **Primary Link**: Management network (10.10.1.x)
- **Secondary Link**: Ceph network (10.10.2.x) for redundancy and performance
- **Migration Network**: Ceph network (10Gbit for fast VM migrations)

## Configuration

### Global Settings
Located in `group_vars/all/main.yml`:
- Network configuration
- OS support matrix
- SSH key settings
- Security settings

### Node-Specific Settings  
Located in `nodes.json`:
- MAC to IP mapping
- Hostname assignment
- Network interface configuration

### Environment Variables
Several scripts support environment variables for customization:
- `PROXMOX_ROOT_PASSWORD` - Proxmox root password (default: 'proxmox123')
- `CLUSTER_NAME` - Proxmox cluster name (default: 'sddc-cluster')  
- `NODE_CONFIG_FILE` - Path to custom node configuration JSON file

Example usage:
```bash
# Set custom password for cluster formation
export PROXMOX_ROOT_PASSWORD="secure-password"
python3 scripts/proxmox-form-cluster.py

# Use custom node configuration
export NODE_CONFIG_FILE="/path/to/custom-nodes.json"
python3 scripts/cluster-status-summary.py
```

### Web Interface Customization
- Modify templates in `roles/web/templates/`
- Custom styling in web assets
- API endpoint configuration

## Web Management Interface

Access the web interface at `http://10.10.1.1` (or your configured server IP).

### Features
- Real-time node status monitoring
- Installation progress tracking
- Log file access
- Redfish power management
- Network boot configuration

## SSH Key Management

The system provides **automated SSH key management** to ensure consistent access across all nodes:

### Automatic Key Generation and Deployment
- SSH keys are automatically generated during ansible deployment if they don't exist
- Management server's SSH key is automatically embedded in both `nodes.json` and `group_vars/all/main.yml`
- Keys are deployed to all Proxmox nodes during installation via the answer file

### Key Usage
- **Initial Access**: SSH keys provide access to nodes after installation
- **Cluster Operations**: Primarily handled through Proxmox API using `root@pam` authentication  
- **Emergency Fallback**: SSH is used as fallback if API becomes unresponsive
- **Inter-node Communication**: Proxmox automatically manages keys for migrations and cluster operations

### Manual Key Verification
```bash
# Test SSH connectivity to nodes
ssh -i ~/.ssh/sysadmin_automation_key root@node1

# Verify key deployment
ssh root@10.10.1.21 'cat /root/.ssh/authorized_keys'
```

## API Endpoints

The provisioning server exposes several API endpoints to facilitate the automated installation process.

- `/api/answer.php` - Dynamic Proxmox answer file generation.
- `/api/register-node.php` - Used by nodes to register themselves with the provisioning server after installation.
- `/api/node-status.php` - Used to update the status of a node during the provisioning process.

## Testing & Validation

### Network Configuration Test
```bash
# Test infrastructure validation
sudo ./verify_provisioning.py
```

### Answer File Validation
```bash  
# Validate Proxmox answer file syntax
./scripts/verify_proxmox_config.sh node1
```

### SSH Connectivity Test
```bash
# Test SSH key deployment
ssh root@10.10.1.21 'ls -la /root/.ssh/id_ed25519*'
```

### Web Interface Test
```bash
# Test web services
curl http://10.10.1.1/

# Test API endpoints
curl http://10.10.1.1/api/register-node.php
```

## Troubleshooting

### Common Issues

#### SSH Connection Timeouts During Setup
The post-install scripts include comprehensive timeout protection to prevent hanging:
- SSH commands use `timeout` wrapper and `BatchMode=yes` to prevent interactive prompts
- ConnectTimeout and ServerAliveInterval settings prevent indefinite waiting
- If connectivity tests fail, they're logged as INFO/WARN rather than causing script failure
- Normal during initial setup when other nodes haven't completed installation yet

#### Ubuntu Installation Problems
```bash
# Check web server logs
sudo tail -f /var/log/nginx/error.log

# Check cloud-init logs on target node
tail -f /var/log/cloud-init.log
```

#### Network Boot Issues
```bash
# Check TFTP service
sudo systemctl status dnsmasq

# Test TFTP connectivity
tftp 10.10.1.1 -c get ipxe.efi
```

#### Proxmox Installation Issues
```bash
# Check primary installation log
tail -f /var/log/proxmox-post-install.log

# Verify SSH key deployment
ssh root@10.10.1.21 'ls -la /root/.ssh/id_ed25519*'

# Check network configuration  
ssh root@10.10.1.21 'ip addr show vmbr0 | grep brd'  # Should show 10.10.1.255
ssh root@10.10.1.21 'ip addr show vmbr1 | grep brd'  # Should show 10.10.2.255

# Test Ceph network connectivity
ping 10.10.2.21  # from any node
```

## VM Template Management

The provisioning server includes a template management system for creating Proxmox VM templates.

### Initial Setup

Before creating templates, set up the configuration:

```bash
# Setup shared configuration (creates ~/proxmox-config/templates.yaml)
./scripts/bootstrap-config.sh

# Customize the configuration as needed
nano ~/proxmox-config/templates.yaml
```

### Creating Templates

```bash
# Create base template
python3 scripts/template-manager.py --create-templates

# Verify template is properly configured
python3 scripts/template-manager.py --verify

# Test template by cloning and booting
python3 scripts/template-manager.py --test-templates

# Force recreate template even if it exists
python3 scripts/template-manager.py --create-templates --force

# Clean up template
python3 scripts/template-manager.py --remove-all --yes
```

### Template Configuration

Templates are configured in `~/proxmox-config/templates.yaml` (created by bootstrap script):
- Template ID 9000: Ubuntu 24.04 base template with cloud-init

#### Cluster Formation Issues
```bash
# Check cluster status
ssh root@10.10.1.21 'pvecm status'

# Check corosync rings  
ssh root@10.10.1.21 'corosync-cfgtool -s'

# Manual cluster formation log (if used)
tail -f /var/log/proxmox-cluster-formation.log
```

## File Structure

### Scripts
- `scripts/coordinated-proxmox-reprovision.py` - Complete automated reprovision workflow with monitoring and cluster formation
- `scripts/enhanced-reprovision-monitor.py` - Monitors reprovision progress and triggers automatic cluster formation  
- `scripts/template-manager.py` - Manages Proxmox VM templates
- `scripts/proxmox-ceph-setup.py` - Automates Proxmox and Ceph configuration
- `scripts/proxmox-post-install.sh` - Primary unified installation script that runs on each node after Proxmox is installed
- `scripts/proxmox-form-cluster.py` - The primary script for forming the Proxmox cluster, run from the management server
- `scripts/reboot-nodes-for-reprovision.py` - Reboots nodes to trigger fresh provisioning
- `scripts/cluster-status-summary.py` - Displays comprehensive cluster and Ceph status
- `scripts/verify_proxmox_config.sh` - Validates Proxmox answer file configuration
- `scripts/update-templates.sh` - Regenerates web templates and tests API endpoints

### APIs
- `roles/web/templates/answer.php.j2` - Dynamic Proxmox answer file generator.
- `roles/web/templates/register-node.php.j2` - Node registration API.
- `roles/web/templates/node-status.php.j2` - Status update API.

### Configuration
- `nodes.json` - Node inventory and configuration
- `group_vars/all/main.yml` - Global configuration and settings
- `inventory` - Ansible inventory file
- `verify_provisioning.py` - Infrastructure validation script (generated by ansible)

### Web Assets
- `roles/web/templates/index.php.j2` - Main dashboard.
- `roles/web/templates/nginx.conf.j2` - Web server configuration.

## Contributing

### Development Setup
1. Fork the repository
2. Create feature branch
3. Test changes thoroughly
4. Submit pull request

### Code Standards
- Follow existing code style
- Include comprehensive testing
- Document all changes
- Remove debug code before committing

### Testing Guidelines
- Test on clean Ubuntu 24.04 installation
- Verify both Ubuntu and Proxmox provisioning
- Test network configurations
- Validate security implementations

## License

MIT License - see LICENSE file for details.