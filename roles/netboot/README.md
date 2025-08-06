# Netboot Role

This role configures DHCP, DNS, and TFTP services required for network booting and provisioning target servers via PXE/iPXE.

## Purpose

The netboot role sets up `dnsmasq` as a combined DHCP/DNS/TFTP server to provide network services for bare-metal provisioning operations.

## Tasks

### DNS Configuration
- Disables systemd-resolved to avoid conflicts
- Configures dnsmasq for DNS resolution with upstream servers
- Creates static DNS records for all nodes
- Sets up PTR records for reverse DNS lookups

### DHCP Configuration
- Configures DHCP reservations for all console nodes based on MAC addresses
- Sets DHCP range and options (router, DNS server)
- Enables DHCP authoritative mode and logging

### TFTP Configuration  
- Disables standalone tftpd-hpa service
- Configures dnsmasq to serve TFTP requests
- Creates TFTP root directory with proper permissions
- Downloads iPXE bootloaders from official sources

### PXE Boot Logic
- Configures chainloading logic to serve appropriate bootloaders
- Detects iPXE clients and serves custom boot URLs
- Handles EFI x86_64 client architecture detection

## Variables

Located in `vars/main.yml`:

### Network Configuration
- `dnsmasq_listen_address: "10.10.1.1"` - Interface IP for dnsmasq
- `dnsmasq_domain: "sddc.info"` - Domain name for DNS records  
- `dnsmasq_dhcp_range: "10.10.1.100,10.10.1.200,12h"` - DHCP IP range and lease time
- `dnsmasq_dhcp_router: "10.10.1.1"` - Default gateway for DHCP clients
- `dnsmasq_dhcp_dns_server: "10.10.1.1"` - DNS server for DHCP clients

### Node Configurations
- `provisioning_nodes` - Array of node objects with hostname, IP, MAC address
- `console_nodes` - Specific console node configurations
- `dnsmasq_dhcp_reservations` - DHCP reservations for managed nodes
- `dnsmasq_static_hosts` - Static DNS host entries

### Service Configuration  
- `dnsmasq_upstream_servers` - External DNS servers for resolution
- `tftp_root: "/var/lib/tftpboot"` - TFTP serving directory
- `ipxe_boot_url` - URL served to iPXE clients for chainloading

## Dependencies

- common role (for basic system setup)

## Templates

- `provisioning.conf.j2` - Main dnsmasq configuration file
- `resolv.conf.j2` - System DNS resolver configuration

## Handlers

- `restart dnsmasq` - Restarts dnsmasq service when configuration changes

## Files Created

- `/etc/dnsmasq.d/provisioning.conf` - Main dnsmasq configuration
- `/etc/resolv.conf` - System DNS configuration  
- `/var/lib/tftpboot/ipxe.efi` - iPXE EFI bootloader

## Tags

- `netboot` - All netboot-related tasks
- `dnsmasq_template` - Template deployment tasks

## Notes

- The role automatically downloads iPXE bootloaders if not present
- Includes retry logic for bootloader downloads with 5 attempts
- Properly handles service dependencies and startup ordering