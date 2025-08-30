#!/usr/bin/env python3
"""
Proxmox Cluster and Ceph Storage Status Summary
Shows current cluster status, configuration, and Ceph storage health
"""

import subprocess
import sys
import os
import json
import re
from typing import Dict, Optional

# Default node configuration - can be overridden with NODE_CONFIG_FILE environment variable
DEFAULT_NODES = {
    "node1": {"mgmt_ip": "10.10.1.21", "ceph_ip": "10.10.2.21"},
    "node2": {"mgmt_ip": "10.10.1.22", "ceph_ip": "10.10.2.22"},
    "node3": {"mgmt_ip": "10.10.1.23", "ceph_ip": "10.10.2.23"},
    "node4": {"mgmt_ip": "10.10.1.24", "ceph_ip": "10.10.2.24"}
}

def load_node_config():
    """Load node configuration from file or use defaults"""
    config_file = os.environ.get('NODE_CONFIG_FILE')
    if config_file and os.path.exists(config_file):
        try:
            with open(config_file, 'r') as f:
                config = json.load(f)
                nodes = {}
                for node_data in config.get('nodes', []):
                    hostname = node_data['os_hostname']
                    nodes[hostname] = {
                        'mgmt_ip': node_data['os_ip'],
                        'ceph_ip': node_data['ceph_ip']
                    }
                return nodes
        except Exception as e:
            print(f"Warning: Failed to load node config from {config_file}: {e}")
    return DEFAULT_NODES

NODES = load_node_config()

def run_ssh_command(host: str, command: str) -> tuple:
    """Run SSH command and return success, stdout, stderr"""
    try:
        cmd = f"ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=no root@{host} '{command}'"
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=15)
        return result.returncode == 0, result.stdout, result.stderr
    except Exception as e:
        return False, "", str(e)

def check_ceph_installed(primary_ip: str) -> bool:
    """Check if Ceph is installed and configured"""
    success, stdout, stderr = run_ssh_command(primary_ip, "test -f /etc/pve/ceph.conf && echo 'YES' || echo 'NO'")
    return success and 'YES' in stdout

def get_ceph_status(primary_ip: str) -> Optional[Dict]:
    """Get Ceph cluster status"""
    if not check_ceph_installed(primary_ip):
        return None
    
    success, stdout, stderr = run_ssh_command(primary_ip, "timeout 10 ceph -s --format json 2>/dev/null")
    if success and stdout.strip():
        try:
            return json.loads(stdout)
        except json.JSONDecodeError:
            return None
    return None

def get_ceph_pools(primary_ip: str) -> Optional[list]:
    """Get Ceph pool information"""
    success, stdout, stderr = run_ssh_command(primary_ip, "timeout 10 ceph osd pool ls detail --format json 2>/dev/null")
    if success and stdout.strip():
        try:
            return json.loads(stdout)
        except json.JSONDecodeError:
            return None
    return None

def format_bytes(bytes_val: int) -> str:
    """Format bytes into human readable format"""
    for unit in ['B', 'KB', 'MB', 'GB', 'TB', 'PB']:
        if bytes_val < 1024.0:
            return f"{bytes_val:.1f} {unit}"
        bytes_val /= 1024.0
    return f"{bytes_val:.1f} EB"

