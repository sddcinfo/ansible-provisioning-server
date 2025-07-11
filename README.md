# Ansible Provisioning Server

This Ansible project configures a dedicated server to provide network services for automated bare-metal provisioning of Ubuntu servers.

## Overview

The playbook configures the target server with the following services:

- **DHCP & TFTP:** `dnsmasq` and `tftpd-hpa` for IP assignment and iPXE bootloaders.
- **HTTP Server:** `Nginx` and `PHP` for dynamic iPXE scripts, autoinstall configurations, and a status page.
- **ISO Preparation:** Downloads and extracts a specified Ubuntu ISO for HTTP serving.
- **NAT Gateway:** `iptables` for internet access for provisioned nodes.

## Configuration

- **`inventory`**: Define the provisioner server's IP or hostname.
- **`group_vars/all.yml`**: Contains all configurable variables, including the default password hash for the `sysadmin` user, network settings, and the list of nodes to provision.

## Usage

1. **Configure your variables** in `group_vars/all.yml`.
2. **Run the playbook:**
   ```bash
   ansible-playbook -i inventory site.yml --ask-become-pass
   ```
