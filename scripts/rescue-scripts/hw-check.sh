#!/bin/sh
# hw-check.sh - Comprehensive hardware diagnostics report
# Checks memory, CPU, sensors, storage health, IPMI events, and PCI devices
set -e

echo "=========================================="
echo "  Hardware Diagnostics Report"
echo "  Host: $(hostname)"
echo "  Date: $(date -u '+%Y-%m-%d %H:%M:%S UTC')"
echo "=========================================="

# --- CPU ---
echo ""
echo "=== CPU Summary ==="
lscpu 2>/dev/null | grep -E 'Model name|Socket|Core|Thread|CPU\(s\)|MHz|Cache|Architecture' || echo "lscpu not available"

# --- Memory Summary ---
echo ""
echo "=== Memory Summary ==="
free -h 2>/dev/null || echo "free not available"

# --- DIMM Inventory ---
echo ""
echo "=== DIMM Inventory ==="
if command -v dmidecode >/dev/null 2>&1; then
    dmidecode -t memory 2>/dev/null | awk '
    /^Memory Device$/ { slot=""; size=""; type=""; speed=""; mfr=""; part=""; serial=""; locator="" }
    /^\tLocator:/ { locator=$2 }
    /^\tSize:/ { size=$0; sub(/^\t*Size: */, "", size) }
    /^\tType:/ && !/Type Detail/ { type=$2 }
    /^\tSpeed:/ && !/Configured/ { speed=$0; sub(/^\t*Speed: */, "", speed) }
    /^\tManufacturer:/ { mfr=$0; sub(/^\t*Manufacturer: */, "", mfr) }
    /^\tPart Number:/ { part=$0; sub(/^\t*Part Number: */, "", part) }
    /^\tSerial Number:/ { serial=$0; sub(/^\t*Serial Number: */, "", serial) }
    /^$/ && locator != "" {
        if (size == "" || size == "No Module Installed" || size == "0") {
            printf "  %-10s EMPTY\n", locator
        } else {
            printf "  %-10s %s %s %s %s (S/N: %s)\n", locator, size, type, speed, mfr, serial
        }
    }
    ' 2>/dev/null || echo "  dmidecode failed"
else
    echo "  dmidecode not installed"
fi

# --- EDAC (ECC Error Detection) ---
echo ""
echo "=== ECC/EDAC Status ==="
if [ -d /sys/devices/system/edac/mc ]; then
    for mc in /sys/devices/system/edac/mc/mc*; do
        [ -d "$mc" ] || continue
        mc_name=$(basename "$mc")
        ce=$(cat "$mc/ce_count" 2>/dev/null || echo "?")
        ue=$(cat "$mc/ue_count" 2>/dev/null || echo "?")
        echo "  ${mc_name}: Correctable=$ce  Uncorrectable=$ue"
        # Per-DIMM (csrow) stats
        for csrow in "$mc"/csrow*; do
            [ -d "$csrow" ] || continue
            csname=$(basename "$csrow")
            csce=$(cat "$csrow/ce_count" 2>/dev/null || echo "?")
            csue=$(cat "$csrow/ue_count" 2>/dev/null || echo "?")
            label=$(cat "$csrow/ch0_dimm_label" 2>/dev/null || echo "unknown")
            if [ "$csce" != "0" ] || [ "$csue" != "0" ]; then
                echo "    ${csname} (${label}): CE=$csce UE=$csue  *** ERRORS ***"
            fi
        done
    done
    if command -v edac-util >/dev/null 2>&1; then
        echo ""
        echo "  edac-util summary:"
        edac-util -s 2>/dev/null || true
    fi
else
    echo "  EDAC subsystem not available (no /sys/devices/system/edac/mc)"
fi

# --- Kernel MCE/Error Messages ---
echo ""
echo "=== Kernel Hardware Errors ==="
dmesg 2>/dev/null | grep -iE 'hardware error|mce|machine check|ecc|edac|nmi|panic|uncorrectable|correctable' | tail -20 || echo "  No hardware errors in kernel log"

# --- IPMI SEL (System Event Log) ---
echo ""
echo "=== IPMI System Event Log (last 30 events) ==="
if command -v ipmitool >/dev/null 2>&1; then
    ipmitool sel list 2>/dev/null | tail -30 || echo "  IPMI SEL not available"
    echo ""
    echo "  SEL Summary:"
    TOTAL=$(ipmitool sel info 2>/dev/null | grep "Entries" | head -1 || echo "unknown")
    echo "    $TOTAL"
    # Highlight memory/critical events
    MEM_EVENTS=$(ipmitool sel list 2>/dev/null | grep -ci "memory\|ecc\|dimm" || true)
    CRIT_EVENTS=$(ipmitool sel list 2>/dev/null | grep -ci "critical\|non-recoverable" || true)
    echo "    Memory-related events: $MEM_EVENTS"
    echo "    Critical events: $CRIT_EVENTS"
