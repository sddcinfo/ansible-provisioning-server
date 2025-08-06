# Ansible Provisioning Server

This Ansible project configures a dedicated server to provide all the necessary network services for automated, bare-metal provisioning of Ubuntu servers using iPXE and cloud-init.

## Overview

This project automates the setup of a provisioning server that enables zero-touch deployment of Ubuntu servers on bare-metal hardware. The system provides a complete provisioning infrastructure including network services, boot management, and monitoring capabilities.

### Components

The provisioning server configures and manages the following services:
- **DHCP & TFTP:** `dnsmasq` provides DHCP leases and serves iPXE bootloaders
- **Web Server:** `nginx` hosts Ubuntu autoinstall configurations and provides a status dashboard
- **Network Configuration:** Configures NAT routing to provide internet access to the provisioning network
- **Server Management:** Includes scripts for managing server boot order and power state via Redfish API
- **Monitoring:** Web-based dashboard for tracking provisioning status and node management

## Architecture

```
Internet
    |
    v
[Provisioning Server] ←→ [Management Network: 10.10.1.0/24]
    |                          |
    |                          ├── console-node1 (10.10.1.11)
    |                          ├── console-node2 (10.10.1.12)
    |                          ├── console-node3 (10.10.1.13)
    |                          └── console-node4 (10.10.1.14)
    |
    └── [Kubernetes Network: 10.10.1.0/24] & [Ceph Network: 10.10.2.0/24]
```

The provisioning server acts as the central hub for:
1. **DHCP/DNS Services**: Assigns IP addresses and provides name resolution
2. **PXE Boot Services**: Serves iPXE bootloaders and autoinstall configurations
3. **Status Monitoring**: Tracks provisioning progress through web interface
4. **Hardware Management**: Controls server power and boot order via IPMI/Redfish

## Prerequisites

### System Requirements
- **Operating System:** Ubuntu 20.04+ (tested on Ubuntu 24.04)
- **Python:** Python 3.8+ with pip
- **Ansible:** Version 2.9+ (installed via pip or package manager)
- **Git:** For repository management
- **Network Access:** Internet connectivity for downloading packages and ISO images
- **Hardware:** Minimum 4GB RAM, 50GB storage for ISO images and web content

### Network Requirements
- **Management Interface:** Connected to provisioning network (default: 10.10.1.0/24)
- **Internet Interface:** For NAT and package downloads
- **IPMI Access:** Network access to target servers' BMC interfaces
- **Supermicro Update Manager (`sum`):** Downloaded automatically by the playbook if not present

## Installation Workflow

Follow these steps to deploy the provisioning server from scratch:

### Step 1: Prepare the Server
```bash
# Update the system
sudo apt update && sudo apt upgrade -y

# Install Ansible and Git
sudo apt install -y ansible git python3-pip

# Clone this repository
git clone https://github.com/sddcinfo/ansible-provisioning-server.git
cd ansible-provisioning-server
```

### Step 2: Configure Nodes and Secrets
```bash
# Edit nodes.json to match your hardware
vi nodes.json

# Create and encrypt vault secrets
ansible-vault create group_vars/all/secrets.yml

# Create vault password file
echo "your_vault_password" > ~/.vault_pass
chmod 600 ~/.vault_pass
```

### Step 3: Deploy the Provisioning Server
```bash
# Run the main playbook to set up all services
sudo ansible-playbook site.yml --vault-password-file ~/.vault_pass
```

### Step 4: Configure Target Servers for PXE Boot
```bash
# Set boot order for each node (replace node name as needed)
sudo ansible-playbook set_boot_order.yml --vault-password-file ~/.vault_pass --limit console-node1
```

### Step 5: Verify Installation
```bash
# Check service status
sudo systemctl status dnsmasq nginx php8.3-fpm

# Test web interface
curl http://localhost

# Test Redfish connectivity
./redfish.py console-node1 sensors
```

## Configuration

### Node Configuration

The single source of truth for all node information is the `nodes.json` file in the root of this repository. All scripts and playbooks read from this file to determine target server configurations.

#### Node Configuration Format

The `nodes.json` file contains an array of console nodes with the following required fields:

```json
{
  "console_nodes": [
    {
      "hostname": "console-node1",
      "ip": "10.10.1.11",
      "mac": "ac:1f:6b:6c:58:31",
      "k8s_ip": "10.10.1.21",
      "ceph_ip": "10.10.2.21"
    }
  ]
}
```

**Field Descriptions:**
- **hostname**: Unique identifier used in Ansible inventory and management scripts
- **ip**: Primary IP address for management and provisioning operations
- **mac**: Network interface MAC address for DHCP reservations and PXE boot identification
- **k8s_ip**: IP address assigned for Kubernetes cluster communication
- **ceph_ip**: IP address assigned for Ceph distributed storage network

**Important Notes:**
- MAC addresses must be lowercase and colon-separated (e.g., `aa:bb:cc:dd:ee:ff`)
- IP addresses should be within your provisioning network range
- Hostnames must match entries in the Ansible inventory file
- All fields are required for proper operation

### Secrets Management

This project uses **Ansible Vault** to securely manage sensitive information like IPMI credentials. The encrypted secrets are stored in `group_vars/all/secrets.yml`.

To run playbooks that use these secrets, you must have a vault password file.

1. **Create the vault password file:**
   ```bash
   echo "your_vault_password" > ~/.vault_pass
   ```

2. **Set secure permissions:**
   ```bash
   chmod 600 ~/.vault_pass
   ```

