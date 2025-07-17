# Ansible Provisioning Server

This Ansible project configures a dedicated server to provide all the necessary network services for automated, bare-metal provisioning of Ubuntu servers using iPXE and cloud-init.

## Overview

This project automates the setup of a provisioning server. It configures:
- **DHCP & TFTP:** `dnsmasq` provides DHCP leases and serves iPXE bootloaders.
- **Web Server:** `nginx` hosts Ubuntu autoinstall configurations and a simple status dashboard.
- **Network:** Configures the server for NAT to provide internet access to the provisioning network.
- **Server Management:** Includes scripts for managing server boot order and power state via Redfish.

## Prerequisites

- **Ansible:** The playbook is designed to be run on the provisioning server itself.
- **Git:** To clone this repository.
- **Supermicro Update Manager (`sum`):** This is required for changing the BIOS boot order and is downloaded automatically by the `set_boot_order.yml` playbook if not present.

## Configuration

The single source of truth for all node information (MAC addresses, IP addresses, and hostnames) is the `nodes.json` file in the root of this repository. All scripts and playbooks read from this file.

### Secrets Management

This project uses **Ansible Vault** to securely manage sensitive information like IPMI credentials. The encrypted secrets are stored in `group_vars/all/secrets.yml`.

To run playbooks that use these secrets, you must have a vault password file.

1.  **Create the vault password file:**
    ```bash
    echo "your_vault_password" > ~/.vault_pass
    ```

2.  **Set secure permissions:**
    ```bash
    chmod 600 ~/.vault_pass
    ```

3.  **Add to `.gitignore`:** The `.vault_pass` file is already included in the `.gitignore` file to prevent it from being committed to the repository.

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

The provisioning server includes a simple web interface for monitoring the status of the nodes. Navigate to the IP address of the provisioning server in your web browser.

**Features:**
- **Status Dashboard:** View the current provisioning status (`NEW`, `INSTALLING`, 'DONE', `FAILED`) for all configured nodes.
- **Timestamps:** See when each node's status was last updated.
- **Reprovisioning:** A "Reprovision" button allows you to reset a node's status to `NEW`, triggering a fresh installation on its next network boot.
- **Manual Refresh:** The page includes a "Refresh" button for manual updates.
