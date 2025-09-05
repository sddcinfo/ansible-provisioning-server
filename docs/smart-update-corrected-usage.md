# Smart Update Script - Corrected Usage Guide

## Updated Tag Mappings

After reviewing the actual tags available in `site-optimized.yml`, the smart-update.sh script has been corrected to use existing tags:

### Tag Mapping Changes:

| Smart-Update Command | Old Tags (Non-existent) | New Tags (Actual) | Purpose |
|---------------------|-------------------------|-------------------|---------|
| `templates` | `quick_templates` | `templates,web_files_only` | Update template files |
| `php` | `web_php_files` | `web_files_only` | Update web application files |
| `api` | `web_api` | `web_files_only` | Update API endpoints |
| `nodes` | `web_nodes_json` | `web_files_only` | Update nodes.json |
| `scripts` | `web_scripts` | `web_files_only` | Update helper scripts |
| `perms` | `quick_perms` | `permissions` | Fix file permissions |
| `autoinstall` | `web_autoinstall` | `autoinstall_configs,templates` | All autoinstall configs |
| `ubuntu` | `web_autoinstall_ubuntu` | `autoinstall_configs` | Ubuntu autoinstall only |
| `proxmox` | `web_autoinstall_proxmox` | `autoinstall_configs` | Proxmox autoinstall only |
| `nginx` | `nginx_config` | `web_config` | Nginx configuration |
| `php-fpm` | `php_config` | `web_config` | PHP-FPM configuration |
| `dnsmasq` | `dnsmasq_config` | `netboot,dns_dhcp` | DNSMasq configuration |
| `network` | N/A (new) | `network_infra,network_setup` | Network configuration |

## Current Working Commands

### Quick Updates (5-15 seconds)
```bash
./scripts/smart-update.sh templates    # Update templates
./scripts/smart-update.sh php          # Update web files
./scripts/smart-update.sh api          # Update web files
./scripts/smart-update.sh nodes        # Update web files
./scripts/smart-update.sh scripts      # Update web files
./scripts/smart-update.sh perms        # Fix permissions
```

### Component Updates (15-30 seconds)
```bash
./scripts/smart-update.sh autoinstall  # All autoinstall configs
./scripts/smart-update.sh ubuntu       # Ubuntu configs only
./scripts/smart-update.sh proxmox      # Proxmox configs only
```

### Service Configuration (30-60 seconds)
```bash
./scripts/smart-update.sh nginx        # Web server config
./scripts/smart-update.sh php-fpm      # PHP-FPM config
./scripts/smart-update.sh dnsmasq      # DNS/DHCP config
./scripts/smart-update.sh network      # Network config
```

### Comprehensive Updates (1-15 minutes)
```bash
./scripts/smart-update.sh web-all      # All web files
./scripts/smart-update.sh web-deploy   # Deploy web files
./scripts/smart-update.sh full-web     # Complete web update
./scripts/smart-update.sh foundation   # Packages, network
./scripts/smart-update.sh services     # Service installation
./scripts/smart-update.sh expensive    # ISO downloads
./scripts/smart-update.sh full         # Complete playbook
```

## Available Tags in site-optimized.yml

From the actual playbook, these tags are available:
- `always`
- `autoinstall_config`
- `autoinstall_configs` 
- `credentials`
- `dns_config`
- `dns_dhcp`
- `docker_setup`
- `expensive`
- `final_validation`
- `foundation`
- `health_check`
- `iso_download`
- `iso_management`
- `maintenance`
- `monitoring`
- `netboot`
- `network_config_update`
- `network_infra`
- `network_setup`
- `never`
- `package_upgrade`
- `packages`
- `permissions`
- `services_install`
- `services_restart`
- `ssh_setup`
- `system_config`
- `templates`
- `validation`
- `verify_script`
- `web_config`
- `web_files_only`

## Note on Granular Tags

The original tagging improvements document described a granular tagging system with tags like:
- `web_php_files`
- `web_api`
- `web_nodes_json`
- `quick_templates`
- etc.

However, these tags were designed for a theoretical optimized playbook but were never actually implemented in `site-optimized.yml`. The smart-update.sh script has been corrected to use the actual available tags.

## Performance Expectations

| Update Type | Smart-Update Command | Time | Actual Tags Used |
|------------|---------------------|------|------------------|
| Quick web updates | `php`, `api`, `nodes`, `scripts` | 5-15 sec | `web_files_only` |
| Template updates | `templates` | 10-20 sec | `templates,web_files_only` |
| Config updates | `nginx`, `php-fmp` | 30-60 sec | `web_config` |
| Network updates | `network` | 1-2 min | `network_infra,network_setup` |
| Complete updates | `full` | 8-15 min | (all tasks) |

The performance benefits are still significant compared to running the full playbook, even though the granularity is not as fine as originally envisioned.