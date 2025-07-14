# Ansible Provisioning Server

This Ansible project configures a dedicated server to provide all the necessary network services for automated, bare-metal provisioning of Ubuntu servers using iPXE and cloud-init.

## Overview

This repository contains two main Ansible playbooks:

1.  **`site.yml`**: Configures the local server to be a provisioning server. This includes setting up DHCP, TFTP, and a web server to host iPXE scripts and Ubuntu autoinstall configurations.
2.  **`configure_nodes.yml`**: Configures the bare-metal servers that will be provisioned. This playbook uses the Supermicro `sum` utility to reliably set the BIOS boot order to UEFI PXE.

## Provisioning Server Setup (`site.yml`)

This playbook configures the target server with the following services:

- **DHCP & TFTP:** `dnsmasq` provides DHCP services and serves the iPXE bootloader over TFTP.
- **Web Server:** `Nginx` and `PHP` serve dynamic iPXE boot scripts and Ubuntu Autoinstall (cloud-init) configurations.
- **ISO Preparation:** The playbook downloads a specified Ubuntu ISO, extracts the necessary kernel and initrd, and makes the full ISO contents available over HTTP for the installation process.
- **Network Address Translation (NAT):** Configures the server to act as a gateway, providing internet access to the provisioning network.

### Usage

To run the entire playbook and configure all services from scratch:
```bash
# From the ansible-provisioning-server directory
ansible-playbook -i inventory site.yml --ask-become-pass
```

## Node Configuration (`configure_nodes.yml`)

For reliable, persistent boot order changes on Supermicro motherboards, this playbook uses a dedicated `bios` role that leverages Supermicro's official `sum` utility. This is the recommended way to ensure servers boot to UEFI PXE for provisioning.

The role is idempotent and will only make changes if the boot order is not already correctly set.

### Configuration

The `bios` role requires IPMI credentials and the target IP address to be defined. You can pass these as extra variables. The boot order itself is defined as a variable in the playbook.

**Default "Provisioning" Boot Order:**
1.  UEFI Network
2.  UEFI Hard Disk

### Usage

To apply the BIOS configuration to one or more nodes, you can run the playbook and target the specific nodes from your inventory, passing the required variables.

**Example for a single node:**
```bash
ansible-playbook -i inventory configure_nodes.yml --tags "bios" -e "ipmi_address=10.10.1.11 ipmi_user=ADMIN ipmi_pass='your_password'"
```

## Redfish Management (`redfish.py`)

For basic, one-off server management tasks like checking power status or rebooting a node, you can use the `redfish.py` script.

**Credential Setup:**

1.  **Create the credential file:**
    ```bash
    echo 'REDFISH_AUTH="your_bmc_user:your_bmc_password"' > ~/.redfish_credentials
    ```

2.  **Set secure permissions:**
    ```bash
    chmod 600 ~/.redfish_credentials
    ```


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