3. **Add to `.gitignore`:** The `.vault_pass` file is already included in the `.gitignore` file to prevent it from being committed to the repository.

## Usage

There are two main playbooks in this project.

### 1. `site.yml`

This is the main playbook for setting up the provisioning server.

```bash
# From the ansible-provisioning-server directory
sudo ansible-playbook site.yml --vault-password-file ~/.vault_pass
```

### 2. `set_boot_order.yml`

This playbook is used to set the boot order on the console nodes to PXE boot and then enter the BIOS. This is a necessary step before provisioning.

```bash
# From the ansible-provisioning-server directory
sudo ansible-playbook set_boot_order.yml --vault-password-file ~/.vault_pass --limit <node_name>
```
Replace `<node_name>` with the specific node you want to configure (e.g., `console-node1`).

## External Scripts

### `redfish.py`

A simple script for interacting with the Redfish API on the console nodes.

**Usage Examples:**

*   **Get sensor data (temperature, fans):**
    ```bash
    ./redfish.py console-node1 sensors
    ```

*   **Set the server to boot into BIOS setup on the next restart:**
    ```bash
    ./redfish.py console-node1 set-boot-to-bios
    ```

*   **Power cycle a node:**
    ```bash
    ./redfish.py console-node1 power-cycle
    ```

## Web Interface

The provisioning server includes a web interface for monitoring node status and managing deployments. Navigate to the IP address of the provisioning server in your web browser.

**Features:**
- **Status Dashboard:** View the current provisioning status (`NEW`, `INSTALLING`, `DONE`, `FAILED`) for all configured nodes
- **Real-time Updates:** Timestamps show when each node's status was last updated
- **Reprovisioning:** "Reprovision" button resets a node's status to `NEW`, triggering fresh installation on next network boot
- **Manual Refresh:** Page includes "Refresh" button for immediate status updates
- **Node Management:** Direct links to autoinstall configurations for each node

**Status Meanings:**
- `NEW`: Node is ready for provisioning
- `INSTALLING`: Node is currently being provisioned
- `DONE`: Provisioning completed successfully
- `FAILED`: Provisioning encountered an error

## Testing

The project includes comprehensive test suites to validate functionality:

### Running Python Script Tests
```bash
# Run redfish script tests
cd test
python3 test_redfish.py

# Run web functionality tests
python3 test_web_actions.py
```

### Manual Testing Checklist
- [ ] DHCP leases are assigned correctly
- [ ] PXE boot serves iPXE bootloader
- [ ] Autoinstall configurations are accessible via HTTP
- [ ] Redfish commands work against target servers
- [ ] Web dashboard displays node status
- [ ] Reprovisioning resets node status correctly

## Customization

### Network Configuration
To adapt the system for different network ranges, modify these files:
- `roles/netboot/vars/main.yml`: DHCP ranges and DNS settings
- `nodes.json`: IP address assignments
- `roles/common/tasks/main.yml`: NAT rules and interface names

### Adding New Node Types
1. Update `nodes.json` with new node information
2. Add corresponding entries to `inventory` file
3. Customize autoinstall templates in `roles/web/templates/` if needed

### Custom Autoinstall Configurations
Autoinstall templates are located in `roles/web/templates/`:
- `autoinstall-user-data.j2`: Main installation configuration
- `autoinstall-meta-data.j2`: Cloud-init metadata

## Troubleshooting

### Common Issues

**DHCP not working:**
- Check `dnsmasq` service status: `sudo systemctl status dnsmasq`
- Verify network interface configuration
- Ensure firewall allows DHCP traffic (ports 67/68)

**PXE boot failures:**
- Confirm iPXE bootloader downloaded: `ls -la /var/lib/tftpboot/`
- Check network connectivity between server and target nodes
- Verify MAC addresses in `nodes.json` match actual hardware

**Redfish connectivity issues:**
- Test network connectivity: `ping <node-ip>`
- Verify IPMI credentials in vault file
- Check BMC network configuration on target servers

**Web interface not accessible:**
- Verify nginx service: `sudo systemctl status nginx`
- Check PHP-FPM status: `sudo systemctl status php8.3-fpm`
- Review nginx error logs: `sudo tail -f /var/log/nginx/error.log`

**Node status not updating:**
- Check file permissions: `ls -la /var/www/html/state.json`
- Verify web server can write to sessions directory
- Review PHP error logs: `sudo tail -f /var/log/php8.3-fpm.log`

### Debug Commands
```bash
# Check all service statuses
sudo systemctl status dnsmasq nginx php8.3-fpm

# View DHCP lease information
sudo cat /var/lib/dhcp/dhcpd.leases

# Monitor dnsmasq logs
sudo journalctl -u dnsmasq -f

# Test autoinstall configuration
curl http://<server-ip>/autoinstall_configs/<mac-address>/user-data
```

### Log Locations
- **dnsmasq**: `journalctl -u dnsmasq`
- **nginx**: `/var/log/nginx/access.log`, `/var/log/nginx/error.log`
- **PHP-FPM**: `/var/log/php8.3-fpm.log`
- **System**: `/var/log/syslog`

## Contributing

When contributing to this project:
1. Test changes in a development environment
2. Update documentation for any configuration changes
3. Run the test suite before submitting pull requests
4. Follow existing code style and commenting patterns

## Support

For issues and questions:
1. Check the troubleshooting section above
2. Review system logs for error messages
3. Create an issue on GitHub with relevant log excerpts
4. Include your system configuration and network setup details