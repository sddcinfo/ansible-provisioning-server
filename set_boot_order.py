#!/usr/bin/env python3
import argparse
import subprocess
import os
import sys
import json
import re
import time

# --- Configuration ---
SUM_URL = "https://www.supermicro.com/Bios/sw_download/698/sum_2.14.0_Linux_x86_64_20240215.tar.gz"
SUM_DIR = "/home/sysadmin/sum_2.14.0_Linux_x86_64"
SUM_EXEC = f"{SUM_DIR}/sum"
NODES_FILE = "/home/sysadmin/ansible-provisioning-server/nodes.json"
CURRENT_BIOS_CONFIG_FILE_TPL = "/tmp/current_config_{node_name}.txt"
NEW_BIOS_CONFIG_FILE_TPL = "/tmp/new_config_{node_name}.txt"

# --- Mappings ---
BOOT_DEVICE_MAP = {
    "pxe": "0006",
    "hdd": "0000",
    "disabled": "0008",
}

# --- Color Codes ---
class bcolors:
    HEADER = '\033[95m'
    OKBLUE = '\033[94m'
    OKCYAN = '\033[96m'
    OKGREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'

def get_node_ip(node_name):
    with open(NODES_FILE) as f:
        nodes = json.load(f)["console_nodes"]
    for node in nodes:
        if node.get("hostname") == node_name:
            return node.get("ip")
    return None

def run_command(command, description, timeout=300):
    print(f"{bcolors.OKCYAN}... {description}{bcolors.ENDC}")
    try:
        process = subprocess.run(
            command, shell=True, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        print(f"{bcolors.OKGREEN}Success.{bcolors.ENDC}")
        return process.stdout
    except subprocess.CalledProcessError as e:
        print(f"{bcolors.FAIL}Error: {description} failed.{bcolors.ENDC}")
        print(f"  Return Code: {e.returncode}")
        print(f"  Stdout: {e.stdout}")
        print(f"  Stderr: {e.stderr}")
        sys.exit(1)

def main():
    """Main function to set the boot order."""
    parser = argparse.ArgumentParser(
        description="A script to set a specific, ordered list of boot devices on Supermicro servers.",
        formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("node_name", help="The hostname of the node to configure (e.g., console-node1).")
    parser.add_argument(
        "boot_devices",
        nargs=2,
        choices=BOOT_DEVICE_MAP.keys(),
        help=f"A space-separated list of two boot devices. Choices: {', '.join(BOOT_DEVICE_MAP.keys())}"
    )
    args = parser.parse_args()

    ipmi_address = get_node_ip(args.node_name)
    if not ipmi_address:
        print(f"{bcolors.FAIL}Error: Node '{args.node_name}' not found in {NODES_FILE}{bcolors.ENDC}")
        sys.exit(1)

    # --- Get IPMI credentials from environment variables ---
    ipmi_user = os.environ.get('IPMI_USER')
    ipmi_pass = os.environ.get('IPMI_PASS')
    if not ipmi_user or not ipmi_pass:
        print(f"{bcolors.FAIL}Error: IPMI_USER and IPMI_PASS environment variables must be set.{bcolors.ENDC}")
        sys.exit(1)

    current_config_file = CURRENT_BIOS_CONFIG_FILE_TPL.format(node_name=args.node_name)
    new_config_file = NEW_BIOS_CONFIG_FILE_TPL.format(node_name=args.node_name)

    # --- Step 1: Get Current BIOS Config ---
    run_command(
        f"'{SUM_EXEC}' -i '{ipmi_address}' -u '{ipmi_user}' -p '{ipmi_pass}' "
        f"-c GetCurrentBiosCfg --file '{current_config_file}' --overwrite",
        f"Getting current BIOS config from {args.node_name}"
    )

    with open(current_config_file, "r") as f:
        config_content = f.read()

    # --- Step 2: Verify Boot Order ---
    print(f"{bcolors.OKCYAN}Verifying boot order...{bcolors.ENDC}")
    boot_order_1_match = re.search(r"UEFI Boot Order #1=(\d+)", config_content)
    current_boot_1 = boot_order_1_match.group(1) if boot_order_1_match else None
    desired_boot_1 = BOOT_DEVICE_MAP[args.boot_devices[0]]

    if current_boot_1 == desired_boot_1:
        print(f"{bcolors.OKGREEN}Boot order is already correct. No changes needed.{bcolors.ENDC}")
    else:
        # --- Step 3: Power Down, Apply, Power Up ---
        print(f"{bcolors.WARNING}Boot order is incorrect. Correcting now...{bcolors.ENDC}")
        
        run_command(
            f"'{SUM_EXEC}' -i '{ipmi_address}' -u '{ipmi_user}' -p '{ipmi_pass}' -c SetPowerAction --action 1",
            f"Powering down {args.node_name}"
        )
        
        print("Waiting for node to power down completely...")
        time.sleep(10)

        for i, device_name in enumerate(args.boot_devices):
            device_code = BOOT_DEVICE_MAP[device_name]
            config_content = re.sub(r"UEFI Boot Order #"+str(i+1)+"=.*", f"UEFI Boot Order #{i+1}={device_code}", config_content)

        with open(new_config_file, "w") as f:
            f.write(config_content)

        run_command(
            f"'{SUM_EXEC}' -i '{ipmi_address}' -u '{ipmi_user}' -p '{ipmi_pass}' "
            f"-c ChangeBiosCfg --file '{new_config_file}'",
            f"Applying new BIOS boot order to {args.node_name}"
        )

        run_command(
            f"'{SUM_EXEC}' -i '{ipmi_address}' -u '{ipmi_user}' -p '{ipmi_pass}' -c SetPowerAction --action 0",
            f"Powering on {args.node_name}"
        )

        print(f"\n{bcolors.BOLD}{bcolors.OKGREEN}BIOS configuration applied and {args.node_name} is powering on.{bcolors.ENDC}")

    run_command(
        f"python3 /home/sysadmin/ansible-provisioning-server/redfish.py {args.node_name} set-boot-to-bios",
        f"Setting one-time boot to BIOS for {args.node_name}"
    )

if __name__ == "__main__":
    main()
