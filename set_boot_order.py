#!/usr/bin/env python3
import argparse
import subprocess
import os
import sys
import json
import time

# --- Configuration ---
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
    with open(NODES_FILE) as f:
        nodes = json.load(f)["nodes"]
    for node in nodes:
        if node.get("hostname") == node_name:
            return node.get("console_ip")
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

    # Check if redfish.py script exists
    redfish_script = "/home/sysadmin/ansible-provisioning-server/redfish.py"
    if not os.path.exists(redfish_script):
        print(f"{bcolors.FAIL}Error: redfish.py script not found at {redfish_script}{bcolors.ENDC}")
        sys.exit(1)

    # Power management and boot configuration using redfish.py
    print(f"{bcolors.OKCYAN}Configuring boot order for {args.node_name}...{bcolors.ENDC}")
    
    # Power off the node first
    run_command(
        f"python3 {redfish_script} {args.node_name} power-off",
        f"Powering down {args.node_name}"
    )
    
    print("Waiting for node to power down completely...")
    time.sleep(15)
    
    # Set boot to BIOS setup for configuration
    run_command(
        f"python3 {redfish_script} {args.node_name} set-boot-to-bios",
        f"Setting one-time boot to BIOS for {args.node_name}"
    )
    
    # Power on the node
    run_command(
        f"python3 {redfish_script} {args.node_name} power-on",
        f"Powering on {args.node_name}"
    )
    
    print(f"\n{bcolors.BOLD}{bcolors.OKGREEN}Node {args.node_name} is configured to boot to BIOS setup.{bcolors.ENDC}")
    print(f"{bcolors.WARNING}Manual BIOS configuration required:")
    print(f"  1. Set UEFI Boot Order #1 to: {BOOT_DEVICE_MAP[args.boot_devices[0]]} ({args.boot_devices[0]})")
    print(f"  2. Set UEFI Boot Order #2 to: {BOOT_DEVICE_MAP[args.boot_devices[1]]} ({args.boot_devices[1]})")
    print(f"  3. Save and exit BIOS setup{bcolors.ENDC}")

if __name__ == "__main__":
    main()
