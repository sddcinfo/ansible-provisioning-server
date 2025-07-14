#!/usr/bin/env python3
import argparse
import subprocess
import os
import sys
import json

# --- Configuration ---
SUM_URL = "https://www.supermicro.com/Bios/sw_download/698/sum_2.14.0_Linux_x86_64_20240215.tar.gz"
SUM_DIR = "/home/sysadmin/sum_2.14.0_Linux_x86_64"
SUM_EXEC = f"{SUM_DIR}/sum"
BIOS_CONFIG_FILE = "/tmp/boot_order_config.txt"
NODES_FILE = "/home/sysadmin/ansible-provisioning-server/nodes.json"

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
    """Finds the IP address for a given node hostname."""
    with open(NODES_FILE) as f:
        nodes = json.load(f)["console_nodes"]
    for node in nodes:
        if node.get("hostname") == node_name:
            return node.get("ip")
    return None

def run_command(command, description):
    """Runs a command and handles errors."""
    print(f"{bcolors.OKCYAN}... {description}{bcolors.ENDC}")
    try:
        process = subprocess.run(
            command,
            shell=True,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
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
    parser.add_argument("ipmi_user", help="The username for the IPMI interface.")
    parser.add_argument("ipmi_pass", help="The password for the IPMI interface.")
    parser.add_argument(
        "boot_devices",
        nargs='+',
        choices=BOOT_DEVICE_MAP.keys(),
        help=f"A space-separated list of boot devices. Choices: {', '.join(BOOT_DEVICE_MAP.keys())}"
    )
    args = parser.parse_args()

    ipmi_address = get_node_ip(args.node_name)
    if not ipmi_address:
        print(f"{bcolors.FAIL}Error: Node '{args.node_name}' not found in {NODES_FILE}{bcolors.ENDC}")
        sys.exit(1)

    # --- Download and Extract SUM if not present ---
    if not os.path.exists(SUM_EXEC):
        print(f"{bcolors.WARNING}SUM utility not found. Downloading...{bcolors.ENDC}")
        run_command(
            f"wget -q -O '{SUM_DIR}.tar.gz' '{SUM_URL}'",
            "Downloading SUM utility"
        )
        run_command(
            f"tar -xzf '{SUM_DIR}.tar.gz' -C /home/sysadmin/",
            "Extracting SUM utility"
        )
        run_command(
            f"chmod +x '{SUM_EXEC}'",
            "Making SUM executable"
        )

    # --- Create Desired BIOS Config File ---
    print(f"{bcolors.OKCYAN}Creating BIOS configuration...{bcolors.ENDC}")
    with open(BIOS_CONFIG_FILE, "w") as f:
        f.write("[Boot]\n")
        f.write("Boot Mode Select=01\n")
        for i, device_name in enumerate(args.boot_devices):
            device_code = BOOT_DEVICE_MAP[device_name]
            f.write(f"UEFI Boot Order #{i+1}={device_code}\n")
    print(f"{bcolors.OKGREEN}Success.{bcolors.ENDC}")

    # --- Apply BIOS settings and Reboot ---
    run_command(
        (
            f"'{SUM_EXEC}' -i '{ipmi_address}' -u '{args.ipmi_user}' -p '{args.ipmi_pass}' "
            f"-c ChangeBiosCfg --file '{BIOS_CONFIG_FILE}' --reboot"
        ),
        f"Applying BIOS boot order to {args.node_name} ({ipmi_address}) and rebooting"
    )

    print(f"\n{bcolors.BOLD}{bcolors.OKGREEN}BIOS configuration applied successfully to {args.node_name}.{bcolors.ENDC}")

if __name__ == "__main__":
    main()