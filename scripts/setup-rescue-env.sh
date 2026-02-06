#!/bin/bash
# setup-rescue-env.sh - Setup Ubuntu PXE rescue environment with Secure Boot (SHIM+GRUB)
# Builds Ubuntu rescue squashfs, deploys SHIM+GRUB signed binaries, configures GRUB
# Usage: setup-rescue-env.sh [-v]
set -euo pipefail

# Parse flags
VERBOSE_FLAG=""
while getopts "v" opt; do
    case "$opt" in
        v) VERBOSE_FLAG="-v" ;;
        *) echo "Usage: $0 [-v]"; exit 1 ;;
    esac
done
shift $((OPTIND - 1))

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
RESCUE_DIR="/var/www/html/provisioning/ubuntu-rescue"
RESCUE_SCRIPTS_DIR="/var/www/html/rescue-scripts"
NFS_RESCUE_DIR="/srv/nfs/ubuntu-rescue"
TFTP_DIR="/var/lib/tftpboot"
REAL_HOME="${SUDO_USER:+$(eval echo ~$SUDO_USER)}"
REAL_HOME="${REAL_HOME:-$HOME}"
SSH_PUBKEY="${REAL_HOME}/.ssh/sysadmin_automation_key.pub"
SERVER_IP="10.10.1.1"

echo "=== Ubuntu PXE Rescue Environment Setup (Secure Boot) ==="
echo ""

# Validate SSH key exists
if [ ! -f "$SSH_PUBKEY" ]; then
    echo "ERROR: SSH public key not found: ${SSH_PUBKEY}"
    echo "Generate one with: ssh-keygen -t ed25519 -f ~/.ssh/sysadmin_automation_key"
    exit 1
fi
echo "SSH public key: ${SSH_PUBKEY}"

# Step 1: Install build dependencies
echo ""
echo "=== Step 1: Installing build dependencies ==="
sudo apt-get update -qq 2>/dev/null
sudo apt-get install -y --no-install-recommends -qq \
    debootstrap squashfs-tools \
    shim-signed grub-efi-amd64-signed \
    nfs-kernel-server 2>/dev/null

# Step 2: Build Ubuntu rescue squashfs
echo ""
echo "=== Step 2: Building Ubuntu rescue squashfs ==="
sudo mkdir -p "$RESCUE_DIR"
sudo "$SCRIPT_DIR/build-rescue-squashfs.sh" $VERBOSE_FLAG "$RESCUE_DIR" "$SSH_PUBKEY"

# Step 3: Deploy SHIM and GRUB signed binaries to TFTP
echo ""
echo "=== Step 3: Deploying Secure Boot binaries to TFTP ==="
sudo mkdir -p "$TFTP_DIR/grub"

# Copy Microsoft-signed SHIM
if [ -f /usr/lib/shim/shimx64.efi.signed.latest ]; then
    sudo cp /usr/lib/shim/shimx64.efi.signed.latest "$TFTP_DIR/shimx64.efi"
    echo "  shimx64.efi: $(du -h "$TFTP_DIR/shimx64.efi" | cut -f1)"
elif [ -f /usr/lib/shim/shimx64.efi.signed ]; then
    sudo cp /usr/lib/shim/shimx64.efi.signed "$TFTP_DIR/shimx64.efi"
    echo "  shimx64.efi: $(du -h "$TFTP_DIR/shimx64.efi" | cut -f1)"
else
    echo "ERROR: Signed SHIM not found. Install shim-signed package."
    exit 1
fi

# Copy Canonical-signed GRUB
if [ -f /usr/lib/grub/x86_64-efi-signed/grubnetx64.efi.signed ]; then
    sudo cp /usr/lib/grub/x86_64-efi-signed/grubnetx64.efi.signed "$TFTP_DIR/grubx64.efi"
    echo "  grubx64.efi: $(du -h "$TFTP_DIR/grubx64.efi" | cut -f1)"
else
    echo "ERROR: Signed GRUB not found. Install grub-efi-amd64-signed package."
    exit 1
fi

# Step 4: Create base GRUB config
echo ""
echo "=== Step 4: Creating GRUB PXE config ==="

# Static rescue config -- casper uses NFS for the squashfs
# Serial console added so GRUB output is visible via SOL
sudo tee "$TFTP_DIR/grub/grub.cfg" > /dev/null << EOF
# PXE Provisioning - Secure Boot compatible
set timeout=5
set default=0

serial --unit=0 --speed=115200
terminal_output console serial
terminal_input console serial

configfile (http,${SERVER_IP})/index.php?mac=\$net_default_mac&format=grub
EOF
# Also put in TFTP root (GRUB may look there depending on prefix)
sudo cp "$TFTP_DIR/grub/grub.cfg" "$TFTP_DIR/grub.cfg"
echo "  grub.cfg created"

