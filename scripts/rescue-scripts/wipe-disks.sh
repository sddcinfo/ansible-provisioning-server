#!/bin/sh
# wipe-disks.sh - Thorough disk wipe for Proxmox VE + Ceph environments
# Handles: LVM, Ceph OSD, ZFS, dm-crypt/LUKS, mdadm, GPT/MBR, NVMe secure erase
#
# Correct order of operations (top of stack to bottom):
#   1. Stop services holding disks (Ceph daemons, ZFS)
#   2. Close dm-crypt/LUKS encrypted volumes
#   3. Remove LVM layers (LV -> VG -> PV)
#   4. Remove all device-mapper mappings
#   5. Destroy ZFS pools and clear labels
#   6. Zero mdadm RAID superblocks
#   7. Wipe filesystem/raid/partition signatures (wipefs)
#   8. Zap GPT/MBR partition tables + zero head/tail of disk
#   9. blkdiscard (TRIM entire device - SSD friendly)
#  10. NVMe secure erase (firmware-level, optional)
#  11. Verify clean state
#
# WARNING: This is destructive and irreversible!
set -e

echo "=== Comprehensive Disk Wipe Tool ==="
echo "  For systems with Proxmox VE + Ceph + ZFS residue"
echo ""

# ---------------------------------------------------------------------------
# Discover all block devices (NVMe namespaces and SATA/SAS drives)
# ---------------------------------------------------------------------------
DEVICES=""
for dev in /dev/nvme*n1; do
    [ -b "$dev" ] && DEVICES="$DEVICES $dev"
done
for dev in /dev/sd?; do
    [ -b "$dev" ] && DEVICES="$DEVICES $dev"
done

if [ -z "$DEVICES" ]; then
    echo "No disk devices found."
    exit 0
fi

echo "Devices to wipe:"
for dev in $DEVICES; do
    SIZE=$(lsblk -bno SIZE "$dev" 2>/dev/null | head -1)
    SIZE_GB=$(echo "scale=1; ${SIZE:-0} / 1073741824" | bc 2>/dev/null || echo "unknown")
    MODEL=$(lsblk -no MODEL "$dev" 2>/dev/null | head -1 | xargs)
    echo "  $dev (${SIZE_GB} GB) ${MODEL}"
done
echo ""

# ---------------------------------------------------------------------------
# PHASE 1: Stop all services that may hold disks
# ---------------------------------------------------------------------------
echo "=== Phase 1: Stopping services ==="

# Stop Ceph targets
for svc in ceph-osd.target ceph-mon.target ceph-mgr.target ceph-mds.target ceph.target; do
    systemctl stop "$svc" 2>/dev/null && echo "  Stopped $svc" || true
done

# Kill lingering Ceph processes
for proc in ceph-mon ceph-mgr ceph-mds ceph-osd ceph-fuse radosgw; do
    killall -9 "$proc" 2>/dev/null && echo "  Killed $proc" || true
done

# Unmount Ceph filesystems
mount 2>/dev/null | grep ceph | awk '{print $3}' | while read mnt; do
    umount -f "$mnt" 2>/dev/null && echo "  Unmounted $mnt" || true
done

# Unmount any tmpfs OSD mounts
mount 2>/dev/null | grep '/var/lib/ceph/osd' | awk '{print $3}' | while read mnt; do
    umount -f "$mnt" 2>/dev/null && echo "  Unmounted OSD $mnt" || true
done

# Stop ZFS services
for svc in zfs-mount.service zfs-share.service zfs-zed.service; do
    systemctl stop "$svc" 2>/dev/null && echo "  Stopped $svc" || true
done

echo "  Phase 1 complete"
echo ""

# ---------------------------------------------------------------------------
# PHASE 2: Close dm-crypt/LUKS encrypted volumes
# ---------------------------------------------------------------------------
echo "=== Phase 2: Closing LUKS/dm-crypt volumes ==="

