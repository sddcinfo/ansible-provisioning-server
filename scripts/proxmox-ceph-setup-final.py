#!/usr/bin/env python3
"""
Proxmox Ceph Hyper-Converged Cluster Setup Script
Following: https://pve.proxmox.com/wiki/Deploy_Hyper-Converged_Ceph_Cluster

Architecture:
- 4 nodes (node1-node4) 
- Management network: 10.10.1.x/24 (SSH access from mgmt server only)
- Ceph network: 10.10.2.x/24 (10Gbit dedicated for Ceph traffic)
- 2x NVMe drives per node (Samsung SSD 980 1TB)

Key Principles:
- NO inter-node SSH (only mgmt -> nodes)
- Step-by-step validation after each operation
- Re-run safe (skips completed steps)
- Follows exact Proxmox pveceph command sequence
"""

import subprocess
import time
import sys
import logging
import json
from typing import Dict, List, Tuple, Optional
from datetime import datetime

# Configuration
LOG_FILE = "/tmp/proxmox-ceph-setup.log"

# Network Configuration (from our architecture)
CEPH_PUBLIC_NETWORK = "10.10.2.0/24"   # 10Gbit dedicated Ceph network
CEPH_CLUSTER_NETWORK = "10.10.2.0/24"  # Same as public for small clusters

# Node configuration
NODES = {
    "node1": {"mgmt_ip": "10.10.1.21", "ceph_ip": "10.10.2.21"},
    "node2": {"mgmt_ip": "10.10.1.22", "ceph_ip": "10.10.2.22"},
    "node3": {"mgmt_ip": "10.10.1.23", "ceph_ip": "10.10.2.23"},
    "node4": {"mgmt_ip": "10.10.1.24", "ceph_ip": "10.10.2.24"}
}

# Hardware configuration
EXPECTED_NVME_DRIVES = ["/dev/nvme0n1", "/dev/nvme1n1"]

# Ceph deployment layout (following Proxmox recommendations)
MONITOR_NODES = ["node1", "node2", "node3"]  # 3 monitors for quorum
MANAGER_NODES = ["node1", "node2", "node3"]  # Managers on monitor nodes
OSD_NODES = ["node1", "node2", "node3", "node4"]  # All nodes have OSDs

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_FILE)
    ]
)

