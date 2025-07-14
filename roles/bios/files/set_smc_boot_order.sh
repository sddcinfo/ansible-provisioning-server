#!/bin/bash
set -e

# Script to idempotently set the boot order on Supermicro servers using the SUM utility.

# --- Configuration ---
SUM_URL="https://www.supermicro.com/Bios/sw_download/698/sum_2.14.0_Linux_x86_64_20240215.tar.gz"
SUM_DIR="/home/sysadmin/sum_2.14.0_Linux_x86_64"
SUM_EXEC="${SUM_DIR}/sum"
CURRENT_BIOS_CONFIG="/tmp/current_bios_settings.txt"
DESIRED_BIOS_CONFIG="/tmp/desired_bios_settings.txt"

# --- Arguments ---
IPMI_ADDRESS="$1"
IPMI_USER="$2"
IPMI_PASS="$3"

if [[ -z "$IPMI_ADDRESS" || -z "$IPMI_USER" || -z "$IPMI_PASS" ]]; then
    echo "Usage: $0 <ipmi_address> <ipmi_user> <ipmi_password>"
    exit 1
fi

# --- Download and Extract SUM if not present ---
if [ ! -f "$SUM_EXEC" ]; then
    echo "SUM utility not found. Downloading..."
    wget -q -O "${SUM_DIR}.tar.gz" "$SUM_URL"
    tar -xzf "${SUM_DIR}.tar.gz" -C /home/sysadmin/
    chmod +x "$SUM_EXEC"
fi

# --- Get Current BIOS Config ---
echo "Getting current BIOS config from ${IPMI_ADDRESS}..."
"$SUM_EXEC" -i "$IPMI_ADDRESS" -u "$IPMI_USER" -p "$IPMI_PASS" -c GetCurrentBiosCfg --file "$CURRENT_BIOS_CONFIG" > /dev/null

# --- Check if settings are already correct ---
BOOT_MODE=$(grep "Boot Mode Select" "$CURRENT_BIOS_CONFIG" | awk -F'=' '{print $2}' | awk '{print $1}')
UEFI_BOOT_1=$(grep "UEFI Boot Order #1" "$CURRENT_BIOS_CONFIG" | awk -F'=' '{print $2}' | awk '{print $1}')

if [[ "$BOOT_MODE" == "01" && "$UEFI_BOOT_1" == "0006" ]]; then
    echo "Boot order is already correctly set to UEFI PXE. No changes needed."
    exit 0
fi

# --- Create Desired BIOS Config File ---
echo "Boot order is not correct. Applying new configuration..."
cat > "$DESIRED_BIOS_CONFIG" << EOF
[Boot]
Boot Mode Select=01
UEFI Boot Order #1=0006
UEFI Boot Order #2=0000
UEFI Boot Order #3=0008
EOF

# --- Apply BIOS settings and Reboot ---
echo "Applying BIOS boot order to ${IPMI_ADDRESS} and rebooting..."
"$SUM_EXEC" -i "$IPMI_ADDRESS" -u "$IPMI_USER" -p "$IPMI_PASS" -c ChangeBiosCfg --file "$DESIRED_BIOS_CONFIG" --reboot

echo "BIOS configuration applied successfully."
exit 0
