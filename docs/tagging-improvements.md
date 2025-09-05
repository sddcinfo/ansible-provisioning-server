# Enhanced Tagging Strategy for Ansible Provisioning Server

## Current Issues with Tagging

1. **Overly broad tags** - `web_files_only` updates ALL web files, not specific components
2. **Mixed concerns** - Single tags cover both installation and configuration
3. **No granular control** - Can't update specific file types (e.g., just PHP files or just templates)
4. **Inefficient re-runs** - Updating one template requires processing all web tasks

## Improved Tagging Hierarchy

### Layer 1: Component Tags (Broad)
```yaml
- web          # All web-related tasks
- network      # All network-related tasks
- iso          # All ISO-related tasks
- validation   # All validation tasks
```

### Layer 2: Operation Tags (Medium)
```yaml
- install      # Package installations only
- configure    # Service configurations only
- deploy       # File deployments only
- validate     # Validation checks only
```

### Layer 3: Granular Tags (Specific)
```yaml
# Web file categories
- web_php_files        # PHP application files only
- web_templates        # Template files only
- web_autoinstall      # Autoinstall configs only
- web_api              # API endpoints only
- web_scripts          # Helper scripts only
- web_static           # Static assets only
- web_nodes_json       # nodes.json updates only

# Service-specific
- nginx_config         # Nginx configuration only
- php_config          # PHP-FPM configuration only
- dnsmasq_config      # Dnsmasq configuration only

# Quick operations
- quick_templates     # Just template regeneration
- quick_perms         # Just permission fixes
```

## Optimized Web Role with Granular Tags

```yaml
---
# roles/web/tasks/main.yml - OPTIMIZED VERSION

- name: Install web packages
  apt:
    name:
      - nginx
      - php-fpm
      - php-json
      - python3
      - python3-pip
      - python3-requests
    state: present
  tags:
    - web
    - install
    - never  # Skip by default, handled in main playbook

- name: Configure PHP-FPM
  template:
    src: www.conf.j2
    dest: /etc/php/8.3/fpm/pool.d/www.conf
  notify: restart php-fpm
  tags:
    - web
    - configure
    - php_config

- name: Configure Nginx
  template:
    src: nginx.conf.j2
    dest: /etc/nginx/sites-available/default
  notify: restart nginx
  tags:
    - web
    - configure
    - nginx_config

# PHP Application Files
- name: Deploy PHP application files
  copy:
    src: "{{ item }}"
    dest: "/var/www/html/{{ item | basename }}"
    owner: www-data
    group: www-data
    mode: "0644"
  loop:
    - index.php
    - pxe-boot.php
    - lib/Database.php
    - lib/StateManager.php
  tags:
    - web
    - deploy
    - web_php_files
    - quick_deploy

# Template Files
- name: Deploy template files
  template:
    src: "{{ item.src }}"
    dest: "{{ item.dest }}"
    owner: www-data
    group: www-data
    mode: "0644"
  loop:
    - { src: ipxe_boot.php.j2, dest: /var/www/html/ipxe_boot.php }
    - { src: grub.cfg.php.j2, dest: /var/www/html/grub.cfg.php }
  tags:
    - web
    - deploy
    - web_templates
    - quick_templates

# nodes.json Management
- name: Deploy nodes.json with SSH key
  block:
    - name: Read management SSH key
      slurp:
        src: /home/sysadmin/.ssh/sysadmin_automation_key.pub
      register: ssh_key
      
    - name: Update and deploy nodes.json
      template:
        src: nodes.json.j2
        dest: /var/www/html/nodes.json
        owner: www-data
        group: www-data
        mode: "0644"
  tags:
    - web
    - deploy
    - web_nodes_json
    - quick_deploy

# Autoinstall Configurations
- name: Create autoinstall directories
  file:
    path: "/var/www/html/autoinstall_configs/{{ item }}"
    state: directory
    owner: www-data
    group: www-data
    mode: "0755"
  loop:
    - ubuntu
    - proxmox
  tags:
    - web
    - deploy
    - web_autoinstall

- name: Deploy Ubuntu autoinstall configs
  template:
    src: "autoinstall/ubuntu/{{ item }}.j2"
    dest: "/var/www/html/autoinstall_configs/ubuntu/{{ item }}"
    owner: www-data
    group: www-data
    mode: "0644"
  loop:
    - user-data-default
    - user-data-2404
    - user-data-2204
    - meta-data
  tags:
    - web
    - deploy
    - web_autoinstall
    - web_autoinstall_ubuntu
    - quick_templates

- name: Deploy Proxmox autoinstall configs
  template:
    src: "autoinstall/proxmox/{{ item }}.j2"
    dest: "/var/www/html/autoinstall_configs/proxmox/{{ item }}"
    owner: www-data
    group: www-data
    mode: "0644"
  loop:
    - answer.toml
  tags:
    - web
    - deploy
    - web_autoinstall
    - web_autoinstall_proxmox
    - quick_templates

# API Endpoints
- name: Deploy API endpoints
  template:
    src: "api/{{ item }}.j2"
    dest: "/var/www/html/api/{{ item }}"
    owner: www-data
    group: www-data
    mode: "0644"
  loop:
    - proxmox-answer.php
    - register.php
    - status.php
  tags:
    - web
    - deploy
    - web_api
    - quick_deploy

# Helper Scripts
- name: Deploy helper scripts
  template:
    src: "scripts/{{ item }}.j2"
    dest: "/var/www/html/scripts/{{ item }}"
    owner: www-data
    group: www-data
    mode: "0755"
  loop:
    - populate_state.py
    - redfish_operations.py
    - proxmox_post_install.sh
  tags:
    - web
    - deploy
    - web_scripts
    - quick_scripts

# Quick Permission Fix
- name: Fix web directory permissions
  file:
    path: /var/www/html
    state: directory
    recurse: yes
    owner: www-data
    group: www-data
  tags:
    - web
    - quick_perms
    - never  # Only run when explicitly requested

# Handlers
handlers:
  - name: restart nginx
    systemd:
      name: nginx
      state: restarted
    
  - name: restart php-fpm
    systemd:
      name: php8.3-fpm
      state: restarted
```

