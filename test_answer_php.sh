#!/bin/bash

# This script tests the answer.php endpoint by simulating a POST request from a Proxmox installer.

# --- Configuration ---
SERVER_IP="10.10.1.1"
API_URL="http://${SERVER_IP}/api/answer.php"
NODE_MAC="ac:1f:6b:6c:58:31"
NODE_HOSTNAME="console-node1"

# --- Test Payload ---
# This JSON payload mimics the data sent by the Proxmox installer.
JSON_PAYLOAD=$(cat <<EOF
{
  "network_interfaces": [
    {
      "link": "eth0",
      "mac": "${NODE_MAC}"
    }
  ],
  "product": {
    "fullname": "Proxmox VE"
  },
  "iso": {
    "release": "9.0"
  }
}
EOF
)

# --- Execution ---
echo "--- Testing answer.php for ${NODE_HOSTNAME} (${NODE_MAC}) ---"
echo "Sending POST data to ${API_URL}"
echo "Payload:"
echo "${JSON_PAYLOAD}"
echo
echo "--- Server Response ---"
curl -s -X POST -H "Content-Type: application/json" -d "${JSON_PAYLOAD}" "${API_URL}"
echo
echo "--- Test Complete ---"
