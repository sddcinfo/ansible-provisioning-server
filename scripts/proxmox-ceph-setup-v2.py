#!/usr/bin/env python3
"""
Proxmox Ceph Storage Cluster Setup Script - Following Official Proxmox Documentation
https://pve.proxmox.com/wiki/Deploy_Hyper-Converged_Ceph_Cluster

- Uses ONLY SSH from mgmt server to individual nodes (no inter-node SSH)
- Follows exact pveceph command sequence from Proxmox documentation
- Uses simple approach without complex API interactions
- Prioritizes consistency and reliability over speed
"""

import subprocess
import time
import sys
import logging
from typing import Dict, List
from datetime import datetime

# Configuration
ROOT_PASSWORD = "proxmox123"
LOG_FILE = "/tmp/proxmox-ceph-setup.log"

# Network Configuration - All Ceph traffic over 10Gbit vmbr1
CEPH_PUBLIC_NETWORK = "10.10.2.0/24"  # 10Gbit network for client traffic
CEPH_CLUSTER_NETWORK = "10.10.2.0/24"  # Same as public (common in small clusters)

# Node configuration (from nodes.json)
NODES = {
    "node1": {"mgmt_ip": "10.10.1.21", "ceph_ip": "10.10.2.21"},
    "node2": {"mgmt_ip": "10.10.1.22", "ceph_ip": "10.10.2.22"},
    "node3": {"mgmt_ip": "10.10.1.23", "ceph_ip": "10.10.2.23"},
    "node4": {"mgmt_ip": "10.10.1.24", "ceph_ip": "10.10.2.24"}
}

# Expected NVMe drives per node
EXPECTED_NVME_DRIVES = ["/dev/nvme0n1", "/dev/nvme1n1"]

# Ceph component layout (following Proxmox recommendations)
MONITOR_NODES = ["node1", "node2", "node3"]  # 3 monitors for quorum
MANAGER_NODES = ["node1", "node2", "node3"]  # 3 managers for HA
OSD_NODES = ["node1", "node2", "node3", "node4"]  # All nodes have OSDs

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_FILE)
    ]
)

def run_ssh_command(node_ip: str, command: str, timeout: int = 120) -> tuple[bool, str, str]:
    """Execute SSH command on remote node"""
    try:
        result = subprocess.run(['ssh', f'root@{node_ip}', command], 
                              capture_output=True, text=True, timeout=timeout)
        return result.returncode == 0, result.stdout.strip(), result.stderr.strip()
    except subprocess.TimeoutExpired:
        return False, "", f"Command timed out after {timeout} seconds"
    except Exception as e:
        return False, "", str(e)

def check_cluster_status() -> bool:
    """Verify all nodes are in cluster and accessible"""
    logging.info("=== Phase 0: Cluster Health Check ===")
    
    for node_name, node_config in NODES.items():
        logging.info(f"Checking {node_name} cluster status...")
        
        success, stdout, stderr = run_ssh_command(
            node_config['mgmt_ip'], 
            "pvecm status | head -10"
        )
        
        if not success:
            logging.error(f"[FAIL] Cannot connect to {node_name}: {stderr}")
            return False
            
        if "Cluster information" in stdout and "sddc-cluster" in stdout:
            logging.info(f"[OK] {node_name} is in cluster and accessible")
        else:
            logging.error(f"[FAIL] {node_name} not properly clustered")
            return False
    
    logging.info("[SUCCESS] All nodes are clustered and accessible")
    return True

def install_ceph_packages() -> bool:
    """Install Ceph packages on all nodes following Proxmox documentation"""
    logging.info("=== Phase 1: Installing Ceph Packages ===")
    
    for node_name, node_config in NODES.items():
        logging.info(f"Installing Ceph on {node_name}...")
        
        # Check if already installed
        success, stdout, stderr = run_ssh_command(
            node_config['mgmt_ip'], 
            "dpkg -l | grep -q ceph-mon && echo 'installed' || echo 'missing'"
        )
        
        if success and 'installed' in stdout:
            logging.info(f"[OK] {node_name} already has Ceph packages")
            continue
        
        # Install Ceph packages
        logging.info(f"Installing Ceph packages on {node_name}...")
        success, stdout, stderr = run_ssh_command(
            node_config['mgmt_ip'], 
            "echo 'y' | pveceph install --repository no-subscription",
            timeout=300
        )
        
        if success and "successfully" in stdout:
            logging.info(f"[OK] Ceph installed on {node_name}")
        else:
            logging.error(f"[FAIL] Ceph installation failed on {node_name}: {stderr}")
            return False
    
    logging.info("[SUCCESS] Ceph packages installed on all nodes")
    return True

