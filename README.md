# Ansible Provisioning Server

This Ansible project configures a dedicated server to provide all the necessary network services for automated, bare-metal provisioning of Ubuntu servers using iPXE and cloud-init.

## Overview

This playbook configures the local server to be a provisioning server. This includes setting up DHCP, TFTP, and a web server to host iPXE scripts and Ubuntu autoinstall configurations.

## Usage

To run the entire playbook and configure all services from scratch:
```bash
# From the ansible-provisioning-server directory
ansible-playbook -i inventory site.yml --ask-become-pass
```

## External Scripts

This project includes external scripts for managing servers.

### `redfish.py`

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

### `set_smc_boot_order.sh`

For reliable, persistent boot order changes on Supermicro motherboards, this project includes the `set_smc_boot_order.sh` script, which uses Supermicro's official `sum` utility.

**Usage:**

To apply a specific boot order to a node, run the script with the IPMI address, credentials, and a space-separated list of boot device codes.

**Example:**
```bash
./set_smc_boot_order.sh 10.10.1.11 ADMIN 'your_password' 0006 0000
```
This example sets the boot order to UEFI Network (`0006`) first, followed by UEFI Hard Disk (`0000`).


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
