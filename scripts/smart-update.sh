#!/bin/bash
#
# Smart Update Script for Ansible Provisioning Server
# Provides granular control over component updates
#

PLAYBOOK_DIR="/home/sysadmin/claude/ansible-provisioning-server"
PLAYBOOK="site-optimized.yml"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Function to print colored output
print_status() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

# Function to run ansible-playbook with timing
run_playbook() {
    local tags=$1
    local skip_tags=${2:-}
    
    cd "$PLAYBOOK_DIR" || exit 1
    
    print_status "Starting update with tags: $tags"
    
    if [ -n "$skip_tags" ]; then
        time ansible-playbook "$PLAYBOOK" --tags "$tags" --skip-tags "$skip_tags" -v
    else
        time ansible-playbook "$PLAYBOOK" --tags "$tags" -v
    fi
    
    local result=$?
    
    if [ $result -eq 0 ]; then
        print_status "Update completed successfully!"
    else
        print_error "Update failed with exit code: $result"
        exit $result
    fi
}

# Function to show help
show_help() {
    cat << EOF
Smart Update Script for Ansible Provisioning Server

Usage: $0 [OPTION] [COMPONENT]

EFFICIENT UPDATES (30-90 seconds):
  Note: Granular "quick" updates (php/api/nodes/scripts) removed - they were misleading.
  All web file changes should use web-deploy (same performance, honest scope).

AUTOINSTALL UPDATES:
  autoinstall    Update all autoinstall configs
  ubuntu         Update Ubuntu autoinstall configs only
  proxmox        Update Proxmox autoinstall configs only

SERVICE CONFIGS:
  nginx          Update Nginx configuration
  php-fpm        Update PHP-FPM configuration
  dnsmasq        Update DNSMasq configuration
  network        Update network configuration

COMPREHENSIVE UPDATES:
  web-all        Update all web files (no service configs)
  web-deploy     Deploy all web files without restart
  full-web       Complete web update with configs

FULL SYSTEM UPDATES:
  full           Run complete playbook (all roles and tasks)
  foundation     Run foundation setup (packages, network, common)
  services       Install and configure all services
  expensive      Run expensive operations (ISO downloads, etc.)

SPECIAL OPTIONS:
  --dry-run      Run in check mode (no changes)
  --list-tags    Show all available tags
  --help         Show this help message

Examples:
  $0 web-deploy             # Deploy all web files (1-2 minutes)
  $0 network                # Update network config (30-60 seconds)
  $0 ubuntu                 # Update Ubuntu configs only (1-2 minutes)
  $0 full                   # Run complete playbook (8-15 minutes)
  $0 foundation             # Foundation setup only (3-5 minutes)
  $0 web-all --dry-run     # Test full web update

Performance Notes:
  - Network/Service configs: 30-90 seconds
  - Component updates: 1-2 minutes  
  - Comprehensive updates: 2-5 minutes
  - Full system updates: 8-15 minutes
EOF
}

# Main script logic
case "${1:-}" in
    # Note: Granular updates like php/api/nodes removed - they all ran the same 54 tasks as web-deploy
    # Use web-deploy for any web file updates (same performance, honest naming)
    
    # Autoinstall updates
    autoinstall)
        print_status "Updating all autoinstall configs..."
        run_playbook "autoinstall_configs,templates"
        ;;
    ubuntu)
        print_status "Updating Ubuntu autoinstall configs..."
        run_playbook "autoinstall_configs"
        ;;
    proxmox)
        print_status "Updating Proxmox autoinstall configs..."
        run_playbook "autoinstall_configs"
        ;;
    
    # Service configs
    nginx)
        print_status "Updating Nginx configuration..."
        run_playbook "web_config"
        ;;
    php-fpm)
        print_status "Updating PHP-FPM configuration..."
        run_playbook "web_config"
        ;;
    dnsmasq)
        print_status "Updating DNSMasq configuration..."
        run_playbook "netboot,dns_dhcp"
        ;;
    network)
        print_status "Updating network configuration..."
        run_playbook "network_config_update"
        ;;
    
    # Comprehensive updates
    web-all)
        print_status "Updating all web files (excluding service configs)..."
        run_playbook "web_files_only,templates"
        ;;
    web-deploy)
        print_status "Deploying all web files without service restart..."
        run_playbook "web_files_only"
        ;;
    full-web)
        print_status "Complete web update with configurations..."
        run_playbook "web_config,web_files_only"
        ;;
    
    # Full system updates
    full)
        print_status "Running complete playbook (all roles and tasks)..."
        cd "$PLAYBOOK_DIR" || exit 1
        time ansible-playbook "$PLAYBOOK" -v
        local result=$?
        if [ $result -eq 0 ]; then
            print_status "Complete playbook execution successful!"
        else
            print_error "Complete playbook execution failed with exit code: $result"
            exit $result
        fi
        ;;
    foundation)
        print_status "Running foundation setup (packages, network, common)..."
        run_playbook "foundation"
        ;;
    services)
        print_status "Installing and configuring all services..."
        run_playbook "services_install"
        ;;
    expensive)
        print_status "Running expensive operations (ISO downloads, etc.)..."
        run_playbook "expensive"
        ;;
    
    # Special options
    --dry-run)
        if [ -z "${2:-}" ]; then
            print_error "Please specify a component to dry-run"
            exit 1
        fi
        print_warning "Running in CHECK MODE - no changes will be made"
        cd "$PLAYBOOK_DIR" || exit 1
        ansible-playbook "$PLAYBOOK" --tags "${2}" --check -v
        ;;
    --list-tags)
        print_status "Available tags in playbook:"
        cd "$PLAYBOOK_DIR" || exit 1
        ansible-playbook "$PLAYBOOK" --list-tags
        ;;
    --help|help|-h)
        show_help
        ;;
    *)
        if [ -n "${1:-}" ]; then
            print_error "Unknown option: $1"
            echo ""
        fi
        show_help
        exit 1
        ;;
esac

# Show execution time summary
if [ $? -eq 0 ] && [ "${1:-}" != "--help" ] && [ "${1:-}" != "help" ] && [ "${1:-}" != "-h" ] && [ "${1:-}" != "--list-tags" ]; then
    echo ""
    print_status "Update completed successfully!"
    echo "Run '$0 --help' to see all available options"
fi