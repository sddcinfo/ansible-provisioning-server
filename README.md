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
- **`roles/*/vars/main.yml`**: These files contain all configurable variables, such as network settings, node lists, and ISO details.

## Usage

1. **Configure your variables** in the `roles/*/vars/main.yml` files.
2. **Run the playbook:**
   ```bash
   ansible-playbook -i inventory site.yml --ask-become-pass
   ```
