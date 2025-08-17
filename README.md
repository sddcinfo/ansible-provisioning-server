# Ansible Provisioning Server

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Ansible](https://img.shields.io/badge/ansible-%3E%3D2.9-blue.svg)](https://www.ansible.com/)
[![Ubuntu](https://img.shields.io/badge/ubuntu-20.04%20%7C%2022.04%20%7C%2024.04-orange.svg)](https://ubuntu.com/)

> **Enterprise-grade bare-metal provisioning infrastructure for Ubuntu servers**

An Ansible-based automation solution that deploys and manages a comprehensive provisioning infrastructure for zero-touch deployment of Ubuntu servers on bare-metal hardware using iPXE and cloud-init technologies.

---

## Table of Contents

- [Overview](#overview)
- [Features](#features)
- [Architecture](#architecture)
- [Prerequisites](#prerequisites)
- [Quick Start](#quick-start)
- [Configuration](#configuration)
- [Usage](#usage)
- [Web Management Interface](#web-management-interface)
- [Testing & Validation](#testing--validation)
- [Customization](#customization)
- [Troubleshooting](#troubleshooting)
- [Contributing](#contributing)
- [License](#license)
- [Support](#support)

## Overview

The Ansible Provisioning Server automates the deployment of a complete bare-metal provisioning infrastructure, enabling organizations to perform zero-touch installations of Ubuntu servers at scale. This solution combines industry-standard technologies including DHCP, DNS, TFTP, iPXE, and cloud-init to provide a robust, enterprise-ready provisioning platform.

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
- **Performance**: Optimized for high-throughput with systemd resource limits
- **Reliability**: Automated error handling, service recovery, and rollback mechanisms
- **Monitoring**: Automated health checks with service status verification
- **Validation**: End-to-end system validation with comprehensive verification checks

## Architecture

```mermaid
graph TB
    Internet([Internet])
    PS[Provisioning Server<br/>10.10.1.1]
    MN[Management Network<br/>10.10.1.0/24]
    KN[Kubernetes Network<br/>10.10.1.0/24]
    CN[Ceph Network<br/>10.10.2.0/24]
    
    N1[console-node1<br/>10.10.1.11]
    N2[console-node2<br/>10.10.1.12]
    N3[console-node3<br/>10.10.1.13]
    N4[console-node4<br/>10.10.1.14]
    
    Internet --> PS
    PS --> MN
    MN --> N1
    MN --> N2
    MN --> N3
    MN --> N4
    PS --> KN
    PS --> CN
```

### System Components

| Component | Technology | Purpose |
|-----------|------------|---------|
| **DHCP Server** | dnsmasq | IP address allocation and PXE boot orchestration |
| **DNS Server** | dnsmasq | Name resolution for provisioning network |
| **TFTP Server** | dnsmasq with integrated TFTP | iPXE bootloader distribution |
| **Web Server** | nginx + PHP-FPM | Hardened autoinstall hosting and dashboard |
| **NAT Gateway** | iptables + dynamic detection | Internet connectivity with interface auto-detection |
| **Management API** | Python/Redfish | Hardware control and monitoring |
| **Health Monitor** | systemd timers | Automated service health checks and recovery |
| **Validation Engine** | Ansible tasks | End-to-end system verification and testing |
| **Multi-OS Manager** | Ansible/Templates | Ubuntu and Proxmox automated provisioning |

## Prerequisites

### System Requirements

| Resource | Minimum | Recommended |
|----------|---------|-------------|
| **Operating System** | Ubuntu 20.04 LTS | Ubuntu 24.04 LTS |
| **Memory** | 4 GB RAM | 8 GB RAM |
| **Storage** | 50 GB | 100 GB SSD |
| **Network** | 2x GbE interfaces | 2x 10GbE interfaces |
| **Python** | 3.8+ | 3.10+ |
| **Ansible** | 2.9+ | 6.0+ |

### Network Infrastructure

- **Management Network**: Dedicated VLAN for provisioning operations (default: 10.10.1.0/24)
- **Internet Connectivity**: Required for package downloads and external services
- **IPMI/BMC Access**: Network reachability to target server management interfaces
- **Firewall Configuration**: DHCP (67/68), TFTP (69), HTTP (80), HTTPS (443) ports

## Quick Start

### 1. Environment Preparation

```bash
# System update and dependency installation
sudo apt update && sudo apt upgrade -y
sudo apt install -y ansible git python3-pip curl wget

# Repository cloning
git clone https://github.com/sddcinfo/ansible-provisioning-server.git
cd ansible-provisioning-server
```

### 2. Configuration Setup

```bash
# Node inventory configuration
cp nodes.json.example nodes.json
${EDITOR:-nano} nodes.json

# Configure Redfish/BMC credentials for hardware management
cp .redfish_credentials.example .redfish_credentials
${EDITOR:-nano} .redfish_credentials
chmod 600 .redfish_credentials
```

### 3. Infrastructure Deployment

```bash
# Core provisioning infrastructure with validation
sudo ansible-playbook site.yml 
# Target server boot configuration
sudo ansible-playbook set_boot_order.yml --limit console-node1
```

### 4. Deployment Verification

```bash
# Service health validation (automated during deployment)
sudo systemctl status dnsmasq nginx php8.3-fpm

# Health monitoring status
sudo systemctl status provisioning-health-check.timer

# Web interface connectivity
curl -I http://localhost

# Hardware management verification
./redfish.py console-node1 sensors

# View health monitoring logs
sudo journalctl -u provisioning-health-check.service -f
```

## Configuration

### Node Inventory Schema

The `nodes.json` file serves as the single source of truth for infrastructure topology:

```json
{
  "console_nodes": [
    {
      "hostname": "console-node1",
      "ip": "10.10.1.11",
      "mac": "aa:bb:cc:dd:ee:ff",
      "k8s_ip": "10.10.1.21",
      "ceph_ip": "10.10.2.21"
    }
  ]
}
```

#### Field Specifications

| Field | Type | Description | Validation |
|-------|------|-------------|------------|
| `hostname` | string | Unique node identifier | Must match Ansible inventory |
| `ip` | IPv4 | Management network address | Within provisioning subnet |
| `mac` | MAC | Network interface identifier | Lowercase, colon-separated |
| `k8s_ip` | IPv4 | Kubernetes cluster address | Unique within cluster subnet |
| `ceph_ip` | IPv4 | Storage network address | Unique within storage subnet |

### Credential Management

#### Ansible Inventory Credentials
Credentials can be configured directly in inventory files:

```yaml
# In inventory or group_vars files
---
ipmi_user: "admin"
ipmi_pass: "secure_password"
```

#### Redfish/BMC Credentials
For the redfish.py script, configure BMC credentials:

```bash
# Copy example and configure
cp .redfish_credentials.example .redfish_credentials
chmod 600 .redfish_credentials

# Edit with your BMC credentials
# Format: REDFISH_AUTH="username:password"
echo 'REDFISH_AUTH="admin:your_bmc_password"' > .redfish_credentials
```

**Security Best Practices:**
- Use strong, unique passwords for all accounts
- Rotate credentials regularly
- Restrict file permissions (600) for sensitive files
- Never commit unencrypted credentials to version control

### Enterprise Security Features

This solution implements comprehensive security hardening:

**Input Validation & Sanitization:**
- Strict MAC address format validation
- Path traversal attack prevention
- Parameter whitelist validation
- Command injection protection

**Error Handling:**
- No sensitive information in error messages
- Comprehensive logging without data exposure
- Graceful failure handling with rollback

**File System Security:**
- Secure directory permissions (0750)
- Safe file operations with verification
- Protected session directory cleanup

### Automated Monitoring & Health Checks

**Health Monitoring System:**
```bash
# View current health status
sudo systemctl status provisioning-health-check.timer

# Check recent health check results
sudo tail -f /var/log/provisioning_health.log

# Manual health check execution
sudo /usr/local/bin/monitoring/health_check.sh
```

**Monitoring Features:**
- **Service Recovery**: Automatic restart of failed services
- **Resource Monitoring**: Disk usage and memory alerts
- **Network Validation**: Connectivity checks every 5 minutes
- **Log Management**: Automatic log rotation (30 days retention)

**Performance Limits:**
- systemd resource limits for all critical services
- File handle limits: 65536 per service
- Process limits: 4096 per service

## Usage

### Primary Playbooks

#### Infrastructure Deployment

The playbook supports comprehensive tagging for efficient component-based execution instead of running the entire expensive playbook.

**Full Initial Deployment:**
```bash
# Complete provisioning server setup (first time)
sudo ansible-playbook site.yml
```

**Tag-Based Execution:**
```bash
# Quick service restarts and validation
sudo ansible-playbook site.yml --tags "quick,maintenance"

# Update configurations only (no expensive operations)
sudo ansible-playbook site.yml --tags "config"

# Run validation and health checks
sudo ansible-playbook site.yml --tags "validation,maintenance"

# Skip expensive operations (ISO downloads, system upgrades)
sudo ansible-playbook site.yml --skip-tags "expensive"
```

#### Tag Strategy Overview

| Tag Category | Tags | Purpose | Frequency |
|--------------|------|---------|-----------|
| **Execution Frequency** | `expensive`, `initial`, `config`, `maintenance`, `quick` | Control when tasks run | Based on operation cost |
| **Component-Based** | `dns_dhcp`, `web_services`, `autoinstall`, `iso_management` | Target specific components | By functional area |
| **Service-Specific** | `dnsmasq_config`, `nginx_config`, `service_restart` | Precise service control | For targeted updates |

#### Execution Frequency Tags

| Tag | Purpose | Frequency | Examples |
|-----|---------|-----------|----------|
| `expensive` | Resource intensive operations | Monthly/Quarterly | ISO downloads (~6GB), system upgrades |
| `initial` | One-time setup | Once per deployment | SSH keys, directory creation, package installation |
| `config` | Configuration updates | When configs change | Template updates, service configurations |
| `maintenance` | Regular validation | Daily/Weekly | Health checks, service validation |
| `quick` | Fast operations | Anytime | Service restarts, permission fixes |

#### Component-Based Tags

| Tag | Components | Usage |
|-----|------------|-------|
| `packages` | Package management, system upgrades | `--tags "packages,initial"` |
| `network` | Bridge setup, NAT rules, interface config | `--tags "network,config"` |
| `dns_dhcp` | dnsmasq configuration, DNS/DHCP services | `--tags "dns_dhcp,config"` |
| `web_services` | nginx, PHP-FPM, web content | `--tags "web_services,config"` |
| `autoinstall` | OS provisioning configurations | `--tags "autoinstall,templates"` |
| `iso_management` | ISO downloads, extraction | `--tags "iso_management" --skip-tags "expensive"` |
| `validation` | Health checks, service validation | `--tags "validation,quick"` |

#### Common Usage Scenarios

**Daily Operations:**
```bash
# Quick health check and service validation
sudo ansible-playbook site.yml --tags "quick,validation"

# Update autoinstall configurations for new nodes
sudo ansible-playbook site.yml --tags "autoinstall,config"

# Restart services after configuration changes
sudo ansible-playbook site.yml --tags "service_restart,quick"
```

**Configuration Management:**
```bash
# Update DNS/DHCP settings
sudo ansible-playbook site.yml --tags "dns_dhcp,config"

# Update web services configuration
sudo ansible-playbook site.yml --tags "web_services,config"

# Update all configurations without expensive operations
sudo ansible-playbook site.yml --tags "config" --skip-tags "expensive"
```

**Maintenance Operations:**
```bash
# Run all validation and health checks
sudo ansible-playbook site.yml --tags "maintenance,validation"

# Update system packages (expensive - run monthly)
sudo ansible-playbook site.yml --tags "expensive,system_upgrade"

# Download and process ISOs (expensive - run when needed)
sudo ansible-playbook site.yml --tags "iso_management,expensive"
```

**Performance Optimization:**
```bash
# Run everything except expensive operations (saves hours)
sudo ansible-playbook site.yml --skip-tags "expensive"

# Target specific role for faster execution
sudo ansible-playbook site.yml --tags "netboot,config,maintenance"

# Quick service recovery after issues
sudo ansible-playbook site.yml --tags "service_restart,validation"
```

#### Legacy Tag Compatibility

The playbook maintains backward compatibility with existing tags:
- `common`, `packages`, `ssh_setup`, `network_setup`, `docker_setup`
- `network_config`, `netboot`, `pxe_setup`  
- `web`, `web_setup`, `autoinstall_templates`
- `iso_preparation`, `iso_processing`
- `validation`

#### Hardware Management
```bash
# Boot order configuration (per node)
sudo ansible-playbook set_boot_order.yml --limit <hostname>

# Bulk boot configuration
sudo ansible-playbook set_boot_order.yml

### Multi-OS Provisioning Support

#### Supported Operating Systems

**Ubuntu 24.04 LTS:**
- Cloud-init autoinstall configuration
- Network-based installation via HTTP
- Automated user and SSH key setup
- Package installation and configuration

**Proxmox VE 9:**
- TOML-based answer file configuration  
- ZFS storage setup with configurable options
- Network boot with HTTP-served installation media
- Post-installation automation hooks
- iPXE EFI network boot support with auto-installer

#### Configuration Management
```bash
# Test multi-OS configuration
./test-multi-os.sh

# View OS-specific templates
ls roles/web/templates/*ubuntu* roles/web/templates/*proxmox*

# Check provisioning directories
ls -la /var/www/html/provisioning/

# Verify Proxmox iPXE configuration
/mnt/verify_proxmox_config.sh
```

## Proxmox VE 9.0 iPXE Network Boot

### Overview
This server provides complete support for iPXE-based EFI network boot installation of Proxmox VE 9.0, including automated configuration discovery and ZFS storage setup.

### Key Features
- **Zero-touch installation** via iPXE EFI network boot
- **Auto-installer activation** with proper kernel parameters
- **Multiple configuration discovery methods** (embedded, HTTP, DHCP)
- **ZFS storage configuration** with automated disk setup
- **Post-installation callbacks** for status tracking

### Critical Requirements

#### Kernel Parameters
Proxmox 9.0 requires specific kernel parameters for auto-installer activation:
```
ro ramdisk_size=16777216 rw quiet splash=silent proxmox-start-auto-installer
```

#### Answer File Format (TOML)
Proxmox 9.0 uses kebab-case field names in TOML format:
```toml
[global]
keyboard = "us"
country = "US"
fqdn = "pve-node.local"
mailto = "admin@local"
timezone = "UTC"
root-password = "proxmox"

[network]
source = "from-dhcp"

[disk-setup]
filesystem = "zfs"
disk_list = ["sda"]
zfs.raid = "raid0"
zfs.compress = "on"
zfs.checksum = "on"
zfs.copies = 1
zfs.hdsize = 32
```

#### initrd Requirements
The initrd must contain:
- **ISO file named exactly `proxmox.iso`** (critical for auto-installer recognition)
- **answer.toml in `/proxmox-auto-installer/` directory**
- **Proper TOML format with kebab-case field names**

### Configuration Discovery Methods

The Proxmox installation supports multiple fallback mechanisms:

1. **Embedded Configuration** (Primary)
   - answer.toml embedded directly in initrd
   - ISO file embedded as `proxmox.iso`
   - Works offline without network dependencies

2. **HTTP-served Configuration** (Secondary)
   - Answer file served via HTTP at kernel parameter URL
   - Dynamic configuration per MAC address
   - Allows runtime configuration changes

3. **DHCP Option Discovery** (Tertiary)
   - DHCP option 250 provides answer file URL
   - Automatic discovery without hardcoded URLs
   - Network-based configuration distribution

### DHCP Configuration
```bash
# DHCP option 250 for answer file discovery
dhcp-option=250,http://10.10.1.1/sessions/answer.toml
```

### Verification
A comprehensive verification script validates all components:
```bash
# Run full Proxmox iPXE verification
/mnt/verify_proxmox_config.sh
```

**Verification includes:**
- Network services (dnsmasq, nginx)
- DHCP configuration and option 250
- File accessibility (kernel, initrd, ISO, answer.toml)
- PHP configuration with correct kernel parameters
- Answer file TOML format validation
- initrd content verification (embedded ISO and config)
- iPXE response testing

### Troubleshooting

**Common Issues:**
1. **ISO Naming** - Must be `proxmox.iso` in initrd (not original filename)
2. **TOML Format** - Must use kebab-case field names (`root-password` not `root_password`)
3. **Kernel Parameters** - Must include `proxmox-start-auto-installer`
4. **initrd Size** - Large due to embedded ISO (~1.6GB)

**Resolution Steps:**
```bash
# Check initrd content
mkdir /tmp/check_initrd && cd /tmp/check_initrd
zstd -dc /var/www/html/provisioning/proxmox9/boot/initrd.img | cpio -i
ls -la proxmox.iso  # Must exist with this exact name
ls -la proxmox-auto-installer/answer.toml  # Must exist with correct format

# Verify TOML format
grep "root-password" proxmox-auto-installer/answer.toml  # Should use kebab-case
```

## Kubernetes Integration

This provisioning server works with the separate `ansible-kubernetes-nodes` project for Kubernetes cluster management:

### Kubernetes Workflow

1. **Provision nodes** using this server (Ubuntu 24.04 or Proxmox VE 9)
2. **Use ansible-kubernetes-nodes** project for Kubernetes deployment:
   - Kubespray management and setup
   - Kubernetes cluster deployment
   - Ceph storage configuration
   - Management tools installation

### Related Projects

- **ansible-kubernetes-nodes**: Handles Kubernetes cluster deployment and management
- **Kubespray integration**: Located in the kubernetes nodes project for better separation of concerns

### Management Scripts

#### Redfish Hardware Control
```bash
# System monitoring
./redfish.py <hostname> sensors [--filter cpu] [--json]

# Power management
./redfish.py <hostname> power-on|power-off|power-cycle|power-reboot

# Boot configuration
./redfish.py <hostname> set-boot-to-bios
```

#### Provisioning Operations
```bash
# Boot order management
./set_boot_order.py <hostname> pxe hdd

# Status verification
./verify_provisioning.py <hostname>
```

## Web Management Interface

### Dashboard Access
Navigate to `http://<provisioning-server-ip>` for the management interface.

### Feature Overview

| Feature | Description | Capability |
|---------|-------------|------------|
| **Status Monitoring** | Real-time node state tracking | `NEW`, `INSTALLING`, `DONE`, `FAILED` |
| **Provisioning Control** | One-click reprovisioning | State reset and reinstallation trigger |
| **Configuration Access** | Direct autoinstall links | Per-node cloud-init configurations |
| **Timestamp Tracking** | Last update monitoring | Activity auditing and debugging |

### API Endpoints

- `GET /` - Main dashboard interface
- `GET /autoinstall_configs/<mac>/user-data` - Node-specific autoinstall configuration
- `GET /autoinstall_configs/<mac>/meta-data` - Cloud-init metadata
- `POST /api/reprovision` - Trigger node reprovisioning

## Testing & Validation

### Automated Testing Suite
```bash
# Multi-OS provisioning validation
./test-multi-os.sh

# Proxmox iPXE configuration verification
/mnt/verify_proxmox_config.sh

# Python script validation
cd test
python3 -m pytest test_redfish.py -v
python3 -m pytest test_web_actions.py -v

# Ansible syntax validation
ansible-playbook --syntax-check site.yml
ansible-playbook --syntax-check set_boot_order.yml
```

#### Multi-OS Test Script

The `test-multi-os.sh` script provides comprehensive validation of both Ubuntu 24.04 and Proxmox VE 9 configurations:

**Test Coverage:**
- Web service availability and HTTP response codes
- Ubuntu autoinstall configuration (user-data, meta-data, YAML syntax)
- Proxmox answer.toml configuration (TOML syntax, required sections)
- TFTP boot setup and PXE configuration  
- PHP configuration and OS support validation
- Kernel parameters for network boot
- Network services (DHCP, DNS, HTTP) status

**Usage:**
```bash
# Run all tests with color-coded output
./test-multi-os.sh

# Check specific test results
echo $?  # 0 = all passed, 1 = failures detected
```

### Integration Testing Checklist

- [ ] **Network Services**: DHCP lease assignment and DNS resolution
- [ ] **Boot Services**: iPXE bootloader serving and chainloading
- [ ] **Web Services**: Autoinstall configuration accessibility
- [ ] **Hardware Management**: Redfish API connectivity and control
- [ ] **Dashboard Functionality**: Status updates and reprovisioning
- [ ] **Ubuntu Provisioning**: Cloud-init autoinstall workflow
- [ ] **Proxmox Provisioning**: iPXE auto-installer with embedded ISO
- [ ] **Multi-OS Support**: OS selection and configuration switching
- [ ] **DHCP Option 250**: Answer file URL discovery
- [ ] **End-to-End**: Complete provisioning workflow validation

### Performance Benchmarks

| Metric | Target | Measurement Method |
|--------|--------|--------------------|
| DHCP Response Time | < 100ms | `dhcping` utility |
| TFTP Transfer Rate | > 10 MB/s | iPXE boot timing |
| Web Response Time | < 200ms | HTTP load testing |
| Concurrent Provisions | 10+ nodes | Parallel deployment |

## Customization

### Network Topology Adaptation

**DHCP Configuration** (`roles/netboot/vars/main.yml`):
```yaml
dnsmasq_dhcp_range: "192.168.1.100,192.168.1.200,12h"
dnsmasq_listen_address: "192.168.1.1"
```

**NAT Configuration** (`roles/common/tasks/main.yml`):
```yaml
nat_source_network: "192.168.1.0/24"
nat_output_interface: "ens160"
```

### Autoinstall Customization

Templates located in `roles/web/templates/`:
- `autoinstall-user-data.j2`: Ubuntu installer configuration
- `autoinstall-meta-data.j2`: Cloud-init metadata configuration

### Hardware Platform Support

**Supermicro Servers**: Native support via SUM utility
**Dell PowerEdge**: iDRAC Redfish compatibility
**HPE ProLiant**: iLO Redfish integration
**Generic IPMI**: Standard BMC functionality

## Troubleshooting

### Automated Diagnostics

#### Health Monitoring System
```bash
# Check automated health monitoring
sudo systemctl status provisioning-health-check.timer
sudo tail -f /var/log/provisioning_health.log

# View recent service recovery actions
sudo journalctl -u provisioning-health-check.service --since "1 hour ago"

# Manual health check execution
sudo /usr/local/bin/monitoring/health_check.sh
```

#### Validation Framework
```bash
# Run system validation manually
sudo ansible-playbook site.yml --tags "validation"
# Check validation failure flags
ls -la /tmp/ansible_validation_failed

# View validation troubleshooting info
sudo journalctl | grep "validation failed"
```

### Service Diagnostics

#### Network Services Issues
```bash
# DHCP service validation with enhanced debugging
sudo systemctl status dnsmasq
sudo journalctl -u dnsmasq --since "1 hour ago"

# TFTP service verification (dnsmasq native)
sudo netstat -ulnp | grep ':69'
echo "quit" | tftp 127.0.0.1 69

# Network connectivity testing
sudo tcpdump -i <interface> port 67 or port 68
```

#### Web Services Issues
```bash
# Application stack health with resource monitoring
sudo systemctl status nginx php8.3-fpm
sudo tail -f /var/log/nginx/error.log

# PHP-FPM diagnostics with performance limits
sudo tail -f /var/log/php8.3-fpm.log
sudo systemctl show php8.3-fpm --property=LimitNOFILE,LimitNPROC
```

#### Hardware Management Issues
```bash
# Redfish connectivity testing
curl -k -u <user>:<pass> https://<node-ip>/redfish/v1/Systems/1

# Network reachability verification
ping <node-ip>
nmap -p 443 <node-ip>
```

### Common Resolution Patterns

| Issue Category | Symptoms | Resolution Strategy |
|----------------|----------|-------------------|
| **DHCP Failures** | No IP assignment | Interface binding, firewall rules, dynamic interface detection |
| **TFTP Conflicts** | Port 69 binding errors | Remove conflicting TFTP daemons, use dnsmasq integrated TFTP |
| **PXE Boot Issues** | Boot loop/timeout | TFTP permissions, bootloader integrity, service conflicts |
| **Provisioning Stalls** | Install hangs | Network connectivity, repository access, validation checks |
| **Hardware Control** | API timeouts | Credential validation, network paths, Redfish compatibility |
| **Service Recovery** | Services down | Check health monitoring logs, automatic restart status |
| **Validation Failures** | Deployment issues | Review validation logs, check system requirements, manual verification |
| **Proxmox Auto-installer** | "Searching for block device" | Check ISO naming in initrd (must be `proxmox.iso`) |
| **Proxmox Config Discovery** | "No config for fetching answer file" | Verify kernel parameters include `proxmox-start-auto-installer` |
| **Proxmox TOML Errors** | Configuration parsing failures | Use kebab-case field names (`root-password` not `root_password`) |
| **Large initrd Issues** | Slow downloads/timeouts | Optimize network, consider compression levels, verify ramdisk size |

### Log Analysis Locations

| Service | Log Location | Analysis Focus |
|---------|-------------|----------------|
| **dnsmasq** | `journalctl -u dnsmasq` | DHCP leases, DNS queries, TFTP transfers |
| **nginx** | `/var/log/nginx/` | HTTP requests, errors, security events |
| **PHP-FPM** | `/var/log/php8.3-fpm.log` | Application errors, performance issues |
| **Health Monitor** | `/var/log/provisioning_health.log` | Service recovery, resource alerts |
| **Validation** | `journalctl \| grep validation` | System validation results, failures |
| **System** | `/var/log/syslog` | General system events, security logs |

## Contributing

We welcome contributions from the community. Please review our contribution guidelines:

### Development Workflow
1. **Fork** the repository and create a feature branch
2. **Test** changes in an isolated environment
3. **Document** new features and configuration options
4. **Submit** pull request with comprehensive description

### Code Standards
- **Ansible**: Follow official best practices and use `ansible-lint`
- **Python**: Adhere to PEP 8 standards with `black` formatting
- **Documentation**: Update relevant README sections and role documentation

### Testing Requirements
- All Ansible playbooks must pass syntax validation
- Python scripts require unit test coverage
- Integration tests for end-to-end workflows

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## Support

### Community Support
- **GitHub Issues**: Bug reports and feature requests
- **Documentation**: Comprehensive guides and examples
- **Community Forums**: Discussion and knowledge sharing

### Enterprise Support
For production deployments and enterprise support:
- **Professional Services**: Implementation and customization
- **Training Programs**: Team education and certification
- **SLA Agreements**: Guaranteed response times and resolution

### Contact Information
- **Project Maintainer**: SDDC.info Team
- **GitHub Repository**: https://github.com/sddcinfo/ansible-provisioning-server
- **Documentation Site**: https://docs.sddc.info/provisioning

---

*Built by the SDDC.info community*