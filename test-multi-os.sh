#!/bin/bash
# Multi-OS Provisioning Test Script
# Tests Ubuntu 24.04 and Proxmox VE 9 configurations

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Test configuration
PROVISIONING_IP="10.10.1.1"
WEB_ROOT="/var/www/html"

log() {
    echo -e "${BLUE}[$(date +'%Y-%m-%d %H:%M:%S')] $1${NC}"
}

success() {
    echo -e "${GREEN}✓ $1${NC}"
}

error() {
    echo -e "${RED}✗ $1${NC}"
}

warning() {
    echo -e "${YELLOW}⚠ $1${NC}"
}

# Test functions
test_web_service() {
    log "Testing web service availability..."
    
    if curl -s -o /dev/null -w "%{http_code}" "http://${PROVISIONING_IP}" | grep -q "200"; then
        success "Web service is responding"
    else
        error "Web service is not responding"
        return 1
    fi
}

test_ubuntu_config() {
    log "Testing Ubuntu 24.04 configuration..."
    
    local ubuntu_dir="${WEB_ROOT}/provisioning/ubuntu24.04"
    local autoinstall_dir="${WEB_ROOT}/autoinstall_configs/ubuntu2404_default"
    
    # Check directories exist
    if [[ -d "$ubuntu_dir" ]]; then
        success "Ubuntu provisioning directory exists"
    else
        error "Ubuntu provisioning directory missing: $ubuntu_dir"
        return 1
    fi
    
    if [[ -d "$autoinstall_dir" ]]; then
        success "Ubuntu autoinstall config directory exists"
    else
        error "Ubuntu autoinstall config directory missing: $autoinstall_dir"
        return 1
    fi
    
    # Check autoinstall files
    local user_data="${autoinstall_dir}/user-data"
    local meta_data="${autoinstall_dir}/meta-data"
    
    if [[ -f "$user_data" ]]; then
        success "Ubuntu user-data file exists"
        
        # Validate YAML syntax
        if python3 -c "import yaml; yaml.safe_load(open('$user_data'))" 2>/dev/null; then
            success "Ubuntu user-data YAML is valid"
        else
            error "Ubuntu user-data YAML is invalid"
            return 1
        fi
        
        # Check for required sections
        if grep -q "autoinstall:" "$user_data"; then
            success "Ubuntu autoinstall directive found"
        else
            error "Ubuntu autoinstall directive missing"
            return 1
        fi
    else
        error "Ubuntu user-data file missing: $user_data"
        return 1
    fi
    
    if [[ -f "$meta_data" ]]; then
        success "Ubuntu meta-data file exists"
    else
        error "Ubuntu meta-data file missing: $meta_data"
        return 1
    fi
    
    # Test HTTP accessibility
    if curl -s "http://${PROVISIONING_IP}/autoinstall_configs/ubuntu2404_default/user-data" | grep -q "autoinstall:"; then
        success "Ubuntu user-data accessible via HTTP"
    else
        error "Ubuntu user-data not accessible via HTTP"
        return 1
    fi
}

test_proxmox_config() {
    log "Testing Proxmox VE 9 configuration..."
    
    local proxmox_dir="${WEB_ROOT}/provisioning/proxmox9"
    local autoinstall_dir="${WEB_ROOT}/autoinstall_configs/proxmox9_default"
    
    # Check directories exist
    if [[ -d "$proxmox_dir" ]]; then
        success "Proxmox provisioning directory exists"
    else
        error "Proxmox provisioning directory missing: $proxmox_dir"
        return 1
    fi
    
    if [[ -d "$autoinstall_dir" ]]; then
        success "Proxmox autoinstall config directory exists"
    else
        error "Proxmox autoinstall config directory missing: $autoinstall_dir"
        return 1
    fi
    
    # Check answer.toml file
    local answer_toml="${autoinstall_dir}/answer.toml"
    
    if [[ -f "$answer_toml" ]]; then
        success "Proxmox answer.toml file exists"
        
        # Validate TOML syntax (basic check)
        if python3 -c "import tomllib; tomllib.load(open('$answer_toml', 'rb'))" 2>/dev/null; then
            success "Proxmox answer.toml syntax is valid"
        else
            # Fallback check for basic TOML structure
            if grep -q "^\[global\]" "$answer_toml" && grep -q "^\[disk-setup\]" "$answer_toml"; then
                success "Proxmox answer.toml structure looks valid"
            else
                error "Proxmox answer.toml structure is invalid"
                return 1
            fi
        fi
        
        # Check for required sections
        if grep -q "\[global\]" "$answer_toml" && grep -q "\[disk-setup\]" "$answer_toml"; then
            success "Proxmox answer.toml required sections found"
        else
            error "Proxmox answer.toml missing required sections"
            return 1
        fi
        
        # Check disk configuration
        if grep -q 'disk_list.*=.*\[.*\]' "$answer_toml"; then
            success "Proxmox disk configuration found"
        else
            error "Proxmox disk configuration missing"
            return 1
        fi
    else
        error "Proxmox answer.toml file missing: $answer_toml"
        return 1
    fi
    
    # Test HTTP accessibility
    if curl -s "http://${PROVISIONING_IP}/autoinstall_configs/proxmox9_default/answer.toml" | grep -q "\[global\]"; then
        success "Proxmox answer.toml accessible via HTTP"
    else
        error "Proxmox answer.toml not accessible via HTTP"
        return 1
    fi
}

