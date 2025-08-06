# Web Role

This role configures the nginx web server and PHP-FPM to host autoinstall configurations, provide a status dashboard, and serve provisioning-related content.

## Purpose

The web role creates a comprehensive web interface for managing and monitoring the bare-metal provisioning process, including serving Ubuntu autoinstall configurations and providing real-time status updates.

## Tasks

### Web Server Setup
- Installs and configures nginx web server
- Installs PHP-FPM and required PHP modules (php-json)
- Configures PHP-FPM process management and security settings
- Creates proper directory structure and permissions

### Content Management
- Generates dynamic PHP dashboard for node status monitoring
- Creates autoinstall configuration directories for each node
- Templates Ubuntu autoinstall user-data and meta-data files
- Sets up session management for web interface state

### Autoinstall Configuration
- Creates node-specific autoinstall directories based on MAC addresses
- Generates customized autoinstall configurations for each target server
- Provides default autoinstall configuration for unknown nodes
- Manages cloud-init user-data and meta-data templates

### Management Scripts
- Generates Redfish management script from template
- Creates provisioning verification scripts
- Sets proper executable permissions for management tools

## Variables

Located in `vars/main.yml`:

### Web Server Configuration
- `nginx_web_root: "/var/www/html"` - Document root for web content
- `nginx_server_name: "_"` - Server name (default/catch-all)
- `php_socket: "/var/run/php/php8.3-fpm.sock"` - PHP-FPM socket path

### Network Configuration
- `server_ip: "10.10.1.1"` - Provisioning server IP address
- `iso_base_url` - Base URL for ISO image serving

### Node Configuration
- `default_autoinstall_node` - Default node configuration for unknown MAC addresses
- `sysladmin_password` - Hashed password for provisioned systems

## Dependencies

- common role (for SSH keys and basic setup)
- netboot role (for node configurations)

## Templates

### Web Interface Templates
- `index.php.j2` - Main dashboard PHP application
- `nginx.conf.j2` - Nginx virtual host configuration
- `www.conf.j2` - PHP-FPM pool configuration

### Autoinstall Templates  
- `autoinstall-user-data.j2` - Ubuntu autoinstall user-data configuration
- `autoinstall-meta-data.j2` - Cloud-init meta-data configuration

### Management Script Templates
- `../../templates/redfish.py.j2` - Redfish API management script
- `../../templates/verify_provisioning.py.j2` - Provisioning verification script

## Handlers

- `restart nginx` - Restarts nginx when configuration changes
- `restart php-fpm` - Restarts PHP-FPM when configuration changes

## Files Created

### Web Content
- `/var/www/html/index.php` - Main dashboard application
- `/var/www/html/state.json` - Node status tracking file
- `/var/www/html/sessions/` - Session storage directory

### Autoinstall Configurations
- `/var/www/html/autoinstall_configs/<mac>/user-data` - Per-node user-data
- `/var/www/html/autoinstall_configs/<mac>/meta-data` - Per-node meta-data
- `/var/www/html/autoinstall_configs/default/user-data` - Default user-data
- `/var/www/html/autoinstall_configs/default/meta-data` - Default meta-data

### Management Scripts
- `/home/sysladmin/redfish.py` - Redfish API management script
- `/home/sysladmin/verify_provisioning.py` - Provisioning verification script

## Tags

- `nginx` - Nginx web server configuration
- `php` - PHP-FPM configuration
- `www_content` - Web content and dashboard setup
- `autoinstall_configs` - Autoinstall configuration generation
- `redfish_script` - Redfish script generation
- `verify_script` - Verification script generation

## Dashboard Features

The web dashboard provides:
- Real-time node status display (`NEW`, `INSTALLING`, `DONE`, `FAILED`)
- Timestamp tracking for status updates  
- Reprovisioning functionality to reset node status
- Direct access to autoinstall configurations
- Manual refresh capabilities

## Notes

- Creates separate autoinstall directories for each node's MAC address
- Includes duplicate task definitions (lines 195-203 duplicate 186-194) - should be cleaned up
- Properly manages file ownership and permissions for web content
- Supports both node-specific and default autoinstall configurations