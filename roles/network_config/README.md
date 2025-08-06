# Network Config Role

This role handles advanced network configuration for provisioned nodes, including multi-network setup for Kubernetes and Ceph clusters.

## Purpose

The network_config role configures complex networking on target servers after provisioning, setting up multiple network interfaces for management, Kubernetes cluster communication, and Ceph storage networks.

## Tasks

### Network Interface Configuration
- Configures multiple network interfaces on target servers
- Sets up management network interfaces
- Configures Kubernetes cluster network interfaces  
- Sets up Ceph storage network interfaces

### Network Templates
- Uses Netplan YAML configuration for Ubuntu network setup
- Configures static IP assignments for each network
- Manages network interface bonding and VLANs if required

## Templates

- `00-unified-netcfg.yaml.j2` - Unified Netplan configuration for all networks

## Variables Required

The template expects the following variables to be defined:

- `hostname` - Target server hostname
- `ip` - Management network IP address  
- `k8s_ip` - Kubernetes cluster network IP address
- `ceph_ip` - Ceph storage network IP address (currently undefined - needs to be added)

## Dependencies

- common role (for basic system setup)
- Requires target servers to be accessible via SSH

## Handlers

- `restart networking` - Restarts networking services when configuration changes

## Status

⚠️ **Currently Not Used**: This role is defined but not included in the main `site.yml` playbook.

## Issues

1. **Missing Variables**: The template references `ceph_ip` variable which is not defined in the role's vars file
2. **Not Included**: Role is not included in the main playbook execution
3. **Incomplete Implementation**: Role lacks proper task definitions for applying network configurations

## Recommended Actions

1. **Define Missing Variables**: Add `ceph_ip` variable definitions
2. **Include in Playbook**: Add role to `site.yml` if network configuration is needed
3. **Complete Implementation**: Add tasks to apply network configurations to target servers
4. **Alternative**: Remove role if network configuration is handled elsewhere

## Network Architecture

The role is designed to support a multi-network architecture:
- **Management Network**: Primary provisioning and management traffic
- **Kubernetes Network**: Pod-to-pod and service communication  
- **Ceph Network**: Distributed storage replication and client traffic

This separation provides network isolation and performance optimization for different traffic types in a Kubernetes cluster with Ceph storage.