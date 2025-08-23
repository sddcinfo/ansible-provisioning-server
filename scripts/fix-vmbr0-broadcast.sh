#!/bin/bash
# Fix vmbr0 broadcast configuration on Proxmox nodes
# This script adds the missing broadcast directive to vmbr0

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log() {
    echo -e "${BLUE}[$(date '+%H:%M:%S')] $1${NC}"
}

success() {
    echo -e "${GREEN}[OK] $1${NC}"
}

warning() {
    echo -e "${YELLOW}[WARN] $1${NC}"
}

error() {
    echo -e "${RED}[FAIL] $1${NC}"
}

# Check if running as root
if [ "$EUID" -ne 0 ]; then
    error "This script must be run as root"
    exit 1
fi

log "Fixing vmbr0 broadcast configuration..."

# Backup current interfaces file
BACKUP_FILE="/etc/network/interfaces.backup.broadcast-fix.$(date +%Y%m%d-%H%M%S)"
cp /etc/network/interfaces "$BACKUP_FILE"
log "Backed up interfaces file to $BACKUP_FILE"

# Check if broadcast is already configured
if grep -q "broadcast 10.10.1.255" /etc/network/interfaces; then
    success "vmbr0 broadcast already configured correctly"
    
    # Still verify the current configuration
    log "Current vmbr0 configuration:"
    ip addr show vmbr0 | grep inet
    exit 0
fi

log "Adding broadcast configuration to vmbr0..."

# Create temporary file with the fixed configuration
cp /etc/network/interfaces /tmp/interfaces.fix.tmp

# Add broadcast line after the address line in vmbr0 section
sed -i '/^iface vmbr0 inet static/,/^[[:space:]]*bridge-fd/ {
    /address.*\/24/ a\
	broadcast 10.10.1.255
}' /tmp/interfaces.fix.tmp

# Verify the change was made correctly
if grep -q "broadcast 10.10.1.255" /tmp/interfaces.fix.tmp; then
    # Show what will be changed
    log "New vmbr0 configuration will be:"
    grep -A 10 "^iface vmbr0 inet static" /tmp/interfaces.fix.tmp | grep -E "(iface|address|broadcast|gateway|bridge)" || true
    
    # Apply the changes
    cp /tmp/interfaces.fix.tmp /etc/network/interfaces
    success "Added broadcast 10.10.1.255 to vmbr0 configuration"
    
    # Apply the network configuration
    log "Restarting vmbr0 interface to apply changes..."
    
    # Method 1: Try ifreload (Proxmox preferred)
    if command -v ifreload >/dev/null 2>&1; then
        log "Using ifreload to apply configuration..."
        if ifreload -a; then
            success "Network configuration reloaded successfully"
        else
            warning "ifreload failed, trying ifdown/ifup method"
            ifdown vmbr0 && sleep 2 && ifup vmbr0 || warning "Manual interface restart failed"
        fi
    else
        # Method 2: Traditional ifdown/ifup
        log "Using ifdown/ifup to apply configuration..."
        ifdown vmbr0 && sleep 2 && ifup vmbr0 || warning "Manual interface restart failed"
    fi
    
    # Verify the configuration
    sleep 3
    log "Verifying new configuration..."
    NEW_CONFIG=$(ip addr show vmbr0 | grep "inet ")
    if echo "$NEW_CONFIG" | grep -q "brd 10.10.1.255"; then
        success "Broadcast configuration applied successfully"
        success "New vmbr0 configuration: $NEW_CONFIG"
    else
        warning "Configuration may not have applied correctly"
        warning "Current vmbr0 configuration: $NEW_CONFIG"
        log "You may need to reboot the system for changes to take effect"
    fi
    
else
    error "Failed to add broadcast configuration to interfaces file"
    rm -f /tmp/interfaces.fix.tmp
    exit 1
fi

# Cleanup
rm -f /tmp/interfaces.fix.tmp

success "vmbr0 broadcast configuration fix completed"
log "If the interface configuration didn't apply correctly, you may need to:"
log "1. Run 'systemctl restart networking' (may cause temporary disconnection)"
log "2. Reboot the system"