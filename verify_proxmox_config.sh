#!/bin/bash
# Proxmox answer.php Validation Script
# Usage: ./verify_proxmox_config.sh <node_name>
# Example: ./verify_proxmox_config.sh node1

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration
SERVER_IP="10.10.1.1"
API_URL="http://${SERVER_IP}/api/answer.php"
NODES_FILE="/var/www/html/nodes.json"

# Check if node name was provided
if [ $# -eq 0 ]; then
    echo -e "${RED}Error: No node name provided${NC}"
    echo "Usage: $0 <node_name>"
    echo "Example: $0 node1"
    echo
    echo "Available nodes:"
    if [ -f "$NODES_FILE" ]; then
        jq -r '.nodes[] | "\t- \(.os_hostname) (MAC: \(.os_mac), IP: \(.os_ip))"' "$NODES_FILE" 2>/dev/null || \
        echo "  Unable to parse nodes.json"
    else
        echo "  nodes.json not found"
    fi
    exit 1
fi

NODE_NAME="$1"

echo -e "${BLUE}=== Proxmox answer.php Validation for ${NODE_NAME} ===${NC}"
echo

# Get node information from nodes.json
if [ ! -f "$NODES_FILE" ]; then
    echo -e "${RED}Error: nodes.json not found at $NODES_FILE${NC}"
    exit 1
fi

# Extract node data using jq
NODE_DATA=$(jq -r ".nodes[] | select(.os_hostname == \"${NODE_NAME}\")" "$NODES_FILE" 2>/dev/null)

if [ -z "$NODE_DATA" ]; then
    echo -e "${RED}Error: Node '${NODE_NAME}' not found in nodes.json${NC}"
    echo "Available nodes:"
    jq -r '.nodes[] | "\t- \(.os_hostname) (MAC: \(.os_mac), IP: \(.os_ip))"' "$NODES_FILE"
    exit 1
fi

# Extract node details
OS_MAC=$(echo "$NODE_DATA" | jq -r '.os_mac')
OS_IP=$(echo "$NODE_DATA" | jq -r '.os_ip')
OS_HOSTNAME=$(echo "$NODE_DATA" | jq -r '.os_hostname')
CONSOLE_HOSTNAME=$(echo "$NODE_DATA" | jq -r '.hostname')

echo -e "${YELLOW}Node Information:${NC}"
echo "  Hostname: $OS_HOSTNAME"
echo "  MAC Address: $OS_MAC"
echo "  IP Address: $OS_IP"
echo "  Console Hostname: $CONSOLE_HOSTNAME"
echo

# Prepare POST data for answer.php
POST_DATA=$(cat <<EOF
{
  "network_interfaces": [
    {
      "link": "eno1",
      "mac": "${OS_MAC}"
    }
  ]
}
EOF
)

echo -e "${YELLOW}Testing answer.php endpoint...${NC}"
echo

# Make the request and capture response
RESPONSE=$(curl -s -X POST -H "Content-Type: application/json" -d "${POST_DATA}" "${API_URL}")

if [ -z "$RESPONSE" ]; then
    echo -e "${RED}Error: Empty response from answer.php${NC}"
    exit 1
fi

echo -e "${GREEN}Response received!${NC}"
echo
echo "=== Full Response ==="
echo "$RESPONSE"
echo
echo "=== Validation Checks ==="

ERRORS=0

# Function to check and validate
check_value() {
    local description="$1"
    local pattern="$2"
    local expected="$3"
    
    echo -n "  $description: "
    
    if echo "$RESPONSE" | grep -q "$pattern"; then
        actual=$(echo "$RESPONSE" | grep "$pattern" | sed 's/.*= *"\?\([^"]*\)"\?.*/\1/' | xargs)
        if [ "$actual" = "$expected" ]; then
            echo -e "${GREEN}✓${NC} $actual"
        else
            echo -e "${YELLOW}⚠${NC} Found: '$actual', Expected: '$expected'"
            ERRORS=$((ERRORS + 1))
        fi
    else
        echo -e "${RED}✗${NC} Not found (Expected: $expected)"
        ERRORS=$((ERRORS + 1))
    fi
}

# Validate critical fields
echo -e "${BLUE}Network Configuration:${NC}"
check_value "Network source" "^source = " "from-answer"
check_value "IP address (CIDR)" "^cidr = " "${OS_IP}/24"
check_value "Gateway" "^gateway = " "10.10.1.1"
check_value "DNS server" "^dns = " "10.10.1.1"
# Extract MAC suffix for validation (last 3 octets without colons)
MAC_SUFFIX=$(echo "$OS_MAC" | cut -d: -f4-6 | tr -d ':')
check_value "Network filter (MAC)" "^filter.ID_NET_NAME_MAC = " "*${MAC_SUFFIX}"

echo
echo -e "${BLUE}System Configuration:${NC}"
check_value "Hostname (FQDN)" "^fqdn = " "${OS_HOSTNAME}.sddc.info"
check_value "Country" "^country = " "jp"
check_value "Timezone" "^timezone = " "UTC"

echo
echo -e "${BLUE}Storage Configuration:${NC}"
check_value "Filesystem" "^filesystem = " "zfs"
check_value "RAID level" "^zfs.raid = " "raid0"

echo
echo "=== Summary ==="

if [ $ERRORS -eq 0 ]; then
    echo -e "${GREEN}✓ All validation checks passed!${NC}"
    echo
    echo "The answer.php correctly generates configuration for ${NODE_NAME}:"
    echo "  - Hostname: ${OS_HOSTNAME} (not ${CONSOLE_HOSTNAME})"
    echo "  - Static IP: ${OS_IP}/24"
    echo "  - Network filter: MAC-based (*${MAC_SUFFIX})"
    echo "  - Gateway: 10.10.1.1"
    echo "  - DNS: 10.10.1.1"
    exit 0
else
    echo -e "${RED}✗ $ERRORS validation check(s) failed${NC}"
    echo
    echo "Please review the issues above and check:"
    echo "  1. answer.php template is correctly configured"
    echo "  2. nodes.json has the correct data"
    echo "  3. PHP templates are properly deployed"
    exit 1
fi