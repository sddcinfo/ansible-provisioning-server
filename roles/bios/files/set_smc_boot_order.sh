#!/bin/bash
set -e

# Script to set the boot order on Supermicro servers using the SUM utility.

# --- Configuration ---
SUM_URL="https://www.supermicro.com/Bios/sw_download/698/sum_2.14.0_Linux_x86_64_20240215.tar.gz"
SUM_DIR="/home/sysadmin/sum_2.14.0_Linux_x86_64"
SUM_EXEC="${SUM_DIR}/sum"
BIOS_CONFIG_FILE="/home/sysadmin/modified_bios.txt"

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
    wget -O "${SUM_DIR}.tar.gz" "$SUM_URL"
    tar -xzf "${SUM_DIR}.tar.gz" -C /home/sysadmin/
    chmod +x "$SUM_EXEC"
fi

# --- Create BIOS Config File ---
cat > "$BIOS_CONFIG_FILE" << EOF
[Boot]
Boot Mode Select=01
UEFI Boot Order #1=0006
UEFI Boot Order #2=0000
UEFI Boot Order #3=0008
EOF

# --- Apply BIOS settings and Reboot ---
echo "Applying BIOS boot order to ${IPMI_ADDRESS} and rebooting..."
"$SUM_EXEC" -i "$IPMI_ADDRESS" -u "$IPMI_USER" -p "$IPMI_PASS" -c ChangeBiosCfg --file "$BIOS_CONFIG_FILE" --reboot

echo "BIOS configuration applied successfully."
exit 0
