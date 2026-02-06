#!/bin/sh
# disk-info.sh - Disk diagnostics report
set -e

echo "=========================================="
echo "  Disk Diagnostics Report"
echo "  Host: $(hostname)"
echo "  Date: $(date -u '+%Y-%m-%d %H:%M:%S UTC')"
echo "=========================================="

echo ""
echo "=== Block Devices ==="
lsblk -o NAME,SIZE,TYPE,FSTYPE,MOUNTPOINT,MODEL,SERIAL 2>/dev/null || lsblk

echo ""
echo "=== NVMe Devices ==="
if command -v nvme >/dev/null 2>&1; then
    nvme list 2>/dev/null || echo "No NVMe devices or nvme-cli not available"
else
    echo "nvme-cli not installed"
fi

echo ""
echo "=== Partition Tables ==="
for dev in /dev/nvme*n1 /dev/sd?; do
    if [ -b "$dev" ]; then
        echo "--- $dev ---"
        sgdisk -p "$dev" 2>/dev/null || echo "  Could not read partition table"
        echo ""
    fi
done

echo ""
echo "=== SMART Health ==="
for dev in /dev/nvme*n1 /dev/sd?; do
    if [ -b "$dev" ]; then
        echo "--- $dev ---"
        case "$dev" in
            /dev/nvme*)
                nvme smart-log "$dev" 2>/dev/null || echo "  SMART not available"
                ;;
            /dev/sd*)
                smartctl -H "$dev" 2>/dev/null || echo "  SMART not available"
                ;;
        esac
        echo ""
    fi
done

echo ""
echo "=== PCI Storage Controllers ==="
lspci 2>/dev/null | grep -iE 'storage|nvme|sata|scsi|raid|sas' || echo "No PCI storage controllers found"

echo ""
echo "=== Filesystem Usage ==="
df -h 2>/dev/null || echo "No mounted filesystems"

echo ""
echo "=========================================="
echo "  End of Disk Diagnostics Report"
echo "=========================================="
