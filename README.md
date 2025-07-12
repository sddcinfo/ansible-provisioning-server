# Ansible Provisioning Server

This Ansible project configures a dedicated server to provide all the necessary network services for automated, bare-metal provisioning of Ubuntu servers using iPXE and cloud-init.

## Overview

The playbook configures the target server with the following services:

- **DHCP & TFTP:** `dnsmasq` provides DHCP services and serves the iPXE bootloader over TFTP.
- **Web Server:** `Nginx` and `PHP` serve dynamic iPXE boot scripts and Ubuntu Autoinstall (cloud-init) configurations.
- **ISO Preparation:** The playbook downloads a specified Ubuntu ISO, extracts the necessary kernel and initrd, and makes the full ISO contents available over HTTP for the installation process.
- **Network Address Translation (NAT):** Configures the server to act as a gateway, providing internet access to the provisioning network.

## Configuration

All configuration is handled through variables defined in the `roles/*/vars/` directories. The most critical variables are in `roles/netboot/vars/main.yml`, where you define the nodes to be provisioned.

## Usage

1. **Define your nodes:** Edit `roles/netboot/vars/main.yml` to define the `provisioning_nodes` and `console_nodes` with their respective MAC addresses, IP addresses, and hostnames.
2. **Run the playbook:**
   ```bash
   # From the ansible-provisioning-server directory
   ansible-playbook -i inventory site.yml --ask-become-pass
   ```

---

## Post-Provisioning Scripts

After the main playbook has been successfully run, two utility scripts will be available in the `/home/sysadmin` directory on the provisioning server.

### 1. Redfish Management Script (`redfish.py`)

This Python script is **dynamically generated** by the playbook and provides a convenient way to manage your servers' power and boot settings using the Redfish API. It is always in sync with the nodes defined in your Ansible inventory.

**One-Time Setup:**

Before using the script for the first time, you must create a credentials file. The script expects a file named `~/.redfish_credentials` containing your Redfish username and password.

Create the file with the following command, replacing the placeholder credentials:
```bash
echo 'REDFISH_AUTH="your_username:your_password"' > ~/.redfish_credentials
chmod 600 ~/.redfish_credentials
```

**Usage:**

The script takes two arguments: the node name and the action to perform.

```bash
# Example: Get the power status of a console node
./redfish.py console-node1 status

# Example: Set a provisioning node to boot from PXE on the next restart
./redfish.py node1 pxe
```

**Available Actions:**
- `status`: Get system power state and health status.
- `power-on`: Powers the system on.
- `power-off`: Powers the system off gracefully.
- `power-force-off`: Forces the system to power off immediately.
- `reboot`: Performs a force restart of the node.
- `bios`: Sets the node to boot into BIOS setup on the next restart.
- `pxe`: Sets the node to boot from PXE on the next restart.
- `disk`: Sets the node to boot from the default disk on the next restart.

### 2. Verification Script (`verify_playbook.sh`)

This script is an integration test for your provisioning environment. After running the main playbook, you can execute this script to get a clear, color-coded report on whether all the critical components are functioning correctly.

**Usage:**

```bash
./verify_playbook.sh
```

The script will check:
- DNS resolution for your nodes.
- Correct generation of iPXE scripts by the web server.
- NAT and IP forwarding rules.
- The status of all required services (`dnsmasq`, `nginx`, etc.).
