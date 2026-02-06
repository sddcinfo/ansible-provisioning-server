#!/bin/sh
# install-incusos.sh - Incus OS installation from rescue environment
#
# This runs on a rescue node via SSH. It:
# 1. Auto-detects staging disk (first NVMe) and target disk (smallest non-NVMe)
# 2. Downloads and writes the installer image to the staging disk
# 3. Patches loader.conf (secure-boot-enroll off)
# 4. Writes seed tar to staging partition 2
# 5. Zeros target disk first 10MB for clean install
#
# Does NOT reboot -- the caller (smcbmc-cli) handles IPMI boot + power cycle.
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
    # Get scsi-3<wwn> disk ID for Incus OS installer seed
    # CRITICAL: Must be scsi-3 format, NOT wwn-0x or ata-
    local target="$1"
    local disk_name
    disk_name=$(basename "$target")
    local disk_id
    disk_id=$(ls -la /dev/disk/by-id/ 2>/dev/null | grep "scsi-3" | grep "${disk_name}$" | awk '{print $9}' | head -1)
    if [ -n "$disk_id" ]; then
        echo "$disk_id"
        return
    fi
    # Fallback: try any scsi- link
    disk_id=$(ls -la /dev/disk/by-id/ 2>/dev/null | grep "scsi-" | grep "${disk_name}$" | awk '{print $9}' | head -1)
    if [ -n "$disk_id" ]; then
        echo "$disk_id"
        return
    fi
    echo "ERROR: Cannot find scsi disk ID for ${target}" >&2
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

SEED_DIR=$(mktemp -d)

# install.yaml
cat > "${SEED_DIR}/install.yaml" << EOF
security:
  missing_secure_boot: true
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

# --- Step 4: Zero target disk first 10MB ---
echo ">>> Step 4: Zeroing first 10MB of target disk ${TARGET_DISK}..."
dd if=/dev/zero of="$TARGET_DISK" bs=1M count=10 conv=fsync 2>&1
sync
echo "    Done."
echo ""

echo "=== Installation staging complete ==="
echo ""
echo "Next steps (handled by smcbmc-cli):"
echo "  1. Set IPMI boot override to HDD"
echo "  2. Power cycle"
echo "  3. Installer boots from ${STAGING_DISK}, installs to ${TARGET_DISK}"
echo "  4. First boot creates LUKS+ZFS, starts Incus API on port 8443"
echo ""
echo "IMPORTANT WARNINGS:"
echo "  - Version 202602031842 was PULLED -- verify image is 202602040632 or later"
echo "  - Do NOT clear TPM after first boot (destroys LUKS keys)"
echo "  - Do NOT modify UKI with objcopy (corrupts PE binary)"