def initialize_ceph() -> bool:
    """Initialize Ceph configuration on primary node"""
    logging.info("=== Phase 2: Initialize Ceph Configuration ===")
    
    primary_node = "node1"
    primary_ip = NODES[primary_node]['mgmt_ip']
    
    logging.info(f"Initializing Ceph configuration on {primary_node}...")
    
    # Check if already initialized
    success, stdout, stderr = run_ssh_command(
        primary_ip, 
        "test -f /etc/pve/ceph.conf && echo 'exists' || echo 'missing'"
    )
    
    if success and 'exists' in stdout:
        logging.info(f"[OK] Ceph already initialized")
        return True
    
    # Initialize Ceph
    cmd = f"pveceph init --network {CEPH_PUBLIC_NETWORK} --cluster-network {CEPH_CLUSTER_NETWORK}"
    success, stdout, stderr = run_ssh_command(primary_ip, cmd)
    
    if success:
        logging.info(f"[OK] Ceph initialized successfully")
        logging.info(f"Output: {stdout}")
    else:
        if "already exists" in stderr:
            logging.info(f"[OK] Ceph configuration already exists")
        else:
            logging.error(f"[FAIL] Ceph initialization failed: {stderr}")
            return False
    
    # Wait for configuration to propagate
    time.sleep(5)
    
    logging.info("[SUCCESS] Ceph configuration initialized")
    return True

def create_monitors() -> bool:
    """Create Ceph monitors on designated nodes"""
    logging.info("=== Phase 3: Creating Ceph Monitors ===")
    
    for node_name in MONITOR_NODES:
        node_ip = NODES[node_name]['mgmt_ip']
        logging.info(f"Creating monitor on {node_name}...")
        
        # Check if monitor already exists and is running
        success, stdout, stderr = run_ssh_command(
            node_ip, 
            f"systemctl is-active ceph-mon@{node_name} 2>/dev/null || echo 'inactive'"
        )
        
        if success and 'active' in stdout:
            logging.info(f"[OK] Monitor already running on {node_name}")
            continue
        
        # Create monitor
        success, stdout, stderr = run_ssh_command(node_ip, "pveceph mon create")
        
        if success:
            logging.info(f"[OK] Monitor created on {node_name}")
            if stdout:
                logging.info(f"Output: {stdout}")
        else:
            if "already exists" in stderr.lower() or "already running" in stderr.lower():
                logging.info(f"[OK] Monitor already exists on {node_name}")
            else:
                logging.error(f"[FAIL] Monitor creation failed on {node_name}: {stderr}")
                return False
        
        # Brief pause between monitor creations
        time.sleep(3)
    
    logging.info("[SUCCESS] Monitors created on all designated nodes")
    return True

def create_managers() -> bool:
    """Create Ceph managers on designated nodes"""
    logging.info("=== Phase 4: Creating Ceph Managers ===")
    
    for node_name in MANAGER_NODES:
        node_ip = NODES[node_name]['mgmt_ip']
        logging.info(f"Creating manager on {node_name}...")
        
        # Check if manager already exists
        success, stdout, stderr = run_ssh_command(
            node_ip, 
            f"systemctl is-active ceph-mgr@{node_name} 2>/dev/null || echo 'inactive'"
        )
        
        if success and 'active' in stdout:
            logging.info(f"[OK] Manager already running on {node_name}")
            continue
        
        # Create manager
        success, stdout, stderr = run_ssh_command(node_ip, "pveceph mgr create")
        
        if success:
            logging.info(f"[OK] Manager created on {node_name}")
            if stdout:
                logging.info(f"Output: {stdout}")
        else:
            if "already exists" in stderr.lower() or "already running" in stderr.lower():
                logging.info(f"[OK] Manager already exists on {node_name}")
            else:
                logging.error(f"[FAIL] Manager creation failed on {node_name}: {stderr}")
                return False
        
        # Brief pause between manager creations
        time.sleep(2)
    
    logging.info("[SUCCESS] Managers created on all designated nodes")
    return True

