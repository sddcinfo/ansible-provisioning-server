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

### Full Provisioning

To run the entire playbook and configure all services from scratch:
```bash
# From the ansible-provisioning-server directory
ansible-playbook -i inventory site.yml --ask-become-pass
```

### Targeted Testing with Tags

For more efficient development and testing, you can use tags to run specific parts of the playbook.

```bash
# Example: Only regenerate the utility scripts
ansible-playbook -i inventory site.yml --tags "redfish_script,verify_script" --ask-become-pass
```

**Available Tags:**
- `packages`: Installs common packages.
- `ssh_keys`: Configures SSH keys and the SSH daemon.
- `network`: Configures NAT and IP forwarding.
- `netboot`: Configures `dnsmasq` and TFTP for network booting.
- `nginx`: Configures the Nginx web server.
- `php`: Configures PHP-FPM.
- `www_content`: Updates the content of the web root (e.g., `index.php`).
- `autoinstall_configs`: Manages the Ubuntu Autoinstall configuration files.
- `redfish_script`: Generates the `redfish.py` management script.

## Testing

This project includes a native Python test suite for validating the functionality of the provisioning server. The tests are located in the `test/` directory.

**Running Tests:**

To run the entire test suite, execute the following command from the root of the `ansible-provisioning-server` directory:

```bash
python3 -m unittest discover test
```

The test suite will automatically handle the setup and teardown of any necessary test files.

---

## Web Interface

The provisioning server now includes a web interface for monitoring and managing the status of provisioning nodes. Simply navigate to the IP address of the provisioning server in your web browser.

**Features:**
- **Status Dashboard:** View the current provisioning status (`NEW`, `INSTALLING`, `DONE`, `FAILED`) for all configured nodes.
- **Timestamps:** See when each node's status was last updated.
- **Reprovisioning:** A "Reprovision" button allows you to reset a node's status to `NEW`, triggering a fresh installation on its next network boot.
- **Auto-Refresh:** The page includes a "Refresh" button for manual updates.