else
    echo "  ipmitool not installed"
fi

# --- IPMI Sensor Readings ---
echo ""
echo "=== IPMI Sensors (temperatures + voltages) ==="
if command -v ipmitool >/dev/null 2>&1; then
    echo "  -- Temperatures --"
    ipmitool sensor 2>/dev/null | grep -i "temp" | awk -F'|' '{
        name=$1; val=$2; status=$4
        gsub(/^[ \t]+|[ \t]+$/, "", name)
        gsub(/^[ \t]+|[ \t]+$/, "", val)
        gsub(/^[ \t]+|[ \t]+$/, "", status)
        if (val == "na") {
            printf "  %-20s %s  *** SENSOR OFFLINE ***\n", name, val
        } else if (status != "ok") {
            printf "  %-20s %s C  [%s]  *** WARNING ***\n", name, val, status
        } else {
            printf "  %-20s %s C  [%s]\n", name, val, status
        }
    }' || echo "  Could not read sensors"

    echo ""
    echo "  -- Voltages --"
    ipmitool sensor 2>/dev/null | grep -iE "volt|vcore|vdimm|3.3|5v|12v" | awk -F'|' '{
        name=$1; val=$2; status=$4
        gsub(/^[ \t]+|[ \t]+$/, "", name)
        gsub(/^[ \t]+|[ \t]+$/, "", val)
        gsub(/^[ \t]+|[ \t]+$/, "", status)
        if (status != "ok" && status != "na") {
            printf "  %-20s %s V  [%s]  *** WARNING ***\n", name, val, status
        } else {
            printf "  %-20s %s V  [%s]\n", name, val, status
        }
    }' || echo "  Could not read sensors"

    echo ""
    echo "  -- Fans --"
    ipmitool sensor 2>/dev/null | grep -i "fan" | awk -F'|' '{
        name=$1; val=$2; unit=$3; status=$4
        gsub(/^[ \t]+|[ \t]+$/, "", name)
        gsub(/^[ \t]+|[ \t]+$/, "", val)
        gsub(/^[ \t]+|[ \t]+$/, "", unit)
        gsub(/^[ \t]+|[ \t]+$/, "", status)
        printf "  %-20s %s RPM  [%s]\n", name, val, status
    }' || echo "  Could not read sensors"
else
    echo "  ipmitool not installed"
fi

# --- lm-sensors ---
echo ""
echo "=== lm-sensors ==="
if command -v sensors >/dev/null 2>&1; then
    sensors 2>/dev/null || echo "  No sensors detected (run sensors-detect first)"
else
    echo "  lm-sensors not installed"
fi

# --- Storage Health ---
echo ""
echo "=== Storage Health ==="
for dev in /dev/nvme*n1; do
    if [ -b "$dev" ]; then
        echo "  --- $dev ---"
        if command -v nvme >/dev/null 2>&1; then
            nvme smart-log "$dev" 2>/dev/null | grep -E 'critical_warning|temperature|available_spare|percentage_used|data_units|media_errors|num_err_log' || echo "    SMART not available"
        fi
    fi
done
for dev in /dev/sd?; do
    if [ -b "$dev" ]; then
        echo "  --- $dev ---"
        if command -v smartctl >/dev/null 2>&1; then
            smartctl -H "$dev" 2>/dev/null | grep -i "result\|status" || echo "    SMART not available"
            smartctl -A "$dev" 2>/dev/null | grep -iE 'reallocated|pending|uncorrect|wear|temperature' || true
        fi
    fi
done

# --- PCI Devices ---
echo ""
echo "=== PCI Devices (key hardware) ==="
if command -v lspci >/dev/null 2>&1; then
    echo "  -- Network --"
    lspci 2>/dev/null | grep -iE 'ethernet|network' || echo "    None found"
    echo "  -- Storage --"
    lspci 2>/dev/null | grep -iE 'storage|nvme|sata|scsi|raid|sas' || echo "    None found"
    echo "  -- GPU --"
    lspci 2>/dev/null | grep -iE 'vga|3d|display|gpu' || echo "    None found"
else
    echo "  lspci not installed"
fi

# --- Hardware Summary (lshw) ---
echo ""
echo "=== Hardware Summary ==="
if command -v lshw >/dev/null 2>&1; then
    lshw -short 2>/dev/null || echo "  lshw failed"
else
    echo "  lshw not installed"
fi

echo ""
echo "=========================================="
echo "  End of Hardware Diagnostics Report"
echo "=========================================="
