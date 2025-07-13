# Ansible Provisioning Server

This Ansible project configures a dedicated server to provide all the necessary network services for automated, bare-metal provisioning of Ubuntu servers using iPXE and cloud-init.

## Overview

The playbook configures the target server with the following services:

- **DHCP & TFTP:** `dnsmasq` provides DHCP services and serves the iPXE bootloader over TFTP.
- **Web Server:** `Nginx` and `PHP` serve dynamic iPXE boot scripts and Ubuntu Autoinstall (cloud-init) configurations.
- **ISO Preparation:** The playbook downloads a specified Ubuntu ISO, extracts the necessary kernel and initrd, and makes the full ISO contents available over HTTP for the installation process.
- **Network Address Translation (NAT):** Configures the server to act as a gateway, providing internet access to the provisioning network.
- **Utility Scripts:** A dynamic `redfish.py` script is generated for server management.

## Configuration

All configuration is handled through variables defined in the `roles/*/vars/` directories. The most critical variables are in `roles/netboot/vars/main.yml`, where you define the nodes to be provisioned.

## Usage

### Full Provisioning

To run the entire playbook and configure all services from scratch:
```bash
# From the ansible-provisioning-server directory
ansible-playbook -i inventory site.yml --ask-become-pass
```

### Targeted Testing with Tags

For more efficient development and testing, you can use tags to run specific parts of the playbook.

```bash
# Example: Only update the Nginx configuration
ansible-playbook -i inventory site.yml --tags "nginx" --ask-become-pass

# Example: Update the autoinstall configs and the redfish script
ansible-playbook -i inventory site.yml --tags "autoinstall_configs,redfish_script" --ask-become-pass
```

**Available Tags:**
- `packages`: Installs common packages.
- `ssh_keys`: Configures SSH keys and the SSH daemon.
- `network`: Configures NAT and IP forwarding.
- `netboot`: Configures `dnsmasq` and TFTP for network booting.
- `nginx`: Configures the Nginx web server.
- `php`: Configures PHP-FPM.
- `autoinstall_configs`: Manages the Ubuntu Autoinstall configuration files.
- `redfish_script`: Generates the `redfish.py` management script.

---

## Included Scripts

### Redfish Management Script (`redfish.py`)

This Python script is **dynamically generated** by the playbook and provides a convenient way to manage your servers' power and boot settings using the Redfish API. It is always in sync with the nodes defined in your Ansible inventory and is located in the project's root directory.

**One-Time Setup:**

Before using the script for the first time, you must create a credentials file.
```bash
# Replace with your actual credentials
echo 'REDFISH_AUTH="your_username:your_password"' > ~/.redfish_credentials
chmod 600 ~/.redfish_credentials
```

**Usage:**

The script can target a single node or a comma-separated list of nodes.

```bash
# Get the power status of a single console node
./redfish.py console-node1 status

# Reboot multiple nodes at once
./redfish.py console-node1,console-node2 reboot

# Get a human-readable summary of system inventory
./redfish.py console-node1 inventory --resource system

# Get memory inventory in CSV format
./redfish.py console-node1 inventory --resource memory --format csv

# Get only temperature sensor data containing "CPU"
./redfish.py console-node1 sensors --type temperature --name "CPU"
```

### Verification Script (`verify_playbook.sh`)

This script is an integration test for your provisioning environment. After running the main playbook (or the relevant tagged sections), you can execute this script to get a clear, color-coded report on whether all the critical components are functioning correctly.

**Usage:**

```bash
./verify_playbook.sh
```