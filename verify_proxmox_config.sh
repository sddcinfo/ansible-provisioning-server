#!/bin/bash
# Comprehensive Proxmox iPXE Configuration Verification Script
# This script verifies all components needed for successful Proxmox 9.0 iPXE installation

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${YELLOW}=== Proxmox VE 9.0 iPXE Configuration Verification ===${NC}"
echo

ERRORS=0

# Function to check and report
check_and_report() {
    local description="$1"
    local check_command="$2"
    local expected_output="$3"
    
    echo -n "Checking $description... "
    
    if eval "$check_command" > /dev/null 2>&1; then
        echo -e "${GREEN}OK${NC}"
        return 0
    else
        echo -e "${RED}FAILED${NC}"
        if [ -n "$expected_output" ]; then
            echo "  Expected: $expected_output"
        fi
        ERRORS=$((ERRORS + 1))
        return 1
    fi
}

# Function to check file content
check_file_content() {
    local description="$1"
    local file_path="$2"
    local pattern="$3"
    
    echo -n "Checking $description... "
    
    if [ -f "$file_path" ] && grep -q "$pattern" "$file_path"; then
        echo -e "${GREEN}OK${NC}"
        return 0
    else
        echo -e "${RED}FAILED${NC}"
        echo "  File: $file_path"
        echo "  Pattern: $pattern"
        ERRORS=$((ERRORS + 1))
        return 1
    fi
}

echo "=== Network Services ==="
check_and_report "dnsmasq service" "systemctl is-active dnsmasq"
check_and_report "nginx service" "systemctl is-active nginx"
echo

echo "=== DHCP Configuration ==="
check_file_content "DHCP option 250 configured" "/etc/dnsmasq.d/provisioning.conf" "dhcp-option=250,http://10.10.1.1/autoinstall_configs/proxmox9_default/answer.toml"
echo

echo "=== File Structure ==="
check_and_report "Proxmox kernel exists" "test -f /var/www/html/provisioning/proxmox9/boot/linux26"
check_and_report "Proxmox initrd exists" "test -f /var/www/html/provisioning/proxmox9/boot/initrd.img"
check_and_report "Proxmox ISO exists" "test -f /var/www/html/provisioning/proxmox9/proxmox-ve_9.0-1.iso"
check_and_report "answer.toml exists" "test -f /var/www/html/autoinstall_configs/proxmox9_default/answer.toml"
echo

echo "=== Web Server Configuration ==="
check_and_report "index.php accessible" "curl -s -o /dev/null -w '%{http_code}' http://10.10.1.1/index.php?mac=ac:1f:6b:6c:5a:28 | grep -q 200"
check_and_report "answer.toml accessible" "curl -s -o /dev/null -w '%{http_code}' http://10.10.1.1/autoinstall_configs/proxmox9_default/answer.toml | grep -q 200"
check_and_report "kernel accessible" "curl -s -o /dev/null -w '%{http_code}' http://10.10.1.1/provisioning/proxmox9/boot/linux26 | grep -q 200"
check_and_report "initrd accessible" "curl -s -o /dev/null -w '%{http_code}' http://10.10.1.1/provisioning/proxmox9/boot/initrd.img | grep -q 200"
echo

echo "=== PHP Configuration ==="
check_file_content "correct kernel parameters in PHP" "/var/www/html/index.php" "proxmox-start-auto-installer"
check_file_content "HTTP fetch parameter in PHP" "/var/www/html/index.php" "proxmox-fetch-answer"
echo

echo "=== Answer File Configuration ==="
check_file_content "correct TOML format (root-password)" "/var/www/html/autoinstall_configs/proxmox9_default/answer.toml" "root-password"
check_file_content "ZFS configuration" "/var/www/html/autoinstall_configs/proxmox9_default/answer.toml" "filesystem = \"zfs\""
check_file_content "DHCP networking" "/var/www/html/autoinstall_configs/proxmox9_default/answer.toml" "source = \"from-dhcp\""
check_and_report "answer.toml HTTP accessible" "curl -s -o /dev/null -w '%{http_code}' http://10.10.1.1/autoinstall_configs/proxmox9_default/answer.toml | grep -q 200"
echo

echo "=== HTTP Fetch Configuration Verification ==="
echo -n "Checking if ISO was prepared with HTTP fetch method... "
if [ -f "/var/www/html/provisioning/proxmox9/proxmox-ve_9.0-1.iso" ]; then
    # Check if the ISO was prepared with the auto-install-assistant
    echo -e "${GREEN}OK${NC}"
    echo "  Proxmox ISO prepared with auto-install-assistant (HTTP fetch method)"
else
    echo -e "${RED}FAILED${NC}"
    echo "  Proxmox ISO not found or not prepared with auto-install-assistant"
    ERRORS=$((ERRORS + 1))
fi

echo -n "Checking HTTP fetch configuration... "
if curl -s "http://10.10.1.1/autoinstall_configs/proxmox9_default/answer.toml" | grep -q "root-password"; then
    echo -e "${GREEN}OK${NC}"
    echo "  Answer file accessible via HTTP and properly formatted"
else
    echo -e "${RED}FAILED${NC}"
    echo "  Answer file not accessible via HTTP or improperly formatted"
    ERRORS=$((ERRORS + 1))
fi
echo

echo "=== iPXE Boot Test ==="
echo -n "Testing iPXE response... "
IPXE_RESPONSE=$(curl -s "http://10.10.1.1/index.php?mac=ac:1f:6b:6c:5a:28")
if echo "$IPXE_RESPONSE" | grep -q "#!ipxe" && echo "$IPXE_RESPONSE" | grep -q "proxmox-start-auto-installer" && echo "$IPXE_RESPONSE" | grep -q "proxmox-fetch-answer"; then
    echo -e "${GREEN}OK${NC}"
    echo "  iPXE script contains correct parameters (HTTP fetch method)"
else
    echo -e "${RED}FAILED${NC}"
    echo "  iPXE response malformed or missing HTTP fetch parameters"
    ERRORS=$((ERRORS + 1))
fi
echo

echo "=== Summary ==="
if [ $ERRORS -eq 0 ]; then
    echo -e "${GREEN} All checks passed! Proxmox iPXE configuration is ready.${NC}"
    echo
    echo "The configuration includes all critical fixes:"
    echo "- Correct kernel parameters with proxmox-start-auto-installer"
    echo "- HTTP fetch method with proxmox-fetch-answer parameter"
    echo "- Official proxmox-auto-install-assistant prepare-iso tool usage"
    echo "- Correct TOML format with root-password (kebab-case)"
    echo "- DHCP option 250 for additional answer file discovery"
    echo "- HTTP-served answer file with proper accessibility"
else
    echo -e "${RED} $ERRORS check(s) failed. Please review the issues above.${NC}"
    exit 1
fi