#!/usr/bin/env python3
"""
Proxmox Cluster Status Summary
Shows current cluster status and configuration
"""

import subprocess
import sys
from typing import Dict

NODES = {
    "node1": {"mgmt_ip": "10.10.1.21", "ceph_ip": "10.10.2.21"},
    "node2": {"mgmt_ip": "10.10.1.22", "ceph_ip": "10.10.2.22"},
    "node3": {"mgmt_ip": "10.10.1.23", "ceph_ip": "10.10.2.23"},
    "node4": {"mgmt_ip": "10.10.1.24", "ceph_ip": "10.10.2.24"}
}

def run_ssh_command(host: str, command: str) -> tuple:
    """Run SSH command and return success, stdout, stderr"""
    try:
        cmd = f"ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=no root@{host} '{command}'"
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=15)
        return result.returncode == 0, result.stdout, result.stderr
    except Exception as e:
        return False, "", str(e)

def main():
    """Display cluster status summary"""
    print("=" * 60)
    print("🏗️  Proxmox Cluster Status Summary")
    print("=" * 60)
    print()
    
    # Get cluster status from primary node
    primary_ip = NODES["node1"]["mgmt_ip"]
    
    print("📊 Cluster Membership:")
    success, stdout, stderr = run_ssh_command(primary_ip, "pvecm nodes")
    if success:
        lines = stdout.strip().split('\n')
        for line in lines:
            if 'node' in line.lower():
                print(f"   {line.strip()}")
    else:
        print("   ❌ Could not retrieve cluster membership")
    
    print()
    print("🔍 Cluster Status:")
    success, stdout, stderr = run_ssh_command(primary_ip, "pvecm status")
    if success:
        for line in stdout.split('\n'):
            if any(key in line for key in ['Name:', 'Nodes:', 'Quorate:', 'Expected votes:']):
                print(f"   {line.strip()}")
    else:
        print("   ❌ Could not retrieve cluster status")
    
    print()
    print("🌐 Node Configuration:")
    for node_name, config in NODES.items():
        mgmt_ip = config["mgmt_ip"]
        ceph_ip = config["ceph_ip"]
        
        # Test connectivity
        success, _, _ = run_ssh_command(mgmt_ip, "echo test")
        status = "🟢 Online" if success else "🔴 Offline"
        
        print(f"   {node_name:<6} - Management: {mgmt_ip:<15} Ceph: {ceph_ip:<15} {status}")
    
    print()
    print("🔧 Access Information:")
    print(f"   Web GUI:     https://{primary_ip}:8006")
    print(f"   SSH Access:  ssh root@{primary_ip}")
    print(f"   Root Password: proxmox123")
    print()
    
    print("📝 Next Steps:")
    print("   • Access Proxmox web interface to configure VMs")
    print("   • Set up Ceph storage if needed")
    print("   • Change root passwords for security")
    print("   • Configure firewall rules as needed")
    print()

if __name__ == "__main__":
    main()