# Close any ceph-related LUKS volumes via cryptsetup
for mapper in /dev/mapper/*; do
    name=$(basename "$mapper")
    case "$name" in
        *ceph*|*osd*|*luks*)
            cryptsetup close "$name" 2>/dev/null && echo "  Closed LUKS: $name" || true
            ;;
    esac
done

# Fallback: remove via dmsetup for any that cryptsetup missed
if command -v dmsetup >/dev/null 2>&1; then
    dmsetup ls 2>/dev/null | grep -iE 'ceph|osd|luks' | awk '{print $1}' | while read dm; do
        dmsetup remove "$dm" 2>/dev/null && echo "  dmsetup removed: $dm" || true
    done
fi

echo "  Phase 2 complete"
echo ""

# ---------------------------------------------------------------------------
# PHASE 3: Remove LVM layers (LV -> VG -> PV order)
# ---------------------------------------------------------------------------
echo "=== Phase 3: Removing LVM layers ==="

if command -v vgs >/dev/null 2>&1; then
    # Deactivate all Ceph-related volume groups
    for vg in $(vgs --noheadings -o vg_name 2>/dev/null | tr -d ' '); do
        vgchange -an "$vg" 2>/dev/null && echo "  Deactivated VG: $vg" || true
    done

    # Remove all logical volumes in each VG
    for vg in $(vgs --noheadings -o vg_name 2>/dev/null | tr -d ' '); do
        lvremove -f "$vg" 2>/dev/null && echo "  Removed LVs in VG: $vg" || true
    done

    # Remove all volume groups
    for vg in $(vgs --noheadings -o vg_name 2>/dev/null | tr -d ' '); do
        vgremove -f "$vg" 2>/dev/null && echo "  Removed VG: $vg" || true
    done

    # Remove all physical volumes
    for pv in $(pvs --noheadings -o pv_name 2>/dev/null | tr -d ' '); do
        pvremove -ff "$pv" 2>/dev/null && echo "  Removed PV: $pv" || true
    done
else
    echo "  LVM tools not available, skipping"
fi

echo "  Phase 3 complete"
echo ""

# ---------------------------------------------------------------------------
# PHASE 4: Remove ALL remaining device-mapper mappings
# ---------------------------------------------------------------------------
echo "=== Phase 4: Removing device-mapper mappings ==="

if command -v dmsetup >/dev/null 2>&1; then
    # First pass: remove ceph-specific entries
    dmsetup ls 2>/dev/null | awk '{print $1}' | grep -iE 'ceph|osd' | while read dm; do
        dmsetup remove "$dm" 2>/dev/null && echo "  Removed DM: $dm" || \
            dmsetup remove --force "$dm" 2>/dev/null && echo "  Force-removed DM: $dm" || true
    done

    # Second pass: remove everything remaining (nuclear option for full wipe)
    REMAINING=$(dmsetup ls 2>/dev/null | grep -v "No devices" | wc -l)
    if [ "$REMAINING" -gt 0 ]; then
        echo "  Removing $REMAINING remaining device-mapper entries..."
        dmsetup remove_all 2>/dev/null && echo "  All DM entries removed" || \
            echo "  Some DM entries could not be removed (may need reboot)"
    fi
else
    echo "  dmsetup not available, skipping"
fi

echo "  Phase 4 complete"
echo ""

# ---------------------------------------------------------------------------
# PHASE 5: Destroy ZFS pools and clear labels
# ---------------------------------------------------------------------------
echo "=== Phase 5: Destroying ZFS pools and labels ==="

if command -v zpool >/dev/null 2>&1; then
    # Destroy all ZFS pools
    for pool in $(zpool list -H -o name 2>/dev/null); do
        zpool destroy -f "$pool" 2>/dev/null && echo "  Destroyed pool: $pool" || true
    done

    # Clear ZFS labels from each device and its partitions
    for dev in $DEVICES; do
        zpool labelclear -f "$dev" 2>/dev/null && echo "  Cleared ZFS label: $dev" || true
        for part in ${dev}p* ${dev}[0-9]*; do
            [ -b "$part" ] && zpool labelclear -f "$part" 2>/dev/null && \
                echo "  Cleared ZFS label: $part" || true
        done
    done
else
    echo "  ZFS tools not available, skipping"
fi

echo "  Phase 5 complete"
echo ""

# ---------------------------------------------------------------------------
# PHASE 6: Zero mdadm RAID superblocks
# ---------------------------------------------------------------------------
echo "=== Phase 6: Zeroing mdadm RAID superblocks ==="

if command -v mdadm >/dev/null 2>&1; then
    # Stop any active md arrays
    for md in /dev/md*; do
        [ -b "$md" ] && mdadm --stop "$md" 2>/dev/null && echo "  Stopped: $md" || true
    done

    # Zero superblocks on each device and its partitions
    for dev in $DEVICES; do
        mdadm --zero-superblock "$dev" 2>/dev/null && \
            echo "  Zeroed mdadm superblock: $dev" || true
        for part in ${dev}p* ${dev}[0-9]*; do
            [ -b "$part" ] && mdadm --zero-superblock "$part" 2>/dev/null && \
                echo "  Zeroed mdadm superblock: $part" || true
        done
    done
else
    echo "  mdadm not available, skipping"
fi

echo "  Phase 6 complete"
echo ""

# ---------------------------------------------------------------------------
# PHASE 7: Wipe all filesystem/raid/partition signatures
# ---------------------------------------------------------------------------
echo "=== Phase 7: Wiping filesystem signatures (wipefs) ==="

for dev in $DEVICES; do
    # Wipe partitions first, then the whole disk
    for part in ${dev}p* ${dev}[0-9]*; do
        if [ -b "$part" ]; then
            wipefs -af "$part" 2>/dev/null && echo "  Wiped signatures: $part" || true
        fi
    done
    wipefs -af "$dev" 2>/dev/null && echo "  Wiped signatures: $dev" || \
        echo "  wipefs failed on $dev (non-fatal)"
done

echo "  Phase 7 complete"
echo ""

# ---------------------------------------------------------------------------
# PHASE 8: Zap GPT/MBR partition tables + zero head and tail of disk
# ---------------------------------------------------------------------------
echo "=== Phase 8: Zapping partition tables and zeroing disk head/tail ==="

for dev in $DEVICES; do
    echo "  --- $dev ---"

    # Zap GPT + MBR
    sgdisk --zap-all "$dev" 2>/dev/null && echo "  Zapped GPT/MBR" || \
        echo "  sgdisk zap failed (non-fatal)"

    # Zero first 100MB (destroys: GPT header, LUKS header, LVM labels, FS superblocks)
    echo "  Zeroing first 100MB..."
    dd if=/dev/zero of="$dev" bs=1M count=100 conv=notrunc 2>/dev/null || \
        echo "  dd zero (head) failed (non-fatal)"

    # Zero last 100MB (destroys: GPT backup header, ZFS tail metadata)
    DISK_SIZE=$(blockdev --getsize64 "$dev" 2>/dev/null || echo 0)
    if [ "$DISK_SIZE" -gt 104857600 ]; then
        SEEK_MB=$((DISK_SIZE / 1048576 - 100))
        echo "  Zeroing last 100MB (seek=${SEEK_MB}M)..."
        dd if=/dev/zero of="$dev" bs=1M count=100 seek="$SEEK_MB" conv=notrunc 2>/dev/null || \
            echo "  dd zero (tail) failed (non-fatal)"
    fi

    sync
    echo ""
done

echo "  Phase 8 complete"
echo ""

# ---------------------------------------------------------------------------
# PHASE 9: blkdiscard (TRIM entire device - fast and SSD-friendly)
# ---------------------------------------------------------------------------
echo "=== Phase 9: blkdiscard (full-device TRIM) ==="

if command -v blkdiscard >/dev/null 2>&1; then
    for dev in $DEVICES; do
        echo "  Discarding $dev..."
        blkdiscard "$dev" 2>/dev/null && echo "  blkdiscard succeeded: $dev" || \
            echo "  blkdiscard not supported on $dev (non-fatal)"
    done
else
    echo "  blkdiscard not available, skipping"
fi

echo "  Phase 9 complete"
echo ""

# ---------------------------------------------------------------------------
# PHASE 10: NVMe secure erase (firmware-level)
# ---------------------------------------------------------------------------
echo "=== Phase 10: NVMe secure erase ==="

if command -v nvme >/dev/null 2>&1; then
    for dev in $DEVICES; do
        case "$dev" in
            /dev/nvme*n1)
                # Extract controller device (/dev/nvme0 from /dev/nvme0n1)
                CTRL=$(echo "$dev" | sed 's/n[0-9]*$//')
                NS=$(echo "$dev" | grep -o 'n[0-9]*$' | tr -d 'n')

                echo "  --- $dev (controller: $CTRL, namespace: $NS) ---"

                # Show capabilities
                echo "  Capabilities:"
                nvme id-ctrl "$CTRL" -H 2>/dev/null | grep -E 'Format |Crypto Erase|Sanitize' | \
                    while read line; do echo "    $line"; done

                # Attempt NVMe format with User Data Erase (ses=1)
                echo "  Attempting NVMe format (User Data Erase)..."
                if nvme format "$CTRL" --ses=1 -n "$NS" 2>/dev/null; then
                    echo "  NVMe User Data Erase complete: $CTRL"
                else
                    echo "  NVMe format ses=1 failed, trying Crypto Erase (ses=2)..."
                    nvme format "$CTRL" --ses=2 -n "$NS" 2>/dev/null && \
                        echo "  NVMe Crypto Erase complete: $CTRL" || \
                        echo "  NVMe format not supported on $CTRL (non-fatal)"
                fi
                ;;
        esac
    done
else
    echo "  nvme-cli not available, skipping"
fi

echo "  Phase 10 complete"
echo ""

# ---------------------------------------------------------------------------
# PHASE 11: Inform kernel and verify
# ---------------------------------------------------------------------------
echo "=== Phase 11: Verification ==="

# Re-read partition tables
partprobe 2>/dev/null || true

for dev in $DEVICES; do
    echo "--- $dev ---"
    echo "  Signatures remaining:"
    SIG=$(wipefs "$dev" 2>/dev/null)
    if [ -z "$SIG" ]; then
        echo "    CLEAN - no signatures found"
    else
        echo "    $SIG"
    fi

    echo "  Filesystem info:"
    lsblk -f "$dev" 2>/dev/null | while read line; do echo "    $line"; done

    echo "  Partition table:"
    sgdisk -p "$dev" 2>&1 | head -5 | while read line; do echo "    $line"; done

    echo ""
done

echo "=== All disks wiped ==="
echo ""
echo "NOTE: If NVMe sanitize is desired (most thorough, but uninterruptible), run manually:"
echo "  nvme sanitize /dev/nvme0 -a 2    # Block Erase"
echo "  nvme sanitize /dev/nvme0 -a 4    # Crypto Erase"
echo "  nvme sanitize-log /dev/nvme0     # Check progress"
echo ""
echo "NOTE: A reboot is recommended to fully clear kernel-cached partition state."