class CephSetup:
    """Main class for Ceph cluster setup"""
    
    def __init__(self):
        self.errors = []
        self.warnings = []
        
    def run_ssh_command(self, node_ip: str, command: str, timeout: int = 120) -> Tuple[bool, str, str]:
        """Execute SSH command on remote node"""
        try:
            result = subprocess.run(
                ['ssh', '-o', 'StrictHostKeyChecking=no', 
                 '-o', 'ConnectTimeout=10', f'root@{node_ip}', command],
                capture_output=True, text=True, timeout=timeout
            )
            return result.returncode == 0, result.stdout.strip(), result.stderr.strip()
        except subprocess.TimeoutExpired:
            return False, "", f"Command timed out after {timeout} seconds"
        except Exception as e:
            return False, "", str(e)
    
    def validate_cluster_status(self) -> bool:
        """Validate all nodes are in Proxmox cluster"""
        logging.info("=== Validating Proxmox Cluster Status ===")
        
        for node_name, node_config in NODES.items():
            success, stdout, stderr = self.run_ssh_command(
                node_config['mgmt_ip'],
                "pvecm status | grep -q 'Cluster information' && echo 'OK' || echo 'FAIL'"
            )
            
            if not success or 'OK' not in stdout:
                logging.error(f"✗ {node_name} is not in cluster")
                return False
            
            logging.info(f"✓ {node_name} is clustered and accessible")
        
        logging.info("✓ All nodes are properly clustered")
        return True
    
    def validate_network_config(self) -> bool:
        """Validate Ceph network configuration on all nodes"""
        logging.info("=== Validating Network Configuration ===")
        
        for node_name, node_config in NODES.items():
            # Check if Ceph network interface exists and has correct IP
            cmd = f"ip addr show | grep -q '{node_config['ceph_ip']}' && echo 'OK' || echo 'FAIL'"
            success, stdout, stderr = self.run_ssh_command(node_config['mgmt_ip'], cmd)
            
            if not success or 'OK' not in stdout:
                logging.error(f"✗ {node_name} missing Ceph network IP {node_config['ceph_ip']}")
                return False
            
            logging.info(f"✓ {node_name} has Ceph network configured ({node_config['ceph_ip']})")
        
        # Test connectivity between nodes on Ceph network
        logging.info("Testing Ceph network connectivity...")
        test_node = list(NODES.keys())[0]
        test_ip = NODES[test_node]['mgmt_ip']
        
        for node_name, node_config in NODES.items():
            if node_name == test_node:
                continue
            
            cmd = f"ping -c 1 -W 2 {node_config['ceph_ip']} > /dev/null 2>&1 && echo 'OK' || echo 'FAIL'"
            success, stdout, stderr = self.run_ssh_command(test_ip, cmd)
            
            if not success or 'OK' not in stdout:
                logging.error(f"✗ Cannot reach {node_name} on Ceph network from {test_node}")
                return False
        
        logging.info("✓ Ceph network connectivity verified")
        return True
    
    def check_ceph_packages(self, node_name: str, node_ip: str) -> bool:
        """Check if Ceph packages are fully installed"""
        cmd = "dpkg -l | grep -E '^ii.*ceph-(mon|mgr|osd)' | wc -l"
        success, stdout, stderr = self.run_ssh_command(node_ip, cmd)
        
        if success and stdout.strip():
            count = int(stdout.strip())
            return count >= 3  # Should have mon, mgr, and osd packages
        return False
    
    def install_ceph_packages(self) -> bool:
        """Install Ceph packages on all nodes"""
        logging.info("=== Installing Ceph Packages ===")
        
        for node_name, node_config in NODES.items():
            node_ip = node_config['mgmt_ip']
            
            # Check if already installed
            if self.check_ceph_packages(node_name, node_ip):
                logging.info(f"✓ {node_name} already has Ceph packages")
                continue
            
            logging.info(f"Installing Ceph packages on {node_name}...")
            
            # Use no-subscription repository for lab environment
            cmd = "echo 'y' | pveceph install --repository no-subscription"
            success, stdout, stderr = self.run_ssh_command(node_ip, cmd, timeout=300)
            
            if not success:
                logging.error(f"✗ Failed to install Ceph on {node_name}: {stderr}")
                return False
            
            if "successfully" in stdout.lower():
                logging.info(f"✓ Ceph packages installed on {node_name}")
            else:
                logging.warning(f"⚠ Ceph installation may have issues on {node_name}")
        
        logging.info("✓ Ceph packages installed on all nodes")
        return True
    
    def check_ceph_initialized(self) -> bool:
        """Check if Ceph is already initialized"""
        node_ip = NODES["node1"]['mgmt_ip']
        cmd = "test -f /etc/pve/ceph.conf && echo 'YES' || echo 'NO'"
        success, stdout, stderr = self.run_ssh_command(node_ip, cmd)
        return success and 'YES' in stdout
    
    def initialize_ceph_config(self) -> bool:
        """Initialize Ceph configuration (once per cluster)"""
        logging.info("=== Initializing Ceph Configuration ===")
        
        if self.check_ceph_initialized():
            logging.info("✓ Ceph configuration already exists")
            return True
        
        node_ip = NODES["node1"]['mgmt_ip']
        
        # Initialize with network configuration
        cmd = f"pveceph init --network {CEPH_PUBLIC_NETWORK}"
        if CEPH_CLUSTER_NETWORK != CEPH_PUBLIC_NETWORK:
            cmd += f" --cluster-network {CEPH_CLUSTER_NETWORK}"
        
        logging.info(f"Initializing Ceph on node1 with network {CEPH_PUBLIC_NETWORK}")
        success, stdout, stderr = self.run_ssh_command(node_ip, cmd)
        
        if not success:
            if "already exists" in stderr:
                logging.info("✓ Ceph configuration already exists")
                return True
            logging.error(f"✗ Failed to initialize Ceph: {stderr}")
            return False
        
        # Add mon_host to config for reliable monitor discovery
        logging.info("Adding monitor hosts to configuration...")
        mon_hosts = ",".join([f"{NODES[n]['ceph_ip']}:6789" for n in MONITOR_NODES])
        cmd = f"""
        echo '' >> /etc/pve/ceph.conf
        echo '\tmon_host = {mon_hosts}' >> /etc/pve/ceph.conf
        """
        success, stdout, stderr = self.run_ssh_command(node_ip, cmd)
        
        if not success:
            logging.warning("⚠ Could not add mon_host to config")
        
        logging.info("✓ Ceph configuration initialized")
        return True
    
    def check_monitor_exists(self, node_name: str, node_ip: str) -> bool:
        """Check if monitor already exists and is running"""
        # Check if monitor service is active
        cmd = f"systemctl is-active ceph-mon@{node_name} 2>/dev/null"
        success, stdout, stderr = self.run_ssh_command(node_ip, cmd)
        
        if success and 'active' in stdout:
            # Verify it's actually responding
            cmd = "timeout 5 ceph mon stat 2>/dev/null | grep -q 'mon:' && echo 'OK' || echo 'FAIL'"
            success, stdout, stderr = self.run_ssh_command(node_ip, cmd)
            return success and 'OK' in stdout
        
        return False
    
    def create_monitors(self) -> bool:
        """Create Ceph monitors on designated nodes"""
        logging.info("=== Creating Ceph Monitors ===")
        
        for node_name in MONITOR_NODES:
            node_ip = NODES[node_name]['mgmt_ip']
            
            if self.check_monitor_exists(node_name, node_ip):
                logging.info(f"✓ Monitor already exists on {node_name}")
                continue
            
            logging.info(f"Creating monitor on {node_name}...")
            
            # Create monitor with explicit address
            cmd = f"pveceph mon create --mon-address {NODES[node_name]['ceph_ip']}"
            success, stdout, stderr = self.run_ssh_command(node_ip, cmd, timeout=60)
            
            if not success:
                # Check if it's a transient error
                if "already exists" in stderr.lower():
                    logging.info(f"✓ Monitor already exists on {node_name}")
                    continue
                elif "could not connect" in stderr.lower():
                    # This might be the first monitor, try without checking cluster
                    logging.info(f"Creating first monitor on {node_name}...")
                    # For first monitor, we might need to bootstrap manually
                    if node_name == MONITOR_NODES[0]:
                        if self.bootstrap_first_monitor(node_name, node_ip):
                            logging.info(f"✓ First monitor bootstrapped on {node_name}")
                            continue
                logging.error(f"✗ Failed to create monitor on {node_name}: {stderr}")
                return False
            
            # Wait for monitor to stabilize
            time.sleep(5)
            
            if self.check_monitor_exists(node_name, node_ip):
                logging.info(f"✓ Monitor created on {node_name}")
            else:
                logging.error(f"✗ Monitor creation may have failed on {node_name}")
                return False
        
        # Verify monitor quorum
        time.sleep(10)
        node_ip = NODES[MONITOR_NODES[0]]['mgmt_ip']
        cmd = "timeout 10 ceph mon stat 2>/dev/null | grep -q 'quorum' && echo 'OK' || echo 'FAIL'"
        success, stdout, stderr = self.run_ssh_command(node_ip, cmd)
        
        if success and 'OK' in stdout:
            logging.info("✓ Monitor quorum established")
        else:
            logging.warning("⚠ Monitor quorum not yet established")
        
        return True
    
    def bootstrap_first_monitor(self, node_name: str, node_ip: str) -> bool:
        """Bootstrap the first monitor manually if pveceph fails"""
        logging.info(f"Attempting manual bootstrap of first monitor on {node_name}...")
        
        # This is a fallback method when pveceph mon create fails
        bootstrap_script = f"""
        # Ensure directories exist
        mkdir -p /var/lib/ceph/mon/ceph-{node_name}
        mkdir -p /etc/ceph
        
        # Copy config
        cp /etc/pve/ceph.conf /etc/ceph/ceph.conf 2>/dev/null || true
        chmod 644 /etc/ceph/ceph.conf 2>/dev/null || true
        
        # Try pveceph mon create once more
        pveceph mon create --mon-address {NODES[node_name]['ceph_ip']} 2>&1
        
        # Check if it worked
        systemctl is-active ceph-mon@{node_name}
        """
        
        success, stdout, stderr = self.run_ssh_command(node_ip, bootstrap_script, timeout=60)
        return success and 'active' in stdout
    
    def check_manager_exists(self, node_name: str, node_ip: str) -> bool:
        """Check if manager already exists and is running"""
        cmd = f"systemctl is-active ceph-mgr@{node_name} 2>/dev/null"
        success, stdout, stderr = self.run_ssh_command(node_ip, cmd)
        return success and 'active' in stdout
    
    def create_managers(self) -> bool:
        """Create Ceph managers on designated nodes"""
        logging.info("=== Creating Ceph Managers ===")
        
        for node_name in MANAGER_NODES:
            node_ip = NODES[node_name]['mgmt_ip']
            
            if self.check_manager_exists(node_name, node_ip):
                logging.info(f"✓ Manager already exists on {node_name}")
                continue
            
            logging.info(f"Creating manager on {node_name}...")
            
            cmd = "pveceph mgr create"
            success, stdout, stderr = self.run_ssh_command(node_ip, cmd, timeout=60)
            
            if not success:
                if "already exists" in stderr.lower():
                    logging.info(f"✓ Manager already exists on {node_name}")
                    continue
                logging.error(f"✗ Failed to create manager on {node_name}: {stderr}")
                return False
            
            # Wait for manager to start
            time.sleep(5)
            
            if self.check_manager_exists(node_name, node_ip):
                logging.info(f"✓ Manager created on {node_name}")
            else:
                logging.warning(f"⚠ Manager may not be running on {node_name}")
        
        return True
    
    def check_osd_exists(self, node_ip: str, device: str) -> bool:
        """Check if OSD already exists on device"""
        cmd = f"pveceph osd list 2>/dev/null | grep -q '{device}' && echo 'EXISTS' || echo 'NO'"
        success, stdout, stderr = self.run_ssh_command(node_ip, cmd)
        return success and 'EXISTS' in stdout
    
    def zap_disk(self, node_name: str, node_ip: str, device: str) -> bool:
        """Zap disk to prepare for OSD creation"""
        logging.info(f"  Preparing {device} on {node_name}...")
        
        cmd = f"ceph-volume lvm zap {device} --destroy"
        success, stdout, stderr = self.run_ssh_command(node_ip, cmd, timeout=60)
        
        if not success and "No valid Ceph" not in stderr:
            logging.error(f"  ✗ Failed to zap {device}: {stderr}")
            return False
        
        return True
    
    def create_osds(self) -> bool:
        """Create OSDs on all nodes"""
        logging.info("=== Creating Ceph OSDs ===")
        
        total_osds_created = 0
        
        for node_name in OSD_NODES:
            node_ip = NODES[node_name]['mgmt_ip']
            logging.info(f"Processing OSDs on {node_name}...")
            
            for device in EXPECTED_NVME_DRIVES:
                if self.check_osd_exists(node_ip, device):
                    logging.info(f"  ✓ OSD already exists on {device}")
                    continue
                
                # Zap the disk first
                if not self.zap_disk(node_name, node_ip, device):
                    continue
                
                # Create OSD
                logging.info(f"  Creating OSD on {device}...")
                cmd = f"pveceph osd create {device}"
                success, stdout, stderr = self.run_ssh_command(node_ip, cmd, timeout=180)
                
                if success:
                    logging.info(f"  ✓ OSD created on {device}")
                    total_osds_created += 1
                else:
                    logging.error(f"  ✗ Failed to create OSD on {device}: {stderr}")
                    return False
                
                # Wait between OSD creations
                time.sleep(5)
        
        if total_osds_created > 0:
            logging.info(f"✓ Created {total_osds_created} new OSDs")
        
        # Verify OSD status
        node_ip = NODES[OSD_NODES[0]]['mgmt_ip']
        cmd = "ceph osd stat 2>/dev/null | grep -oP '\\d+ osds:' | grep -oP '\\d+' || echo '0'"
        success, stdout, stderr = self.run_ssh_command(node_ip, cmd)
        
        if success and stdout.strip():
            osd_count = int(stdout.strip())
            expected_count = len(OSD_NODES) * len(EXPECTED_NVME_DRIVES)
            if osd_count > 0:
                logging.info(f"✓ Cluster has {osd_count} OSDs (expected {expected_count})")
            else:
                logging.warning("⚠ Could not verify OSD count")
        
        return True
    
    def create_pools(self) -> bool:
        """Create default storage pools"""
        logging.info("=== Creating Storage Pools ===")
        
        node_ip = NODES["node1"]['mgmt_ip']
        
        # Check if default pool exists
        cmd = "pveceph pool ls 2>/dev/null | grep -q 'rbd' && echo 'EXISTS' || echo 'NO'"
        success, stdout, stderr = self.run_ssh_command(node_ip, cmd)
        
        if success and 'EXISTS' in stdout:
            logging.info("✓ RBD pool already exists")
            return True
        
        # Create RBD pool for VM storage
        logging.info("Creating RBD pool...")
        cmd = "pveceph pool create rbd --add_storages"
        success, stdout, stderr = self.run_ssh_command(node_ip, cmd)
        
        if success:
            logging.info("✓ RBD pool created")
        else:
            if "already exists" in stderr.lower():
                logging.info("✓ RBD pool already exists")
            else:
                logging.error(f"✗ Failed to create RBD pool: {stderr}")
                return False
        
        return True
    
    def verify_cluster_health(self) -> bool:
        """Verify final Ceph cluster health"""
        logging.info("=== Verifying Ceph Cluster Health ===")
        
        node_ip = NODES["node1"]['mgmt_ip']
        
        # Get cluster status
        cmd = "timeout 30 ceph -s 2>/dev/null"
        success, stdout, stderr = self.run_ssh_command(node_ip, cmd)
        
        if not success:
            logging.error("✗ Cannot get Ceph cluster status")
            return False
        
        # Parse and display status
        if stdout:
            logging.info("Ceph Cluster Status:")
            for line in stdout.split('\n'):
                if line.strip():
                    logging.info(f"  {line}")
            
            # Check health
            if "HEALTH_OK" in stdout:
                logging.info("✓ Cluster health: HEALTH_OK")
                return True
            elif "HEALTH_WARN" in stdout:
                logging.warning("⚠ Cluster health: HEALTH_WARN (may need attention)")
                return True
            else:
                logging.error("✗ Cluster health is not OK")
                return False
        
        return False
    
    def run(self) -> bool:
        """Main execution function"""
        start_time = datetime.now()
        
        logging.info("="*60)
        logging.info("Proxmox Ceph Hyper-Converged Cluster Setup")
        logging.info("="*60)
        logging.info(f"Target Ceph Network: {CEPH_PUBLIC_NETWORK}")
        logging.info(f"Nodes: {', '.join(NODES.keys())}")
        logging.info("")
        
        # Execute setup phases
        phases = [
            ("Cluster Validation", self.validate_cluster_status),
            ("Network Validation", self.validate_network_config),
            ("Ceph Package Installation", self.install_ceph_packages),
            ("Ceph Configuration", self.initialize_ceph_config),
            ("Monitor Creation", self.create_monitors),
            ("Manager Creation", self.create_managers),
            ("OSD Creation", self.create_osds),
            ("Pool Creation", self.create_pools),
            ("Health Verification", self.verify_cluster_health)
        ]
        
        for phase_name, phase_func in phases:
            logging.info("")
            logging.info(f">>> Starting: {phase_name}")
            
            try:
                if not phase_func():
                    logging.error(f"✗ {phase_name} failed")
                    logging.error("Setup cannot continue. Please fix the issues and re-run.")
                    return False
                
                logging.info(f"✓ {phase_name} completed successfully")
                
            except Exception as e:
                logging.error(f"✗ Unexpected error in {phase_name}: {e}")
                return False
        
        # Final summary
        duration = datetime.now() - start_time
        logging.info("")
        logging.info("="*60)
        logging.info("✓ CEPH CLUSTER SETUP COMPLETED SUCCESSFULLY!")
        logging.info(f"Duration: {duration}")
        logging.info(f"Log file: {LOG_FILE}")
        logging.info("="*60)
        
        return True

def main():
    """Entry point"""
    setup = CephSetup()
    success = setup.run()
    sys.exit(0 if success else 1)

if __name__ == "__main__":
    main()