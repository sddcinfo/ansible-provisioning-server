#!/bin/sh
# install-incusos.sh - Incus OS installation from rescue environment
#
# This runs on a rescue node via SSH. It:
# 1. Auto-detects staging disk (first NVMe) and target disk (smallest non-NVMe)
# 2. Downloads and writes the installer image to the staging disk
# 3. Patches loader.conf (secure-boot-enroll off -- keys enrolled manually in BIOS)
# 4. Writes seed tar to staging partition 2 (detects Secure Boot state dynamically)
# 5. Zeros target disk first 10MB for clean install
# 6. Sets EFI BootOrder via efibootmgr so NVMe staging boots next
#
# Does NOT reboot -- the caller handles power cycle.
#
# Usage: install-incusos.sh <server_ip>
# Environment: INCUSOS_IMAGE_URL (optional, overrides default)

set -e

SERVER_IP="$1"
if [ -z "$SERVER_IP" ]; then
    echo "Usage: $0 <server_ip>"
    exit 1
fi

IMAGE_URL="${INCUSOS_IMAGE_URL:-http://${SERVER_IP}/provisioning/incusos/IncusOS_latest.img.gz}"
CLIENT_CERT_URL="http://${SERVER_IP}/provisioning/incusos/client.crt"

# --- Disk Detection ---

detect_staging_disk() {
    # Staging disk: first NVMe device
    local disk
    disk=$(ls /dev/nvme*n1 2>/dev/null | head -1)
    if [ -n "$disk" ]; then
        echo "$disk"
        return
    fi
    echo "ERROR: No NVMe device found for staging" >&2
    exit 1
}

detect_target_disk() {
    # Target disk: smallest non-NVMe disk
    local disk
    disk=$(lsblk -dnbo NAME,SIZE,TYPE 2>/dev/null | awk '$3 == "disk" && $1 !~ /^nvme/' | sort -k2 -n | head -1 | awk '{print $1}')
    if [ -n "$disk" ]; then
        echo "/dev/$disk"
        return
    fi
    echo "ERROR: No non-NVMe disk found for target" >&2
    exit 1
}

get_target_disk_id() {
    # Get stable disk ID for Incus OS installer seed
    # IMPORTANT: IncusOS installer uses minimal udev that only creates wwn-* links,
    # NOT ata-* or scsi-* links.  We MUST prefer wwn-* to match the installer env.
    # Preference order: wwn-*, scsi-3*, scsi-*, ata-*
    local target="$1"
    local disk_name
    disk_name=$(basename "$target")
    local disk_id

    # Try wwn- first (REQUIRED: IncusOS installer only has wwn-* in /dev/disk/by-id/)
    disk_id=$(ls -la /dev/disk/by-id/ 2>/dev/null | grep "wwn-" | grep "/${disk_name}$" | awk '{print $9}' | head -1)
    if [ -n "$disk_id" ]; then
        echo "$disk_id"
        return
    fi

    # Try scsi-3 (SCSI/SAS disks)
    disk_id=$(ls -la /dev/disk/by-id/ 2>/dev/null | grep "scsi-3" | grep "/${disk_name}$" | awk '{print $9}' | head -1)
    if [ -n "$disk_id" ]; then
        echo "$disk_id"
        return
    fi

    # Try scsi- link
    disk_id=$(ls -la /dev/disk/by-id/ 2>/dev/null | grep "scsi-" | grep "/${disk_name}$" | awk '{print $9}' | head -1)
    if [ -n "$disk_id" ]; then
        echo "$disk_id"
        return
    fi

    # Try ata- link (fallback, NOT available in IncusOS installer)
    disk_id=$(ls -la /dev/disk/by-id/ 2>/dev/null | grep "ata-" | grep "/${disk_name}$" | awk '{print $9}' | head -1)
    if [ -n "$disk_id" ]; then
        echo "$disk_id"
        return
    fi

    echo "ERROR: Cannot find disk ID for ${target}" >&2
    echo "Available by-id links:" >&2
    ls -la /dev/disk/by-id/ 2>&1 | grep "${disk_name}" >&2 || true
    exit 1
}

