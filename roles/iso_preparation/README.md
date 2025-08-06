# ISO Preparation Role

This role handles downloading and preparing Ubuntu ISO images for the provisioning process.

## Purpose

The iso_preparation role manages the download, mounting, and preparation of Ubuntu server ISO images that will be used for network-based installations via the provisioning server.

## Tasks

### ISO Management
- Downloads Ubuntu server ISO images if not already present
- Creates mount points for ISO images
- Manages ISO storage and organization
- Prepares ISO content for network serving

### Storage Management
- Creates dedicated directories for ISO storage
- Manages disk space for ISO images
- Organizes downloaded content for web serving

## Variables

Located in `vars/main.yml`:

### ISO Configuration
- `ubuntu_iso_url` - URL for downloading Ubuntu server ISO
- `ubuntu_iso_name` - Filename for the Ubuntu ISO image
- `ubuntu_iso_download_dir` - Directory for storing downloaded ISOs
- `ubuntu_iso_mount_point` - Mount point for ISO images
- `ubuntu_provisioning_dir` - Directory for provisioning content extracted from ISOs

## Dependencies

- common role (for basic system setup)
- web role (for serving ISO content via HTTP)

## Files Created

- ISO download directory with downloaded Ubuntu images
- Mount points for accessing ISO content
- Extracted provisioning content directories

## Tags

- `iso` - ISO-related tasks
- `storage` - Storage management tasks

## Notes

- Handles multiple Ubuntu versions if configured
- Manages disk space efficiently
- Integrates with web role to serve ISO content
- Supports automated ISO downloading with validation

## Typical ISO Workflow

1. Downloads latest Ubuntu server ISO from configured URL
2. Creates mount point and storage directories  
3. Mounts ISO to extract necessary files
4. Organizes content for network serving
5. Integrates with autoinstall process for network installations

This role ensures that the provisioning server has access to the necessary Ubuntu installation media for deploying to target servers via network boot.