test_tftp_boot() {
    log "Testing TFTP boot configuration..."
    
    local tftp_root="/var/lib/tftpboot"
    
    # Check TFTP root exists
    if [[ -d "$tftp_root" ]]; then
        success "TFTP root directory exists"
    else
        error "TFTP root directory missing: $tftp_root"
        return 1
    fi
    
    # Check for PXE boot files
    local pxelinux_cfg="${tftp_root}/pxelinux.cfg"
    if [[ -d "$pxelinux_cfg" ]]; then
        success "PXE configuration directory exists"
        
        # Check for default config
        if [[ -f "${pxelinux_cfg}/default" ]]; then
            success "PXE default configuration exists"
        else
            warning "PXE default configuration missing"
        fi
    else
        error "PXE configuration directory missing: $pxelinux_cfg"
        return 1
    fi
    
    # Test TFTP service
    if systemctl is-active --quiet dnsmasq; then
        success "TFTP service (dnsmasq) is running"
        
        # Check TFTP port
        if netstat -ulnp | grep -q ":69.*dnsmasq"; then
            success "TFTP port 69 is listening"
        else
            error "TFTP port 69 is not listening"
            return 1
        fi
    else
        error "TFTP service (dnsmasq) is not running"
        return 1
    fi
}

test_php_configuration() {
    log "Testing PHP configuration and OS support..."
    
    # Test PHP syntax
    if php -l "${WEB_ROOT}/index.php" >/dev/null 2>&1; then
        success "PHP syntax is valid"
    else
        error "PHP syntax errors found"
        return 1
    fi
    
    # Test OS configuration via PHP
    local php_test=$(php -r "
    include '${WEB_ROOT}/index.php';
    if (isset(\$supported_os['ubuntu2404'])) {
        echo 'ubuntu2404:OK ';
    }
    if (isset(\$supported_os['proxmox9'])) {
        echo 'proxmox9:OK';
    }
    ")
    
    if [[ "$php_test" == *"ubuntu2404:OK"* ]]; then
        success "Ubuntu 24.04 configuration found in PHP"
    else
        error "Ubuntu 24.04 configuration missing in PHP"
        return 1
    fi
    
    if [[ "$php_test" == *"proxmox9:OK"* ]]; then
        success "Proxmox VE 9 configuration found in PHP"
    else
        error "Proxmox VE 9 configuration missing in PHP"
        return 1
    fi
}

test_kernel_parameters() {
    log "Testing kernel parameters configuration..."
    
    # Check Ubuntu kernel params
    local ubuntu_params=$(grep -r "ip=dhcp" "${WEB_ROOT}" 2>/dev/null || true)
    if [[ -n "$ubuntu_params" ]]; then
        success "Ubuntu kernel parameters configured"
    else
        warning "Ubuntu kernel parameters not found"
    fi
    
    # Check Proxmox kernel params
    local proxmox_params=$(grep -r "proxmox-start-auto-installer" "${WEB_ROOT}" 2>/dev/null || true)
    if [[ -n "$proxmox_params" ]]; then
        success "Proxmox kernel parameters configured"
        
        # Check for answer.toml reference
        if echo "$proxmox_params" | grep -q "answer.toml"; then
            success "Proxmox answer.toml reference found in kernel params"
        else
            error "Proxmox answer.toml reference missing in kernel params"
            return 1
        fi
    else
        error "Proxmox kernel parameters not found"
        return 1
    fi
}

test_network_services() {
    log "Testing network services..."
    
    # Test DHCP
    if systemctl is-active --quiet dnsmasq; then
        success "DHCP service (dnsmasq) is running"
        
        # Check DHCP port
        if netstat -ulnp | grep -q ":67.*dnsmasq"; then
            success "DHCP port 67 is listening"
        else
            error "DHCP port 67 is not listening"
            return 1
        fi
    else
        error "DHCP service (dnsmasq) is not running"
        return 1
    fi
    
    # Test DNS
    if dig @${PROVISIONING_IP} google.com +short >/dev/null 2>&1; then
        success "DNS resolution working"
    else
        error "DNS resolution not working"
        return 1
    fi
    
    # Test HTTP
    if systemctl is-active --quiet nginx; then
        success "HTTP service (nginx) is running"
    else
        error "HTTP service (nginx) is not running"
        return 1
    fi
}

# Main test execution
main() {
    log "Starting Multi-OS Provisioning Test Suite"
    log "=========================================="
    
    local failed_tests=0
    
    # Run all tests
    test_web_service || ((failed_tests++))
    echo
    
    test_ubuntu_config || ((failed_tests++))
    echo
    
    test_proxmox_config || ((failed_tests++))
    echo
    
    test_tftp_boot || ((failed_tests++))
    echo
    
    test_php_configuration || ((failed_tests++))
    echo
    
    test_kernel_parameters || ((failed_tests++))
    echo
    
    test_network_services || ((failed_tests++))
    echo
    
    # Summary
    log "Test Summary"
    log "============"
    
    if [[ $failed_tests -eq 0 ]]; then
        success "All tests passed! Multi-OS provisioning is ready."
        return 0
    else
        error "$failed_tests test(s) failed. Please check the configuration."
        return 1
    fi
}

# Run tests if script is executed directly
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    main "$@"
fi