STAGING_DISK=$(detect_staging_disk)
TARGET_DISK=$(detect_target_disk)
TARGET_DISK_ID=$(get_target_disk_id "$TARGET_DISK")

echo "=== Incus OS Installation ==="
echo "Server:       ${SERVER_IP}"
echo "Image URL:    ${IMAGE_URL}"
echo "Staging disk: ${STAGING_DISK}"
echo "Target disk:  ${TARGET_DISK}"
echo "Target ID:    ${TARGET_DISK_ID}"
echo ""

# --- Step 1: Download and write installer image to staging disk ---
echo ">>> Step 1: Writing installer image to ${STAGING_DISK}..."
echo "    Downloading and decompressing (this takes several minutes)..."
curl -sfL "$IMAGE_URL" | gunzip | dd of="$STAGING_DISK" bs=4M conv=fsync status=progress 2>&1
sync
# Re-read partition table after writing image
echo "    Re-reading partition table..."
partprobe "$STAGING_DISK" 2>/dev/null || blockdev --rereadpt "$STAGING_DISK" 2>/dev/null || true
sleep 2
echo "    Done."
echo ""

# --- Step 2: Patch loader.conf on staging ESP ---
echo ">>> Step 2: Patching loader.conf (secure-boot-enroll off)..."
STAGING_ESP="${STAGING_DISK}p1"
mkdir -p /mnt/staging-esp
mount "$STAGING_ESP" /mnt/staging-esp

if [ -f /mnt/staging-esp/loader/loader.conf ]; then
    echo "    Before: $(cat /mnt/staging-esp/loader/loader.conf)"
    sed -i 's/secure-boot-enroll.*/secure-boot-enroll off/' /mnt/staging-esp/loader/loader.conf
    echo "    After:  $(cat /mnt/staging-esp/loader/loader.conf)"
else
    echo "    WARNING: loader.conf not found, creating..."
    mkdir -p /mnt/staging-esp/loader
    echo "secure-boot-enroll off" > /mnt/staging-esp/loader/loader.conf
fi
sync
umount /mnt/staging-esp
echo "    Done."
echo ""

# --- Step 3: Write seed tar to staging partition 2 ---
echo ">>> Step 3: Writing seed configuration..."

# Detect Secure Boot state from EFI variable (last byte: 01=enabled, 00=disabled)
SB_BYTE=$(od -An -t x1 -j4 -N1 /sys/firmware/efi/efivars/SecureBoot-* 2>/dev/null | tr -d ' ')
if [ "$SB_BYTE" = "01" ]; then
    MISSING_SB="false"
    echo "    Secure Boot: ENABLED (missing_secure_boot=false)"
else
    MISSING_SB="true"
    echo "    Secure Boot: DISABLED (missing_secure_boot=true)"
fi

SEED_DIR=$(mktemp -d)

# install.yaml
cat > "${SEED_DIR}/install.yaml" << EOF
security:
  missing_secure_boot: ${MISSING_SB}
target:
  id: ${TARGET_DISK_ID}
force_reboot: true
EOF
echo "    install.yaml: target.id=${TARGET_DISK_ID}"

# applications.yaml
cat > "${SEED_DIR}/applications.yaml" << EOF
applications:
  - name: incus
EOF
echo "    applications.yaml: incus"

# incus.yaml - download client cert from provisioning server
echo "    Downloading client certificate..."
CLIENT_CERT=$(curl -sfL "$CLIENT_CERT_URL") || {
    echo "ERROR: Failed to download client certificate from ${CLIENT_CERT_URL}" >&2
    exit 1
}

cat > "${SEED_DIR}/incus.yaml" << EOF
apply_defaults: true
preseed:
  certificates:
    - name: mgmt-admin
      type: client
      certificate: |
$(echo "$CLIENT_CERT" | sed 's/^/        /')
EOF
echo "    incus.yaml: apply_defaults=true, client cert embedded"

