#!/bin/bash

# Test script to verify the Ansible playbook ran successfully

# Color codes
GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# Function to print a success message
print_success() {
    echo -e "${GREEN}SUCCESS: $1${NC}"
}

# Function to print a failure message
print_failure() {
    echo -e "${RED}FAILURE: $1${NC}"
}

# --- DNS and DHCP Verification ---
echo "--- Verifying DNS and DHCP ---"
# Check if dnsmasq is listening on the correct interfaces
if echo 'password' | sudo -S netstat -tulpn | grep "dnsmasq" | grep -q "127.0.0.1:53"; then
    print_success "dnsmasq is listening on 127.0.0.1:53"
else
    print_failure "dnsmasq is not listening on 127.0.0.1:53"
fi

if echo 'password' | sudo -S netstat -tulpn | grep "dnsmasq" | grep -q "10.10.1.1:53"; then
    print_success "dnsmasq is listening on 10.10.1.1:53"
else
    print_failure "dnsmasq is not listening on 10.10.1.1:53"
fi

# Check DNS resolution
if dig @10.10.1.1 node1.sddc.info | grep -q "10.10.1.21"; then
    print_success "DNS resolution for node1.sddc.info is correct"
else
    print_failure "DNS resolution for node1.sddc.info is incorrect"
fi

# --- TFTP Verification ---
echo "--- Verifying TFTP ---"

# Check TFTP file download
echo "get ipxe.efi" | tftp 10.10.1.1 69
if [[ -f ipxe.efi ]]; then
    print_success "TFTP file download is successful"
    rm ipxe.efi
else
    print_failure "TFTP file download failed"
fi

# Check dnsmasq logs for TFTP request
if journalctl -u dnsmasq | grep -q "sent /var/lib/tftpboot/ipxe.efi"; then
    print_success "dnsmasq log shows TFTP file sent"
else
    print_failure "dnsmasq log does not show TFTP file sent"
fi


# --- Web Server Verification ---
echo "--- Verifying Web Server ---"
sleep 5
# Check if nginx is listening on port 80
if echo 'password' | sudo -S netstat -tulpn | grep "nginx" | grep -q ":80"; then
    print_success "nginx is listening on port 80"
else
    print_failure "nginx is not listening on port 80"
fi

# Check web server response
if curl -s http://10.10.1.1/ | grep -q "Ansible Provisioning Status"; then
    print_success "Web server is responding correctly"
else
    print_failure "Web server is not responding correctly"
fi

# --- NAT and IP Forwarding Verification ---
echo "--- Verifying NAT and IP Forwarding ---"
# Check IP forwarding
if [[ $(cat /proc/sys/net/ipv4/ip_forward) -eq 1 ]]; then
    print_success "IP forwarding is enabled"
else
    print_failure "IP forwarding is not enabled"
fi

# Check iptables NAT rule
if echo 'password' | sudo -S iptables -t nat -C POSTROUTING -s 10.10.1.0/24 -o ens34 -j MASQUERADE -m comment --comment 'NAT for internal network'; then
    print_success "iptables NAT rule exists"
else
    print_failure "iptables NAT rule does not exist"
fi

# --- Service Status Verification ---
echo "--- Verifying Service Status ---"
# Check that dnsmasq is running
if systemctl is-active --quiet dnsmasq; then
    print_success "dnsmasq service is running"
else
    print_failure "dnsmasq service is not running"
fi

# Check that nginx is running
if systemctl is-active --quiet nginx; then
    print_success "nginx service is running"
else
    print_failure "nginx service is not running"
fi

# Check that php-fpm is running
if systemctl is-active --quiet php8.3-fpm; then
    print_success "php-fpm service is running"
else
    print_failure "php-fpm service is not running"
fi

# Check that tftpd-hpa is stopped and disabled
if ! systemctl is-active --quiet tftpd-hpa && ! systemctl is-enabled --quiet tftpd-hpa; then
    print_success "tftpd-hpa service is stopped and disabled"
else
    print_failure "tftpd-hpa service is not stopped and disabled"
fi

# Check that systemd-resolved is stopped and disabled
if ! systemctl is-active --quiet systemd-resolved && ! systemctl is-enabled --quiet systemd-resolved; then
    print_success "systemd-resolved service is stopped and disabled"
else
    print_failure "systemd-resolved service is not stopped and disabled"
fi