# Step 5: Setup NFS export for casper
echo ""
echo "=== Step 5: Setting up NFS export ==="
sudo mkdir -p "${NFS_RESCUE_DIR}/casper"

# Use hard link (not symlink) so NFS clients see the actual file
sudo ln -f "${RESCUE_DIR}/filesystem.squashfs" "${NFS_RESCUE_DIR}/casper/filesystem.squashfs"

# Add NFS export if not already present
if ! grep -q "${NFS_RESCUE_DIR}" /etc/exports 2>/dev/null; then
    echo "${NFS_RESCUE_DIR} 10.10.1.0/24(ro,no_subtree_check,no_root_squash)" | sudo tee -a /etc/exports
fi
sudo exportfs -ra
echo "  NFS export: ${NFS_RESCUE_DIR}"

# Step 6: Deploy SUM binary
echo ""
echo "=== Step 6: Deploying SUM tool ==="
SUM_SOURCE="$SCRIPT_DIR/../../supermicro-sum-scripts/sum_2.14.0_Linux_x86_64/sum"
TOOLS_DIR="${RESCUE_DIR}/tools"
sudo mkdir -p "$TOOLS_DIR"
if [ -f "$SUM_SOURCE" ]; then
    sudo cp "$SUM_SOURCE" "$TOOLS_DIR/sum"
    sudo chmod 644 "$TOOLS_DIR/sum"
    echo "  SUM binary deployed: $(du -h "$TOOLS_DIR/sum" | cut -f1)"
else
    echo "  WARNING: SUM binary not found at ${SUM_SOURCE}"
    echo "  In-band BIOS operations will not be available"
fi

# Step 7: Deploy rescue action scripts
echo ""
echo "=== Step 7: Deploying rescue action scripts ==="
sudo mkdir -p "$RESCUE_SCRIPTS_DIR"
for script in install-incusos.sh wipe-disks.sh disk-info.sh; do
    src="${SCRIPT_DIR}/rescue-scripts/${script}"
    if [ -f "$src" ]; then
        sudo cp "$src" "$RESCUE_SCRIPTS_DIR/${script}"
        sudo chmod 644 "$RESCUE_SCRIPTS_DIR/${script}"
        echo "  Deployed: ${script}"
    else
        echo "  WARNING: ${src} not found, skipping"
    fi
done

# Step 8: Set permissions
echo ""
echo "=== Step 8: Setting permissions ==="
# HTTP files owned by www-data
sudo chown -R www-data:www-data "$RESCUE_DIR" 2>/dev/null || \
    echo "WARNING: Could not set www-data ownership on rescue dir"
sudo chown -R www-data:www-data "$RESCUE_SCRIPTS_DIR" 2>/dev/null || \
    echo "WARNING: Could not set www-data ownership on rescue-scripts dir"

# TFTP files owned by dnsmasq
sudo chown -R dnsmasq:nogroup "$TFTP_DIR" 2>/dev/null || \
    echo "WARNING: Could not set dnsmasq ownership on tftpboot dir"
sudo chmod 644 "$TFTP_DIR/shimx64.efi" "$TFTP_DIR/grubx64.efi" "$TFTP_DIR/grub/grub.cfg"

# Step 9: Restart services
echo ""
echo "=== Step 9: Restarting services ==="
sudo systemctl restart dnsmasq
echo "  dnsmasq restarted"
sudo systemctl restart nfs-kernel-server
echo "  NFS server restarted"

# Verification
echo ""
echo "=== Verification ==="
echo "TFTP directory:"
ls -lh "$TFTP_DIR/shimx64.efi" "$TFTP_DIR/grubx64.efi" "$TFTP_DIR/grub/grub.cfg"
echo ""
echo "Rescue directory:"
ls -lh "$RESCUE_DIR"/
echo ""
echo "NFS export:"
exportfs -v | grep ubuntu-rescue
echo ""
echo "Rescue scripts:"
ls -lh "$RESCUE_SCRIPTS_DIR"/
echo ""

# Show expected boot flow
echo "=== Secure Boot PXE Flow ==="
echo "  1. Node PXE boots -> DHCP -> shimx64.efi (TFTP)"
echo "  2. shimx64.efi -> grubx64.efi (TFTP)"
echo "  3. grubx64.efi -> grub/grub.cfg (TFTP)"
echo "  4. grub.cfg -> fetches dynamic config from http://${SERVER_IP}/index.php?mac=...&format=grub"
echo "  5. GRUB boots Ubuntu kernel + casper initrd (HTTP)"
echo "  6. casper mounts filesystem.squashfs over NFS"
echo "  7. Ubuntu boots, SSH ready, SUM works natively"
echo ""
echo "=== Setup complete ==="
