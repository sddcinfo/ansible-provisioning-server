# Ansible Provisioning Server

This Ansible project configures a dedicated server to provide all the necessary network services for automated, bare-metal provisioning of Ubuntu servers using iPXE and cloud-init.

## Overview

This playbook configures the local server to be a provisioning server. This includes setting up DHCP, TFTP, and a web server to host iPXE scripts and Ubuntu autoinstall configurations.

## Prerequisites

This project relies on one external component that must be configured correctly:

1.  **Supermicro Update Manager (`sum`)**: The `set_boot_order.py` script will automatically download this utility if it's not found.

## Configuration

The single source of truth for all node information (MAC addresses, IP addresses, and hostnames) is the `nodes.json` file in the root of this repository. All scripts and playbooks read from this file.

### Secrets Management

This project uses **Ansible Vault** to securely manage sensitive information like IPMI credentials. The encrypted secrets are stored in `group_vars/all/secrets.yml`.

To run playbooks that use these secrets, you must have a vault password file.

1.  **Create the vault password file:**
    ```bash
    echo "your_vault_password" > .vault_pass
    ```

2.  **Set secure permissions:**
    ```bash
    chmod 600 .vault_pass
    ```

3.  **Add to `.gitignore`:** The `.vault_pass` file is already included in the `.gitignore` file to prevent it from being committed to the repository.

## Usage

To run the entire playbook and configure all services from scratch:
```bash
# From the ansible-provisioning-server directory
sudo ansible-playbook -i inventory site.yml --vault-password-file /path/to/your/.vault_pass
```

## External Scripts

This project includes external Python scripts for managing servers. They share a common configuration file, `nodes.json`, for node information.

### `redfish.py`

For basic, one-off server management tasks like checking power status or rebooting a node, you can use the `redfish.py` script. It can operate on a single node, multiple nodes, or all nodes defined in `nodes.json`.

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

To apply a specific boot order to a node, run the `set_boot_order.yml` playbook with the vault password file.

**Example:**
```bash
ansible-playbook set_boot_order.yml --vault-password-file .vault_pass
```
This will set the boot order to UEFI Network (`pxe`) first, followed by UEFI Hard Disk (`hdd`).

---

## Web Interface

The provisioning server now includes a web interface for monitoring and managing the status of provisioning nodes. Simply navigate to the IP address of the provisioning server in your web browser.

**Features:**
- **Status Dashboard:** View the current provisioning status (`NEW`, `INSTALLING`, 'DONE', `FAILED`) for all configured nodes.
- **Timestamps:** See when each node's status was last updated.
- **Reprovisioning:** A "Reprovision" button allows you to reset a node's status to `NEW`, triggering a fresh installation on its next network boot.
- **Auto-Refresh:** The page includes a "Refresh" button for manual updates.
