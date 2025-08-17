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
check_file_content "DHCP option 250 configured" "/etc/dnsmasq.d/provisioning.conf" "dhcp-option=250,http://10.10.1.1/sessions/answer.toml"
echo

echo "=== File Structure ==="
check_and_report "Proxmox kernel exists" "test -f /var/www/html/provisioning/proxmox9/boot/linux26"
check_and_report "Proxmox initrd exists" "test -f /var/www/html/provisioning/proxmox9/boot/initrd.img"
check_and_report "Proxmox ISO exists" "test -f /var/www/html/provisioning/proxmox9/proxmox-ve_9.0-1.iso"
check_and_report "answer.toml exists" "test -f /var/www/html/sessions/answer.toml"
echo

echo "=== Web Server Configuration ==="
check_and_report "index.php accessible" "curl -s -o /dev/null -w '%{http_code}' http://10.10.1.1/index.php?mac=ac:1f:6b:6c:5a:28 | grep -q 200"
check_and_report "answer.toml accessible" "curl -s -o /dev/null -w '%{http_code}' http://10.10.1.1/sessions/answer.toml | grep -q 200"
check_and_report "kernel accessible" "curl -s -o /dev/null -w '%{http_code}' http://10.10.1.1/provisioning/proxmox9/boot/linux26 | grep -q 200"
check_and_report "initrd accessible" "curl -s -o /dev/null -w '%{http_code}' http://10.10.1.1/provisioning/proxmox9/boot/initrd.img | grep -q 200"
echo

echo "=== PHP Configuration ==="
check_file_content "correct kernel parameters in PHP" "/var/www/html/index.php" "ro ramdisk_size=16777216 rw quiet splash=silent proxmox-start-auto-installer"
echo

echo "=== Answer File Configuration ==="
check_file_content "correct TOML format (root-password)" "/var/www/html/sessions/answer.toml" "root-password"
check_file_content "ZFS configuration" "/var/www/html/sessions/answer.toml" "filesystem = \"zfs\""
check_file_content "DHCP networking" "/var/www/html/sessions/answer.toml" "source = \"from-dhcp\""
echo

echo "=== Critical initrd Content Verification ==="
echo -n "Checking if initrd contains embedded ISO... "
TEMP_DIR=$(mktemp -d)
cd "$TEMP_DIR"
zstd -dc /var/www/html/provisioning/proxmox9/boot/initrd.img | cpio -i --quiet 2>/dev/null
if [ -f "proxmox.iso" ]; then
    echo -e "${GREEN}OK${NC}"
    echo "  ISO found as 'proxmox.iso' (correct naming)"
else
    echo -e "${RED}FAILED${NC}"
    echo "  ISO not found or incorrectly named in initrd"
    ERRORS=$((ERRORS + 1))
fi

echo -n "Checking if initrd contains embedded answer.toml... "
if [ -f "proxmox-auto-installer/answer.toml" ]; then
    echo -e "${GREEN}OK${NC}"
    echo "  answer.toml found in proxmox-auto-installer directory"
    
    # Verify answer.toml format
    echo -n "Checking embedded answer.toml format... "
    if grep -q "root-password" "proxmox-auto-installer/answer.toml"; then
        echo -e "${GREEN}OK${NC}"
    else
        echo -e "${RED}FAILED${NC}"
        echo "  Embedded answer.toml uses incorrect format"
        ERRORS=$((ERRORS + 1))
    fi
else
    echo -e "${RED}FAILED${NC}"
    echo "  answer.toml not found in initrd"
    ERRORS=$((ERRORS + 1))
fi

cd - > /dev/null
rm -rf "$TEMP_DIR"
echo

echo "=== iPXE Boot Test ==="
echo -n "Testing iPXE response... "
IPXE_RESPONSE=$(curl -s "http://10.10.1.1/index.php?mac=ac:1f:6b:6c:5a:28")
if echo "$IPXE_RESPONSE" | grep -q "#!ipxe" && echo "$IPXE_RESPONSE" | grep -q "proxmox-start-auto-installer" && ! echo "$IPXE_RESPONSE" | grep -q "proxmox-fetch-answer"; then
    echo -e "${GREEN}OK${NC}"
    echo "  iPXE script contains correct parameters (clean kernel command line)"
else
    echo -e "${RED}FAILED${NC}"
    echo "  iPXE response malformed or contains incorrect parameters"
    ERRORS=$((ERRORS + 1))
fi
echo

echo "=== Summary ==="
if [ $ERRORS -eq 0 ]; then
    echo -e "${GREEN} All checks passed! Proxmox iPXE configuration is ready.${NC}"
    echo
    echo "The configuration includes all critical fixes:"
    echo "- Correct kernel parameters for auto-installer activation"
    echo "- Properly named ISO file (proxmox.iso) in initrd"
    echo "- Correct TOML format with root-password (kebab-case)"
    echo "- DHCP option 250 for answer file discovery"
    echo "- Multiple fallback mechanisms for configuration"
else
    echo -e "${RED} $ERRORS check(s) failed. Please review the issues above.${NC}"
    exit 1
fi