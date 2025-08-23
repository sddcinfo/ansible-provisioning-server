#!/bin/bash
# Proxmox Cluster Formation Script - API-Based
# This is a wrapper script that calls the new API-based cluster formation
# 
# UPDATED: Now uses Proxmox API instead of SSH for cluster operations
# - More reliable and secure than SSH-based approaches
# - No SSH key management between nodes required
# - Uses API tokens created during post-install
# - Better error handling with structured responses

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
API_SCRIPT="$SCRIPT_DIR/proxmox-form-cluster-api.sh"

# Check if the API script exists
if [ ! -f "$API_SCRIPT" ]; then
    echo "ERROR: API-based cluster formation script not found: $API_SCRIPT"
    echo "Please ensure proxmox-form-cluster-api.sh is present in the scripts directory"
    exit 1
fi

# Check if the API script is executable
if [ ! -x "$API_SCRIPT" ]; then
    chmod +x "$API_SCRIPT"
fi

echo "========================================================================"
echo "Proxmox Cluster Formation - Using API Method"
echo "========================================================================"
echo "This script now uses the Proxmox REST API instead of SSH."
echo "Benefits:"
echo "- No SSH key management between nodes"
echo "- More secure with API tokens"
echo "- Better error handling"
echo "- Reliable timeout handling"
echo ""
echo "Calling API-based cluster formation script..."
echo "========================================================================"

# Execute the API-based script
exec "$API_SCRIPT" "$@"