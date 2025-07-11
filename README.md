# Ansible Provisioning Server

This Ansible project configures a dedicated server to provide network services for automated bare-metal provisioning of Ubuntu servers.

## Overview

The playbook configures the target server with the following services:

- **DHCP & TFTP:** `dnsmasq` and `tftpd-hpa` for IP assignment and iPXE bootloaders.
- **HTTP Server:** `Nginx` and `PHP` for dynamic iPXE scripts, autoinstall configurations, and a status page.
- **ISO Preparation:** Downloads and extracts a specified Ubuntu ISO for HTTP serving.

## Configuration Variables

All configuration is handled through variables defined in `roles/*/vars/main.yml`.

### `roles/netboot/vars/main.yml`

- `dnsmasq_interface`: The network interface for `dnsmasq` to listen on.
- `dnsmasq_listen_address`: The IP addresses for `dnsmasq` to listen on.
- `dnsmasq_domain`: The domain name for the provisioning network.
- `dnsmasq_upstream_servers`: Upstream DNS servers.
- `provisioning_nodes`: A list of dictionaries, each defining a node with its `mac`, `ip`, and `hostname`.
- `console_nodes`: A list of dictionaries, each defining a console node with its `mac`, `ip`, and `hostname`.
- `dnsmasq_static_hosts`: A list of static DNS entries.
- `dnsmasq_dhcp_range`: The DHCP address range.
- `dnsmasq_dhcp_router`: The default gateway for the provisioning network.
- `dnsmasq_dhcp_dns_server`: The DNS server for the provisioning network.
- `tftp_root`: The root directory for TFTP.
- `ipxe_boot_url`: The URL for the iPXE boot script.

### `roles/iso_preparation/vars/main.yml`

- `ubuntu_iso_url`: The URL to download the Ubuntu ISO.
- `ubuntu_iso_name`: The filename of the Ubuntu ISO.
- `ubuntu_iso_download_dir`: The directory to download the ISO to.
- `ubuntu_iso_mount_point`: The mount point for the ISO.
- `ubuntu_provisioning_dir`: The directory to store the extracted ISO contents.

### `roles/web/vars/main.yml`

- `nginx_web_root`: The root directory for the Nginx web server.
- `nginx_server_name`: The server name for Nginx.
- `php_socket`: The path to the PHP-FPM socket.
- `server_ip`: The IP address of the provisioning server.
- `iso_base_url`: The base URL for accessing the ISO contents.
- `autoinstall_nodes`: A list of nodes for autoinstallation, matching the `provisioning_nodes` in the `netboot` role.
- `autoinstall_console_nodes`: A list of console nodes for autoinstallation, matching the `console_nodes` in the `netboot` role.

## Usage

1. **Configure your variables** in the `roles/*/vars/main.yml` files.
2. **Run the playbook:**
   ```bash
   ansible-playbook site.yml --ask-become-pass
   ```