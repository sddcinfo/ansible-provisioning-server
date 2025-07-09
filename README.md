# Ansible Provisioning Server

This Ansible project configures a dedicated server to provide all necessary network services for the automated bare-metal provisioning of Ubuntu servers.

## Overview

The playbook in this repository will configure the target server to perform the following roles:

-   **DHCP & TFTP:** Using `dnsmasq` and `tftpd-hpa` to assign IPs and serve iPXE bootloaders.
-   **HTTP Server:** Using `Nginx` and `PHP` to serve dynamic iPXE scripts, autoinstall configurations, and a live status page.
-   **ISO Preparation:** Downloads and extracts a specified Ubuntu ISO to be served over HTTP.
-   **NAT Gateway:** Configures `iptables` to provide internet access to the provisioned nodes.

## Configuration

-   **`inventory`**: This file should contain the IP address or hostname of the server you want to configure as the provisioner.
-   **`group_vars/all.yml`**: Contains the default password hash for the `sysadmin` user created during autoinstall.
-   **`roles/netboot/vars/main.yml`**: This is the **primary configuration file**. You must edit the `provisioning_nodes` list to define the MAC addresses, IP addresses, and hostnames for the machines you intend to provision.
-   **`roles/iso_preparation/vars/main.yml`**: Defines the URL of the Ubuntu ISO to be used.

## Usage

1.  **Bootstrap the control node:**
    ```bash
    ./bootstrap.sh
    ```
2.  **Configure your variables** (especially in `roles/netboot/vars/main.yml`).
3.  **Run the playbook:**
    ```bash
    ansible-playbook -i inventory site.yml
    ```