def display_ceph_status(primary_ip: str):
    """Display Ceph cluster status"""
    print("Ceph Storage Cluster:")
    
    if not check_ceph_installed(primary_ip):
        print("   Status: Not installed or configured")
        return
    
    ceph_status = get_ceph_status(primary_ip)
    if not ceph_status:
        print("   Status: Installed but not responding")
        return
    
    # Overall health
    health = ceph_status.get('health', {}).get('status', 'UNKNOWN')
    health_color = {
        'HEALTH_OK': '✓',
        'HEALTH_WARN': '⚠',
        'HEALTH_ERR': '✗'
    }.get(health, '?')
    print(f"   Health: {health_color} {health}")
    
    # Services
    services = ceph_status.get('servicemap', {}).get('services', {})
    
    # Monitors
    mon_info = services.get('mon', {})
    if mon_info:
        mon_count = len(mon_info.get('daemons', {}))
        quorum_names = ceph_status.get('quorum_names', [])
        print(f"   Monitors: {mon_count} total, {len(quorum_names)} in quorum ({', '.join(quorum_names)})")
    
    # Managers
    mgr_summary = ceph_status.get('mgrmap', {})
    if mgr_summary:
        active_mgr = mgr_summary.get('active_name', 'none')
        standby_mgrs = [mgr['name'] for mgr in mgr_summary.get('standbys', [])]
        standby_str = f", standbys: {', '.join(standby_mgrs)}" if standby_mgrs else ""
        print(f"   Managers: {active_mgr} (active){standby_str}")
    
    # OSDs
    osd_summary = ceph_status.get('osdmap', {}).get('osdmap', {})
    if osd_summary:
        num_osds = osd_summary.get('num_osds', 0)
        num_up_osds = osd_summary.get('num_up_osds', 0)
        num_in_osds = osd_summary.get('num_in_osds', 0)
        print(f"   OSDs: {num_osds} total, {num_up_osds} up, {num_in_osds} in")
    
    # Storage usage
    pgmap = ceph_status.get('pgmap', {})
    if pgmap:
        total_bytes = pgmap.get('bytes_total', 0)
        used_bytes = pgmap.get('bytes_used', 0)
        avail_bytes = pgmap.get('bytes_avail', 0)
        if total_bytes > 0:
            used_pct = (used_bytes / total_bytes) * 100
            print(f"   Storage: {format_bytes(used_bytes)} used / {format_bytes(total_bytes)} total ({used_pct:.1f}% used)")
    
    # Pool information
    pools = get_ceph_pools(primary_ip)
    if pools:
        print(f"   Pools: {len(pools)} configured")
        for pool in pools:
            pool_name = pool.get('pool_name', 'unknown')
            pg_num = pool.get('pg_num', 0)
            size = pool.get('size', 0)
            min_size = pool.get('min_size', 0)
            print(f"     - {pool_name}: {pg_num} PGs, replication {size}/{min_size}")

def main():
    """Display cluster and storage status summary"""
    print("=" * 70)
    print("Proxmox Cluster and Ceph Storage Status Summary")
    print("=" * 70)
    print()
    
    # Get cluster status from primary node
    primary_ip = NODES["node1"]["mgmt_ip"]
    
    print("Cluster Membership:")
    success, stdout, stderr = run_ssh_command(primary_ip, "pvecm nodes")
    if success:
        lines = stdout.strip().split('\n')
        for line in lines:
            if 'node' in line.lower():
                print(f"   {line.strip()}")
    else:
        print("   ERROR: Could not retrieve cluster membership")
    
    print()
    print("Cluster Status:")
    success, stdout, stderr = run_ssh_command(primary_ip, "pvecm status")
    if success:
        for line in stdout.split('\n'):
            if any(key in line for key in ['Name:', 'Nodes:', 'Quorate:', 'Expected votes:']):
                print(f"   {line.strip()}")
    else:
        print("   ERROR: Could not retrieve cluster status")
    
    print()
    print("Node Configuration:")
    for node_name, config in NODES.items():
        mgmt_ip = config["mgmt_ip"]
        ceph_ip = config["ceph_ip"]
        
        # Test connectivity
        success, _, _ = run_ssh_command(mgmt_ip, "echo test")
        status = "Online" if success else "Offline"
        
        print(f"   {node_name:<6} - Management: {mgmt_ip:<15} Ceph: {ceph_ip:<15} {status}")
    
    print()
    # Ceph Status
    display_ceph_status(primary_ip)
    
    print()
    print("Access Information:")
    print(f"   Web GUI:     https://{primary_ip}:8006")
    print(f"   SSH Access:  ssh root@{primary_ip}")
    root_password = os.environ.get('PROXMOX_ROOT_PASSWORD', 'proxmox123')
    print(f"   Root Password: {root_password}")
    if check_ceph_installed(primary_ip):
        print(f"   Ceph Dashboard: https://{primary_ip}:8443 (if enabled)")
    print()
    
    print("Management Commands:")
    print(f"   Cluster status:  ssh root@{primary_ip} 'pvecm status'")
    if check_ceph_installed(primary_ip):
        print(f"   Ceph status:     ssh root@{primary_ip} 'ceph -s'")
        print(f"   Ceph health:     ssh root@{primary_ip} 'ceph health detail'")
    print()
    
    print("Next Steps:")
    print("   - Access Proxmox web interface to configure VMs")
    if check_ceph_installed(primary_ip):
        print("   - Create RBD images for VM storage: pvesm alloc <storage> <vmid> <name> <size>")
    else:
        print("   - Set up Ceph storage: ./proxmox-ceph-setup.py")
    print("   - Change root passwords for security")
    print("   - Configure firewall rules as needed")
    print()

if __name__ == "__main__":
    main()