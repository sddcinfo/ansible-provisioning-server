#!/usr/bin/env python3
import json
import os
import sys
import argparse
import time
from pathlib import Path
from mount_iso_http import mount_iso, get_auth_header, find_cd_media_url, make_request

# --- Configuration ---
NODES_FILE = Path(__file__).parent.parent / "nodes.json"
PROVISION_SERVER_IP = "10.10.1.1"
BMC_USER = "admin"
BMC_PASS = "blocked1"

def load_nodes():
    with open(NODES_FILE) as f:
        return json.load(f).get("nodes", [])

def reprovision_node(node_info, iso_url):
    hostname = node_info['os_hostname']
    bmc_ip = node_info.get('console_ip')
    
    if not bmc_ip:
        print(f"Error: No BMC IP for {hostname}")
        return False

    print(f"\n{'='*60}")
    print(f"Reprovisioning {hostname} ({bmc_ip})")
    print(f"{ '='*60}")

    # 1. Reset API status (optional but recommended)
    # This matches the logic in reboot-nodes-for-reprovision.py
    import requests
    api_url = f"http://{PROVISION_SERVER_IP}/index.php?action=reprovision&mac={node_info['os_mac']}&os_type=proxmox9"
    try:
        r = requests.get(api_url, timeout=10)
        print(f"API Reset Status: {r.status_code}")
    except Exception as e:
        print(f"API Reset Failed (continuing): {e}")

    # 2. Mount ISO and Reboot via Redfish
    try:
        mount_iso(bmc_ip, BMC_USER, BMC_PASS, iso_url)
        print(f"Successfully initiated Redfish provisioning for {hostname}")
        return True
    except Exception as e:
        print(f"Redfish Provisioning Failed for {hostname}: {e}")
        return False

def main():
    parser = argparse.ArgumentParser(description="Reprovision nodes using Redfish ISO mount")
    parser.add_argument("--nodes", help="Comma-separated list of hostnames (e.g. node1,node2)")
    parser.add_argument("--all", action="store_true", help="Reprovision all nodes")
    parser.add_argument("--iso", help="Custom ISO URL (defaults to Proxmox 9 on local server)")

    args = parser.parse_args()

    nodes = load_nodes()
    
    if args.all:
        selected_nodes = nodes
    elif args.nodes:
        names = args.nodes.split(",")
        selected_nodes = [n for n in nodes if n['os_hostname'] in names]
    else:
        parser.print_help()
        sys.exit(1)

    if not selected_nodes:
        print("No nodes selected or found.")
        sys.exit(1)

    default_iso = f"http://{PROVISION_SERVER_IP}/provisioning/proxmox9/proxmox-ve_9.0-1.iso"
    iso_url = args.iso if args.iso else default_iso

    success_count = 0
    for node in selected_nodes:
        if reprovision_node(node, iso_url):
            success_count += 1
        # Small delay between nodes
        time.sleep(2)

    print(f"\nSummary: Successfully initiated {success_count}/{len(selected_nodes)} nodes.")

if __name__ == "__main__":
    main()