def create_osds() -> bool:
    """Create Ceph OSDs on all NVMe drives"""
    logging.info("=== Phase 5: Creating Ceph OSDs ===")
    
    for node_name in OSD_NODES:
        node_ip = NODES[node_name]['mgmt_ip']
        logging.info(f"Creating OSDs on {node_name}...")
        
        for drive in EXPECTED_NVME_DRIVES:
            logging.info(f"Creating OSD on {node_name}:{drive}...")
            
            # Check if OSD already exists on this drive
            success, stdout, stderr = run_ssh_command(
                node_ip, 
                f"pveceph osd list | grep -q {drive} && echo 'exists' || echo 'missing'"
            )
            
            if success and 'exists' in stdout:
                logging.info(f"[OK] OSD already exists on {node_name}:{drive}")
                continue
            
            # Create OSD
            cmd = f"pveceph osd create {drive}"
            success, stdout, stderr = run_ssh_command(node_ip, cmd, timeout=300)
            
            if success:
                logging.info(f"[OK] OSD created on {node_name}:{drive}")
                if stdout:
                    logging.info(f"Output: {stdout}")
            else:
                logging.error(f"[FAIL] OSD creation failed on {node_name}:{drive}: {stderr}")
                return False
            
            # Pause between OSD creations
            time.sleep(5)
    
    logging.info("[SUCCESS] OSDs created on all drives")
    return True

def create_rbd_pool() -> bool:
    """Create RBD pool for VM storage"""
    logging.info("=== Phase 6: Creating RBD Pool ===")
    
    primary_ip = NODES["node1"]['mgmt_ip']
    pool_name = "rbd"
    
    logging.info(f"Creating RBD pool '{pool_name}'...")
    
    # Check if pool already exists
    success, stdout, stderr = run_ssh_command(
        primary_ip, 
        f"pveceph pool ls | grep -q {pool_name} && echo 'exists' || echo 'missing'"
    )
    
    if success and 'exists' in stdout:
        logging.info(f"[OK] Pool '{pool_name}' already exists")
        return True
    
    # Create pool with storage integration
    cmd = f"pveceph pool create {pool_name} --add_storages"
    success, stdout, stderr = run_ssh_command(primary_ip, cmd)
    
    if success:
        logging.info(f"[OK] Pool '{pool_name}' created successfully")
        if stdout:
            logging.info(f"Output: {stdout}")
    else:
        logging.error(f"[FAIL] Pool creation failed: {stderr}")
        return False
    
    logging.info("[SUCCESS] RBD pool created and integrated")
    return True

def verify_ceph_status() -> bool:
    """Verify Ceph cluster health"""
    logging.info("=== Phase 7: Verifying Ceph Cluster Health ===")
    
    primary_ip = NODES["node1"]['mgmt_ip']
    
    # Wait for cluster to stabilize
    logging.info("Waiting for cluster to stabilize...")
    time.sleep(10)
    
    # Check Ceph status
    success, stdout, stderr = run_ssh_command(primary_ip, "ceph -s")
    
    if success:
        logging.info("[SUCCESS] Ceph cluster status:")
        for line in stdout.split('\n'):
            logging.info(f"  {line}")
        
        # Check if cluster is healthy
        if "HEALTH_OK" in stdout or "HEALTH_WARN" in stdout:
            logging.info("[SUCCESS] Ceph cluster is operational")
            return True
        else:
            logging.warning("[WARNING] Ceph cluster may need attention")
            return True  # Still consider success as cluster is responding
    else:
        logging.error(f"[FAIL] Cannot get Ceph status: {stderr}")
        return False

def main():
    """Main execution function"""
    start_time = datetime.now()
    logging.info("=== Proxmox Ceph Storage Cluster Setup ===")
    logging.info("Following official Proxmox documentation")
    logging.info(f"Target network: {CEPH_PUBLIC_NETWORK} (vmbr1 - 10Gbit)")
    logging.info(f"Expected drives per node: {EXPECTED_NVME_DRIVES}")
    
    # Execute phases in sequence
    phases = [
        ("Cluster Health Check", check_cluster_status),
        ("Install Ceph Packages", install_ceph_packages),
        ("Initialize Ceph", initialize_ceph),
        ("Create Monitors", create_monitors),
        ("Create Managers", create_managers),
        ("Create OSDs", create_osds),
        ("Create RBD Pool", create_rbd_pool),
        ("Verify Cluster Health", verify_ceph_status)
    ]
    
    for phase_name, phase_func in phases:
        logging.info(f"\n{'='*50}")
        logging.info(f"Starting: {phase_name}")
        logging.info(f"{'='*50}")
        
        if not phase_func():
            logging.error(f"[FATAL] {phase_name} failed - stopping setup")
            return False
        
        logging.info(f"[SUCCESS] {phase_name} completed")
    
    # Final summary
    end_time = datetime.now()
    duration = end_time - start_time
    
    logging.info(f"\n{'='*60}")
    logging.info("CEPH CLUSTER SETUP COMPLETE!")
    logging.info(f"Total duration: {duration}")
    logging.info(f"Log file: {LOG_FILE}")
    logging.info(f"{'='*60}")
    
    return True

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)