# Bare-Metal Provisioning Server

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Proxmox](https://img.shields.io/badge/Proxmox-9.x-orange.svg)](https://www.proxmox.com/)
[![Ubuntu](https://img.shields.io/badge/ubuntu-20.04%20%7C%2022.04%20%7C%2024.04-orange.svg)](https://ubuntu.com/)

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
- **Unified SSH Key Management**: Single key infrastructure for seamless cluster communication
- **Intelligent Cluster Formation**: Automatic cluster creation and node joining
- **High-Performance Networking**: 10Gbit Ceph network with MTU 9000 optimization
- **Dual Network Links**: Management and Ceph networks for redundancy and performance
- **Automated Repository Configuration**: Enterprise/community repository management

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
- Ubuntu 24.04 LTS (management server)
- Ansible >= 2.9
- Internet connectivity for ISO downloads

### Network Requirements
- Isolated provisioning network (recommended: 10.10.1.0/24)
- DHCP range available for target nodes
- Management server with static IP

## Quick Start

### 1. Clone Repository
```bash
git clone https://github.com/your-org/ansible-provisioning-server.git
cd ansible-provisioning-server
```

### 2. Configure Network Settings
Edit `inventory/host_vars/localhost.yml`:
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
ansible-playbook -i inventory/hosts site.yml
```

## Ubuntu Server Provisioning

### Supported Versions
- Ubuntu 24.04 LTS (default)
- Ubuntu 22.04 LTS
- Ubuntu 20.04 LTS

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
- Unified SSH key deployment from provisioning server
- Storage and backup configuration
- GRUB settings for IOMMU
- Monitoring tools installation

#### 3. Intelligent Cluster Operations
- **Node1 (Primary)**: Creates cluster with dual network links
- **Node2-4 (Secondary)**: Prepare for cluster joining with join instructions
- Automatic cluster health verification

### Manual Cluster Formation (Fallback)

If automatic cluster formation fails:
```bash
# From provisioning server
./scripts/proxmox-form-cluster.sh
```

Or from individual nodes:
```bash
# From node1 to add node2
pvecm add 10.10.1.22 --link0 10.10.1.22 --link1 10.10.2.22

# From node2 to join cluster
pvecm add 10.10.1.21 --use_ssh
```

### Cluster Configuration
- **Cluster Name**: sddc-cluster
- **Primary Link**: Management network (10.10.1.x)
- **Secondary Link**: Ceph network (10.10.2.x) for redundancy and performance
- **Migration Network**: Ceph network (10Gbit for fast VM migrations)

## Configuration

### Global Settings
Located in `inventory/group_vars/all.yml`:
- Network configuration
- OS support matrix
- Security settings

### Node-Specific Settings  
Located in `nodes.json`:
- MAC to IP mapping
- Hostname assignment
- Network interface configuration

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

### API Endpoints
- `/api/answer.php` - Dynamic Proxmox answer file generation
- `/api/register-node.php` - Node registration
- `/api/node-status.php` - Status updates
- `/api/get-ssh-keys.php` - SSH key distribution

## SSH Key Management

### Unified SSH Key System

All Proxmox nodes use the provisioning server's SSH key for seamless cluster communication.

#### Key Features
- **Single Key Infrastructure**: All nodes use `sysadmin_automation_key`
- **Seamless Communication**: Management server -> nodes, node -> node, node -> management server
- **Automatic Deployment**: Keys distributed during first boot
- **Secure Storage**: Keys copied to `/var/www/html/keys/` with proper permissions

#### API Endpoints
```bash
# Get public key
curl http://10.10.1.1/api/get-ssh-keys.php?type=management&key=public

# Get private key  
curl http://10.10.1.1/api/get-ssh-keys.php?type=management&key=private
```

#### Implementation
The post-install script downloads both private and public keys from the provisioning server, ensuring all nodes can communicate without password authentication.

## API Endpoints

### Node Registration
**POST** `/api/register-node.php`
```json
{
  "hostname": "node1",
  "ip": "10.10.1.21",
  "type": "proxmox",
  "status": "ready"
}
```

### Status Updates
**POST** `/api/node-status.php`
```json
{
  "hostname": "node1",
  "status": "installing",
  "timestamp": "2025-08-23T10:00:00Z"
}
```

### SSH Key Distribution
**GET** `/api/get-ssh-keys.php?type=management&key=public`
Returns the provisioning server's SSH public key.

**GET** `/api/get-ssh-keys.php?type=management&key=private`
Returns the provisioning server's SSH private key.

### Dynamic Configuration
**POST** `/api/answer.php`
Generates Proxmox auto-installer answer files based on requesting node's MAC address.

## Testing & Validation

### Network Configuration Test
```bash
# Test Proxmox network configuration
./scripts/test-network-config.sh
```

### Answer File Validation
```bash  
# Validate Proxmox answer file syntax
./verify_proxmox_config.sh node1
```

### SSH Connectivity Test
```bash
# Test SSH key deployment
ssh root@10.10.1.21 'ls -la /root/.ssh/id_ed25519*'

# Test inter-node communication
ssh root@10.10.1.21 'ssh root@10.10.1.22 echo "SSH-OK"'
```

### Web Interface Test
```bash
# Test web services
curl http://10.10.1.1/

# Test API endpoints
curl http://10.10.1.1/api/get-ssh-keys.php?type=management&key=public
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

#### Cluster Formation Issues
```bash
# Check cluster status
ssh root@10.10.1.21 'pvecm status'

# Check corosync rings  
ssh root@10.10.1.21 'corosync-cfgtool -s'

# Manual cluster formation log (if used)
tail -f /var/log/proxmox-cluster-formation.log
```

#### SSH Key Issues
```bash
# Check SSH key API
curl http://10.10.1.1/api/get-ssh-keys.php?type=management&key=public

# Test node-to-node SSH
ssh root@10.10.1.21 'ssh root@10.10.1.22 echo "SSH-OK"'

# Check key permissions
ls -la /var/www/html/keys/
```

### Log Files

#### System Logs
- `/var/log/nginx/error.log` - Web server errors
- `/var/log/nginx/access.log` - Web server access
- `/var/log/dnsmasq.log` - DHCP/DNS/TFTP activity

#### Provisioning Logs  
- `/var/log/proxmox-post-install.log` - Proxmox node preparation
- `/var/log/proxmox-cluster-formation.log` - Manual cluster formation
- `/var/log/ssh-key-management.log` - SSH key activity

#### Target Node Logs
- `/var/log/cloud-init.log` - Ubuntu installation
- `/var/log/installer/autoinstall-user-data` - Ubuntu autoinstall

### Performance Optimization

#### Network Performance
The system includes optimizations for high-speed networking:
- MTU 9000 on Ceph network interfaces
- TCP congestion control (BBR)
- Network buffer tuning
- Optimized sysctl parameters

#### Storage Performance
- ZFS compression and checksums enabled
- Proper disk alignment for SSDs
- I/O scheduler optimization

### Security Considerations

#### Network Security
- Isolated provisioning network
- Firewall rules for cluster communication
- SSH key-based authentication only

#### Web Security
- Input validation and sanitization
- Path traversal protection
- CSRF protection headers

### Backup and Recovery

#### Configuration Backup
```bash
# Backup provisioning server configuration
tar -czf provisioning-backup.tar.gz /etc/nginx/ /var/www/html/ nodes.json
```

#### Node Recovery
```bash
# Re-provision failed node
# Simply reboot with network boot - system will reinstall automatically
```

## File Structure

### Scripts
- `scripts/proxmox-post-install.sh` - Primary unified installation script
- `scripts/proxmox-form-cluster.sh` - Manual cluster formation script
- `scripts/test-network-config.sh` - Network configuration validation

### APIs
- `roles/web/templates/answer.php.j2` - Dynamic Proxmox answer file generator
- `roles/web/templates/get-ssh-keys.php.j2` - SSH key distribution API
- `roles/web/templates/register-node.php.j2` - Node registration API
- `roles/web/templates/node-status.php.j2` - Status update API

### Configuration
- `nodes.json` - Node inventory and configuration
- `inventory/group_vars/all.yml` - Global configuration
- `inventory/host_vars/localhost.yml` - Server-specific settings

### Web Assets
- `roles/web/templates/index.php.j2` - Main dashboard
- `roles/web/templates/nginx.conf.j2` - Web server configuration

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

## Support

### Documentation
- All documentation is maintained in this README
- Check troubleshooting section for common issues
- Review log files for detailed error information

### Community Support
- GitHub Issues for bug reports
- GitHub Discussions for questions
- Pull Requests for contributions

### Enterprise Support
Contact the maintainers for enterprise support options.

---

This unified provisioning system provides a complete solution for bare-metal server and Proxmox VE cluster deployment with enterprise-grade features, security, and performance optimization.