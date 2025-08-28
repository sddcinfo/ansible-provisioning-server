#!/bin/bash
# Bootstrap Proxmox Template Configuration
# This script sets up the configuration file needed for template management

set -e

CONFIG_DIR="$HOME/proxmox-config"
CONFIG_FILE="$CONFIG_DIR/templates.yaml"
EXAMPLE_FILE="$(dirname "$0")/../config/templates.yaml.example"

echo "Setting up Proxmox template configuration..."

# Create config directory
mkdir -p "$CONFIG_DIR"

# Copy example if config doesn't exist
if [ ! -f "$CONFIG_FILE" ]; then
    echo "Copying example configuration to $CONFIG_FILE"
    cp "$EXAMPLE_FILE" "$CONFIG_FILE"
    echo "Configuration created at: $CONFIG_FILE"
    echo ""
    echo "Please review and customize the configuration, especially:"
    echo "- Template IDs (if you need different ones)"
    echo "- Proxmox host IP address"
    echo "- SSH key paths"
    echo "- Storage configuration"
else
    echo "Configuration already exists at: $CONFIG_FILE"
fi

echo ""
echo "Configuration setup complete!"
echo "You can now run: python3 scripts/template-manager.py --create-templates"