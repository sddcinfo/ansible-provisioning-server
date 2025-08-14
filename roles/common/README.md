# Common Role

This role handles basic system configuration and setup tasks that are common across all provisioning server deployments.

## Purpose

The common role performs essential system hardening, package management, and network configuration tasks required for the provisioning server to function properly.

## Tasks

### Package Management
- Updates system packages to latest versions
- Installs essential tools: vim, git, curl, wget, unzip
- Configures automatic package cleanup

### SSH Configuration
- Generates Ed25519 SSH key pairs for both `sysladmin` and `root` users
- Configures SSH client settings for automated connections to managed nodes
- Hardens SSH daemon configuration (PermitRootLogin prohibit-password)
- Sets up authorized keys for key-based authentication

### Network Configuration
- Enables IPv4 and IPv6 forwarding for dual-stack functionality
- Configures iptables NAT rules for internal network access
- Installs and configures iptables-persistent for rule persistence
- Supports full dual-stack IPv4/IPv6 networking

### System Hardening
- Sets proper file permissions and ownership for SSH keys
- Creates secure SSH client configurations
- Configures system for provisioning network operations

## Variables

This role primarily uses dynamically generated variables and hardcoded values. Key variables include:

- SSH keys generated during execution
- Network interface configurations (hardcoded to `ens34` for external, internal network: `10.10.1.0/24`)

## Dependencies

- None - this is a foundational role

## Files Created

- `/home/sysladmin/.ssh/sysladmin_automation_key` - SSH private key for sysladmin user
- `/home/sysladmin/.ssh/sysladmin_automation_key.pub` - SSH public key for sysladmin user  
- `/root/.ssh/root_automation_key` - SSH private key for root user
- `/root/.ssh/root_automation_key.pub` - SSH public key for root user
- `/home/sysladmin/.ssh/config` - SSH client configuration

## Handlers

- `restart sshd` - Restarts SSH daemon when configuration changes

## Tags

- `packages` - Package management tasks
- `ssh_keys` - SSH key generation and configuration
- `network` - Network and NAT configuration