## Usage Examples

### 1. Update Only PHP Application Files
```bash
ansible-playbook site.yml --tags web_php_files
# Time: ~5 seconds
```

### 2. Regenerate All Templates
```bash
ansible-playbook site.yml --tags quick_templates
# Time: ~10 seconds
```

### 3. Update Ubuntu Autoinstall Configs Only
```bash
ansible-playbook site.yml --tags web_autoinstall_ubuntu
# Time: ~8 seconds
```

### 4. Quick Deploy (PHP + API + nodes.json)
```bash
ansible-playbook site.yml --tags quick_deploy
# Time: ~15 seconds
```

### 5. Fix Permissions Only
```bash
ansible-playbook site.yml --tags quick_perms
# Time: ~3 seconds
```

### 6. Update Specific Components
```bash
# Just nodes.json
ansible-playbook site.yml --tags web_nodes_json

# Just API endpoints
ansible-playbook site.yml --tags web_api

# Just helper scripts
ansible-playbook site.yml --tags web_scripts
```

### 7. Configuration Updates Only
```bash
# Update nginx config
ansible-playbook site.yml --tags nginx_config

# Update PHP config
ansible-playbook site.yml --tags php_config
```

## Enhanced Update Script

```bash
#!/bin/bash
# scripts/smart-update.sh

case "$1" in
    templates)
        echo "Updating templates only..."
        ansible-playbook site.yml --tags quick_templates
        ;;
    php)
        echo "Updating PHP files..."
        ansible-playbook site.yml --tags web_php_files
        ;;
    api)
        echo "Updating API endpoints..."
        ansible-playbook site.yml --tags web_api
        ;;
    autoinstall)
        echo "Updating autoinstall configs..."
        ansible-playbook site.yml --tags web_autoinstall
        ;;
    nodes)
        echo "Updating nodes.json..."
        ansible-playbook site.yml --tags web_nodes_json
        ;;
    ubuntu)
        echo "Updating Ubuntu configs..."
        ansible-playbook site.yml --tags web_autoinstall_ubuntu
        ;;
    proxmox)
        echo "Updating Proxmox configs..."
        ansible-playbook site.yml --tags web_autoinstall_proxmox
        ;;
    perms)
        echo "Fixing permissions..."
        ansible-playbook site.yml --tags quick_perms
        ;;
    all-web)
        echo "Updating all web files..."
        ansible-playbook site.yml --tags "web,deploy"
        ;;
    *)
        echo "Usage: $0 {templates|php|api|autoinstall|nodes|ubuntu|proxmox|perms|all-web}"
        echo ""
        echo "Examples:"
        echo "  $0 templates    # Regenerate all template files"
        echo "  $0 php          # Update PHP application files"
        echo "  $0 nodes        # Update nodes.json with SSH keys"
        echo "  $0 ubuntu       # Update Ubuntu autoinstall configs"
        echo "  $0 perms        # Fix file permissions"
        exit 1
        ;;
esac

echo "Update complete!"
```

## Performance Comparison

| Operation | Old Method | New Method | Time Saved |
|-----------|------------|------------|------------|
| Update single template | Run all web tasks | Run specific tag | ~45 seconds |
| Update nodes.json | `--tags web_files_only` (all files) | `--tags web_nodes_json` | ~30 seconds |
| Fix permissions | Full web role | `--tags quick_perms` | ~40 seconds |
| Update PHP files | Full web deployment | `--tags web_php_files` | ~35 seconds |
| Update autoinstall | All templates | `--tags web_autoinstall` | ~25 seconds |

## Implementation Checklist

- [ ] Backup current web role: `cp -r roles/web roles/web-backup`
- [ ] Implement granular tags in web/tasks/main.yml
- [ ] Update site.yml to support new tag structure
- [ ] Create smart-update.sh helper script
- [ ] Test each tag combination
- [ ] Document new tags in README
- [ ] Update team documentation

## Tag Naming Conventions

1. **Component prefix**: Always start with component name (web_, network_, iso_)
2. **Action middle**: Include action type (install, configure, deploy)
3. **Specific suffix**: End with specific resource type
4. **Quick prefix**: For common operations needing speed

Examples:
- `web_autoinstall_ubuntu` - Component_Resource_Specific
- `quick_templates` - Speed_Resource
- `nginx_config` - Service_Action

## Advanced Tag Combinations

```bash
# Deploy everything except service configs
ansible-playbook site.yml --tags deploy --skip-tags configure

# Update all templates across all components
ansible-playbook site.yml --tags "*templates*"

# Quick web updates without service restarts
ansible-playbook site.yml --tags "quick_*" --skip-tags configure
```

## Monitoring Tag Performance

Add timing callback to ansible.cfg:
```ini
[defaults]
callback_whitelist = timer, profile_tasks
stdout_callback = yaml
```

Then run with timing:
```bash
ansible-playbook site.yml --tags web_php_files -v
```

This will show exact timing for each task, helping identify further optimization opportunities.