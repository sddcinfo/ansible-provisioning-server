# Ansible Provisioning Server

This Ansible project configures a dedicated server to provide all the necessary network services for automated, bare-metal provisioning of Ubuntu servers using iPXE and cloud-init.

## Overview

This playbook configures the local server to be a provisioning server. This includes setting up DHCP, TFTP, and a web server to host iPXE scripts and Ubuntu autoinstall configurations.

## Prerequisites

This project relies on two external components that must be configured correctly:

1.  **Supermicro Update Manager (`sum`)**: The `set_boot_order.py` script will automatically download this utility if it's not found.
2.  **Redfish Credentials**: The `redfish.py` script requires a credential file at `~/.redfish_credentials`.

## Configuration

The single source of truth for all node information (MAC addresses, IP addresses, and hostnames) is the `nodes.json` file in the root of this repository. All scripts and playbooks read from this file.

## Usage

To run the entire playbook and configure all services from scratch:
```bash
# From the ansible-provisioning-server directory
ansible-playbook -i inventory site.yml --ask-become-pass
```

## External Scripts

This project includes external Python scripts for managing servers. They share a common configuration file, `nodes.json`, for node information.

### `redfish.py`

For basic, one-off server management tasks like checking power status or rebooting a node, you can use the `redfish.py` script. It can operate on a single node, multiple nodes, or all nodes defined in `nodes.json`.

**Credential Setup:**

This script requires a credential file at `~/.redfish_credentials` to authenticate with the servers' Baseboard Management Controllers (BMCs).

1.  **Create the credential file:**
    ```bash
    echo 'REDFISH_AUTH="your_bmc_user:your_bmc_password"' > ~/.redfish_credentials
    ```

2.  **Set secure permissions:**
    ```bash
    chmod 600 ~/.redfish_credentials
    ```

**Usage Examples:**

*   **Get the status of a single node:**
    ```bash
    ./redfish.py -n console-node1 status
    ```

*   **Reboot multiple nodes:**
    ```bash
    ./redfish.py -n console-node1 console-node2 reboot
    ```

*   **Get the boot order for all nodes:**
    ```bash
    ./redfish.py -a get-boot-order
    ```

### `set_boot_order.py`

For reliable, persistent boot order changes on Supermicro motherboards, this project includes the `set_boot_order.py` script, which uses Supermicro's official `sum` utility.

**Usage:**

To apply a specific boot order to a node, run the script with the node's hostname, IPMI credentials, and a space-separated list of boot devices.

**Example:**
```bash
./set_boot_order.py console-node1 ADMIN 'your_password' pxe hdd
```
This example sets the boot order to UEFI Network (`pxe`) first, followed by UEFI Hard Disk (`hdd`).

**Available Boot Devices:**
*   `pxe`
*   `hdd`
*   `disabled`


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