# Create tar (plain, not gzip) and dd to partition 2
STAGING_SEED="${STAGING_DISK}p2"
cd "${SEED_DIR}"
tar cf seed.tar install.yaml applications.yaml incus.yaml
dd if=seed.tar of="$STAGING_SEED" bs=1M conv=fsync 2>&1
sync
cd /
rm -rf "${SEED_DIR}"
echo "    Seed tar written to ${STAGING_SEED}"
echo "    Done."
echo ""

# --- Step 4: Wipe target disk (both GPT headers + partition signatures) ---
echo ">>> Step 4: Wiping target disk ${TARGET_DISK}..."
# Zero first 10MB (primary GPT header + partition entries)
dd if=/dev/zero of="$TARGET_DISK" bs=1M count=10 conv=fsync 2>&1
# Zero last 10MB (backup GPT header at end of disk -- IncusOS installer detects this!)
TARGET_SIZE=$(blockdev --getsize64 "$TARGET_DISK")
SEEK_BYTES=$((TARGET_SIZE - 10485760))
dd if=/dev/zero of="$TARGET_DISK" bs=1 count=10485760 seek="$SEEK_BYTES" conv=fsync 2>&1
# Also use wipefs to remove any filesystem/partition signatures
wipefs -a "$TARGET_DISK" 2>/dev/null || true
sync
echo "    Target disk fully wiped (GPT primary + backup + signatures)."
echo ""

# --- Step 5: Set EFI BootOrder so staging NVMe boots next ---
echo ">>> Step 5: Setting EFI boot order (NVMe staging first)..."
if ! command -v efibootmgr >/dev/null 2>&1; then
    echo "    Installing efibootmgr..."
    apt-get install -y efibootmgr 2>/dev/null | tail -1
fi

if command -v efibootmgr >/dev/null 2>&1; then
    echo "    Current EFI boot entries:"
    efibootmgr -v 2>&1 | grep -E '^Boot[0-9A-Fa-f]{4}' | head -10
    echo ""

    # Find a non-PXE, non-Shell boot entry (disk/NVMe/UEFI OS)
    DISK_NUM=$(efibootmgr -v 2>/dev/null \
        | grep -E '^Boot[0-9A-Fa-f]{4}' \
        | grep -ivE 'PXE|Network|IPv4|IPv6|EFI Shell|Built-in' \
        | head -1 \
        | grep -o 'Boot[0-9A-Fa-f]*' | sed 's/Boot//')

    if [ -n "$DISK_NUM" ]; then
        # Set as BootNext for immediate next reboot
        efibootmgr -n "$DISK_NUM" 2>&1
        # Reorder BootOrder: disk first, then everything else
        CUR_ORDER=$(efibootmgr 2>/dev/null | grep '^BootOrder:' | sed 's/BootOrder: //')
        NEW_ORDER=$(printf '%s\n%s' "$DISK_NUM" "$(echo "$CUR_ORDER" | tr ',' '\n' | grep -v "$DISK_NUM")" | tr '\n' ',' | sed 's/,$//')
        efibootmgr -o "$NEW_ORDER" 2>&1
        echo "    BootNext=$DISK_NUM, BootOrder=$NEW_ORDER"
    else
        echo "    WARNING: No disk boot entry found in EFI. Listing all entries:"
        efibootmgr -v 2>&1
    fi
else
    echo "    ERROR: Cannot install efibootmgr. IPMI override needed as fallback."
fi
echo "    Done."
echo ""

echo "=== Installation staging complete ==="
echo ""
echo "Next steps:"
echo "  1. Power cycle (EFI BootOrder already set to boot NVMe first)"
echo "  2. Installer boots from ${STAGING_DISK}, installs to ${TARGET_DISK}"
echo "  3. Installer reboots, IncusOS first boot creates LUKS+ZFS"
echo "  4. Incus API starts on port 8443 (takes 3-5 minutes)"
echo ""
echo "IMPORTANT WARNINGS:"
echo "  - Version 202602031842 was PULLED -- verify image is 202602040632 or later"
echo "  - Do NOT clear TPM after first boot (destroys LUKS keys)"
echo "  - Do NOT modify UKI with objcopy (corrupts PE binary)"
