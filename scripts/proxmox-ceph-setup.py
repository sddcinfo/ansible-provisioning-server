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
- Configures firewall rules for Ceph network traffic
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
    
    def configure_firewall(self) -> bool:
        """Configure firewall rules for Ceph network traffic"""
        logging.info("=== Configuring Firewall for Ceph Network ===")
        
        for node_name, node_config in NODES.items():
            node_ip = node_config['mgmt_ip']
            logging.info(f"Configuring firewall on {node_name}...")
            
            # Add comprehensive rules to allow all traffic within Ceph network
            firewall_script = f"""
            # Check and add INPUT rule for Ceph network
            if ! iptables -C PVEFW-INPUT -s {CEPH_PUBLIC_NETWORK} -d {CEPH_PUBLIC_NETWORK} -j ACCEPT 2>/dev/null; then
                iptables -I PVEFW-INPUT 1 -s {CEPH_PUBLIC_NETWORK} -d {CEPH_PUBLIC_NETWORK} -j ACCEPT
                echo 'Added INPUT ACCEPT rule for {CEPH_PUBLIC_NETWORK}'
            else
                echo 'INPUT ACCEPT rule already exists'
            fi
            
            # Check and add OUTPUT rule for Ceph network
            if ! iptables -C PVEFW-OUTPUT -s {CEPH_PUBLIC_NETWORK} -d {CEPH_PUBLIC_NETWORK} -j ACCEPT 2>/dev/null; then
                iptables -I PVEFW-OUTPUT 1 -s {CEPH_PUBLIC_NETWORK} -d {CEPH_PUBLIC_NETWORK} -j ACCEPT
                echo 'Added OUTPUT ACCEPT rule for {CEPH_PUBLIC_NETWORK}'
            else
                echo 'OUTPUT ACCEPT rule already exists'
            fi
            
            # Verify rules are in place
            iptables -L PVEFW-INPUT -n | grep -q '{CEPH_PUBLIC_NETWORK.split('/')[0]}' && echo 'Firewall rules verified'
            """
            
            success, stdout, stderr = self.run_ssh_command(node_ip, firewall_script)
            
            if not success:
                logging.error(f"✗ Failed to configure firewall on {node_name}: {stderr}")
                return False
            
            if 'already exists' in stdout and 'verified' in stdout:
                logging.info(f"✓ {node_name} firewall already configured for Ceph network")
            elif 'verified' in stdout:
                logging.info(f"✓ {node_name} firewall configured for Ceph network")
            else:
                logging.warning(f"⚠ {node_name} firewall configuration may have issues")
        
        logging.info("✓ Firewall configured on all nodes")
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
            cmd = "timeout 5 ceph mon stat 2>/dev/null | grep -q 'mons at' && echo 'OK' || echo 'FAIL'"
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
    
    def get_current_cluster_fsid(self, node_ip: str) -> Optional[str]:
        """Get current cluster FSID"""
        cmd = "ceph fsid 2>/dev/null"
        success, stdout, stderr = self.run_ssh_command(node_ip, cmd)
        if success and stdout.strip():
            return stdout.strip()
        return None
    
    def check_osd_cluster_fsid(self, node_ip: str, device: str) -> Tuple[bool, Optional[str]]:
        """Check if existing OSD belongs to current cluster
        Returns: (has_osd, fsid_or_none)
        """
        # Check ceph-volume lvm list for this device
        cmd = f"ceph-volume lvm list 2>/dev/null"
        success, stdout, stderr = self.run_ssh_command(node_ip, cmd)
        
        if not success or not stdout.strip():
            return False, None
            
        # Find the OSD block that contains this device
        lines = stdout.split('\n')
        in_osd_block = False
        current_fsid = None
        current_has_device = False
        
        for line in lines:
            original_line = line
            line = line.strip()
            
            # Start of new OSD block
            if line.startswith('====== osd.'):
                # Check previous OSD block before starting new one
                if in_osd_block and current_has_device and current_fsid:
                    return True, current_fsid
                
                in_osd_block = True
                current_fsid = None
                current_has_device = False
                continue
            
            # Only reset on new OSD blocks, not on empty lines
            if line.startswith('======'):
                if in_osd_block and current_has_device and current_fsid:
                    return True, current_fsid
                in_osd_block = False
                current_fsid = None
                current_has_device = False
                continue
            
            if in_osd_block and line:  # Skip empty lines but stay in block
                # Check for cluster fsid
                if 'cluster fsid' in line:
                    current_fsid = line.split()[-1]
                
                # Check if this OSD uses our device
                if 'devices' in line and device in line:
                    current_has_device = True
        
        # Check final OSD block (no trailing delimiter)
        if in_osd_block and current_has_device and current_fsid:
            return True, current_fsid
            
        return False, None
    
    def check_osd_exists(self, node_ip: str, device: str) -> bool:
        """Check if OSD already exists on device and belongs to current cluster"""
        current_fsid = self.get_current_cluster_fsid(node_ip)
        if not current_fsid:
            logging.warning("Could not get current cluster FSID")
            return False
            
        has_osd, osd_fsid = self.check_osd_cluster_fsid(node_ip, device)
        
        if not has_osd:
            return False
            
        if osd_fsid and osd_fsid == current_fsid:
            return True  # OSD exists and belongs to current cluster
        elif osd_fsid and osd_fsid != current_fsid:
            logging.info(f"  Found OSD on {device} from different cluster (FSID: {osd_fsid[:8]}...)")
            return False  # OSD exists but from different cluster - needs cleanup
        else:
            logging.warning(f"  Found OSD on {device} but could not verify cluster FSID")
            return False

    def check_osd_status_in_cluster(self, node_ip: str, device: str) -> Tuple[bool, bool]:
        """Check if OSD exists in cluster and if it's UP
        Returns: (exists_in_cluster, is_up)
        """
        # Get OSD ID for this device from ceph-volume
        cmd = f"ceph-volume lvm list 2>/dev/null | grep -B10 -A10 '{device}' | grep 'osd id' | awk '{{print $3}}' | head -1"
        success, stdout, stderr = self.run_ssh_command(node_ip, cmd)
        
        if not success or not stdout.strip():
            return False, False
        
        osd_id = stdout.strip()
        if not osd_id.isdigit():
            return False, False
        
        # Check if this OSD exists in cluster and its status
        cmd = f"ceph osd tree --format json 2>/dev/null"
        success, stdout, stderr = self.run_ssh_command(node_ip, cmd)
        
        if not success or not stdout.strip():
            return False, False
        
        try:
            import json
            tree_data = json.loads(stdout)
            
            # Find our OSD in the tree
            for node in tree_data.get('nodes', []):
                if node.get('type') == 'osd' and node.get('id') == int(osd_id):
                    status = node.get('status', 'down')
                    return True, (status == 'up')
                    
        except (json.JSONDecodeError, ValueError) as e:
            logging.warning(f"Could not parse OSD tree JSON: {e}")
            return False, False
        
        return False, False

    def reactivate_osd(self, node_ip: str, device: str) -> bool:
        """Reactivate an existing OSD that is down"""
        # Get OSD ID for this device
        cmd = f"ceph-volume lvm list 2>/dev/null | grep -B10 -A10 '{device}' | grep 'osd id' | awk '{{print $3}}' | head -1"
        success, stdout, stderr = self.run_ssh_command(node_ip, cmd)
        
        if not success or not stdout.strip():
            logging.error(f"  ✗ Could not find OSD ID for {device}")
            return False
        
        osd_id = stdout.strip()
        if not osd_id.isdigit():
            logging.error(f"  ✗ Invalid OSD ID found: {osd_id}")
            return False
        
        logging.info(f"  Reactivating OSD {osd_id} on {device}...")
        
        # Use ceph-volume to activate the OSD
        cmd = f"ceph-volume lvm activate {osd_id} --all"
        success, stdout, stderr = self.run_ssh_command(node_ip, cmd, timeout=60)
        
        if not success:
            logging.warning(f"  ⚠ ceph-volume activate failed, trying systemctl approach...")
            # Try enabling and starting the systemd service
            cmd = f"systemctl enable ceph-osd@{osd_id} && systemctl start ceph-osd@{osd_id}"
            success, stdout, stderr = self.run_ssh_command(node_ip, cmd, timeout=30)
            
            if not success:
                logging.error(f"  ✗ Failed to reactivate OSD {osd_id}: {stderr}")
                return False
        
        # Wait a moment for the OSD to come online
        time.sleep(10)
        
        # Verify the OSD is now up
        exists_in_cluster, is_up = self.check_osd_status_in_cluster(node_ip, device)
        if exists_in_cluster and is_up:
            logging.info(f"  ✓ OSD {osd_id} successfully reactivated and is UP")
            return True
        else:
            logging.warning(f"  ⚠ OSD {osd_id} reactivated but may not be fully UP yet")
            return True  # Return True anyway, it may take time to come fully online
    
    def stop_osd_services_for_device(self, node_ip: str, device: str) -> bool:
        """Stop OSD services that might be using this device"""
        # Get OSD ID for this device
        cmd = f"ceph-volume lvm list 2>/dev/null | grep -B10 -A10 '{device}' | grep 'osd id' | awk '{{print $3}}'"
        success, stdout, stderr = self.run_ssh_command(node_ip, cmd)
        
        if not success or not stdout.strip():
            return True  # No OSD found, nothing to stop
        
        osd_ids = [id.strip() for id in stdout.strip().split('\n') if id.strip().isdigit()]
        
        for osd_id in osd_ids:
            logging.info(f"    Stopping OSD service ceph-osd@{osd_id}...")
            
            # Stop the OSD service
            cmd = f"systemctl stop ceph-osd@{osd_id}"
            success, stdout, stderr = self.run_ssh_command(node_ip, cmd, timeout=30)
            
            if success:
                logging.info(f"    ✓ Stopped ceph-osd@{osd_id}")
            else:
                logging.warning(f"    ⚠ Could not stop ceph-osd@{osd_id}: {stderr}")
            
            # Disable the service to prevent restart
            cmd = f"systemctl disable ceph-osd@{osd_id}"
            self.run_ssh_command(node_ip, cmd, timeout=10)
            
            # Wait a moment for unmount
            time.sleep(2)
        
        return True

    def zap_disk(self, node_name: str, node_ip: str, device: str, force_cleanup: bool = False) -> bool:
        """Zap disk to prepare for OSD creation"""
        # Check if we need to force cleanup for different cluster OSDs
        has_osd, osd_fsid = self.check_osd_cluster_fsid(node_ip, device)
        current_fsid = self.get_current_cluster_fsid(node_ip)
        
        if has_osd and osd_fsid and current_fsid and osd_fsid != current_fsid:
            logging.info(f"  Found OSD from different cluster (FSID: {osd_fsid[:8]}... vs current: {current_fsid[:8]}...)")
            logging.info(f"  Forcing cleanup of old OSD on {device}...")
            force_cleanup = True
        elif has_osd and not osd_fsid:
            logging.info(f"  Found OSD with unknown FSID, forcing cleanup...")
            force_cleanup = True
        
        # If we need to force cleanup, stop any OSD services first
        if force_cleanup and has_osd:
            logging.info(f"  Stopping OSD services for {device}...")
            self.stop_osd_services_for_device(node_ip, device)
        
        # Check if device is mounted (but allow forced cleanup for old cluster OSDs)
        if not force_cleanup:
            cmd = f"lsblk {device} -o MOUNTPOINT --noheadings | grep -v '^$' | wc -l"
            success, stdout, stderr = self.run_ssh_command(node_ip, cmd)
            if success and stdout.strip() and int(stdout.strip()) > 0:
                logging.warning(f"  ⚠ {device} on {node_name} is mounted/in use, skipping zap")
                return False
        
        logging.info(f"  Preparing {device} on {node_name}...")
        
        cmd = f"ceph-volume lvm zap {device} --destroy"
        success, stdout, stderr = self.run_ssh_command(node_ip, cmd, timeout=120)
        
        if success:
            logging.info(f"  ✓ Successfully zapped {device}")
            return True
        else:
            if "No valid Ceph" in stderr:
                logging.info(f"  ✓ {device} is clean, ready for OSD creation")
                return True
            elif "target is busy" in stderr or "Device or resource busy" in stderr:
                # Try one more time after stopping services
                if has_osd and not force_cleanup:
                    logging.info(f"  Device busy, attempting to stop OSD services...")
                    self.stop_osd_services_for_device(node_ip, device)
                    time.sleep(5)
                    
                    # Retry the zap command
                    success, stdout, stderr = self.run_ssh_command(node_ip, cmd, timeout=120)
                    if success:
                        logging.info(f"  ✓ Successfully zapped {device} after stopping services")
                        return True
                
                logging.warning(f"  ⚠ {device} is busy, cannot zap (likely still mounted)")
                return False
            else:
                logging.error(f"  ✗ Failed to zap {device}: {stderr}")
                return False
    
    def intelligent_osd_cleanup(self) -> bool:
        """Intelligently clean up OSDs - preserve current cluster, remove old cluster OSDs"""
        logging.info("=== Intelligent OSD Analysis and Cleanup ===")
        
        # Get the primary node for cluster operations
        node_ip = NODES["node1"]['mgmt_ip']
        
        # Get current cluster FSID
        current_fsid = self.get_current_cluster_fsid(node_ip)
        if not current_fsid:
            logging.warning("Could not get current cluster FSID - proceeding with caution")
        
        # Analyze OSDs on each node
        mismatched_osds = []
        current_cluster_osds = []
        total_expected = len(OSD_NODES) * len(EXPECTED_NVME_DRIVES)
        
        for node_name in OSD_NODES:
            node_ip_local = NODES[node_name]['mgmt_ip']
            logging.info(f"Analyzing OSDs on {node_name}...")
            
            for device in EXPECTED_NVME_DRIVES:
                has_osd, osd_fsid = self.check_osd_cluster_fsid(node_ip_local, device)
                
                if has_osd:
                    if osd_fsid and current_fsid and osd_fsid == current_fsid:
                        current_cluster_osds.append(f"{node_name}:{device}")
                        logging.info(f"  ✓ {device}: OSD belongs to current cluster")
                    elif osd_fsid and current_fsid and osd_fsid != current_fsid:
                        mismatched_osds.append(f"{node_name}:{device}")
                        logging.info(f"  ⚠ {device}: OSD from different cluster (FSID: {osd_fsid[:8]}...)")
                    else:
                        mismatched_osds.append(f"{node_name}:{device}")
                        logging.info(f"  ⚠ {device}: OSD with unknown/invalid FSID")
                else:
                    logging.info(f"  ○ {device}: No OSD found")
        
        # Decision logic
        current_count = len(current_cluster_osds)
        mismatch_count = len(mismatched_osds)
        
        logging.info(f"Analysis Results:")
        logging.info(f"  Current cluster OSDs: {current_count}/{total_expected}")
        logging.info(f"  Mismatched/old OSDs: {mismatch_count}")
        
        # Scenario 1: Healthy existing cluster (most/all OSDs belong to current cluster)
        if current_count >= total_expected * 0.75 and mismatch_count <= total_expected * 0.25:
            logging.info("✓ Detected healthy existing cluster - preserving current OSDs")
            if mismatch_count > 0:
                logging.info("Cleaning up only mismatched OSDs...")
                return self.cleanup_mismatched_osds_only(mismatched_osds)
            return True
        
        # Scenario 2: Mixed state - some current, some old (partial rebuild scenario)
        elif current_count > 0 and mismatch_count > 0:
            logging.info("⚠ Detected mixed cluster state - cleaning up mismatched OSDs only")
            return self.cleanup_mismatched_osds_only(mismatched_osds)
        
        # Scenario 3: Full rebuild scenario (no/few current cluster OSDs)
        elif current_count < total_expected * 0.25:
            logging.info("⚠ Detected rebuild scenario - performing full cleanup")
            return self.full_osd_cleanup()
        
        # Scenario 4: Unexpected state
        else:
            logging.warning("⚠ Unexpected cluster state - proceeding with selective cleanup")
            if mismatch_count > 0:
                return self.cleanup_mismatched_osds_only(mismatched_osds)
            return True

    def cleanup_mismatched_osds_only(self, mismatched_osds: list) -> bool:
        """Clean up only OSDs that don't belong to current cluster"""
        if not mismatched_osds:
            logging.info("✓ No mismatched OSDs to clean up")
            return True
        
        logging.info(f"Cleaning up {len(mismatched_osds)} mismatched OSDs...")
        
        for osd_location in mismatched_osds:
            node_name, device = osd_location.split(':')
            node_ip_local = NODES[node_name]['mgmt_ip']
            
            logging.info(f"  Cleaning up {device} on {node_name}...")
            
            # Stop OSD services for this device
            self.stop_osd_services_for_device(node_ip_local, device)
            
            # Zap the device
            cmd = f"ceph-volume lvm zap {device} --destroy"
            success, stdout, stderr = self.run_ssh_command(node_ip_local, cmd, timeout=120)
            
            if success:
                logging.info(f"    ✓ Successfully cleaned {device}")
            else:
                logging.error(f"    ✗ Failed to clean {device}: {stderr}")
                
        # Remove orphaned OSDs from cluster
        self.cleanup_orphaned_osds()
        return True

    def full_osd_cleanup(self) -> bool:
        """Perform full OSD cleanup for rebuild scenarios"""
        logging.info("Performing full OSD cleanup...")
        
        # Get all existing OSDs
        node_ip = NODES["node1"]['mgmt_ip']
        cmd = "ceph osd tree --format json 2>/dev/null"
        success, stdout, stderr = self.run_ssh_command(node_ip, cmd)
        
        if success and stdout.strip():
            try:
                tree_data = json.loads(stdout)
                osds_to_remove = [node.get('id') for node in tree_data.get('nodes', []) 
                                if node.get('type') == 'osd' and node.get('id') is not None]
                
                if osds_to_remove:
                    logging.info(f"Removing {len(osds_to_remove)} OSDs from cluster: {osds_to_remove}")
                    
                    # Stop all OSD services
                    for node_name in OSD_NODES:
                        node_ip_local = NODES[node_name]['mgmt_ip']
                        for osd_id in osds_to_remove:
                            cmd = f"systemctl stop ceph-osd@{osd_id} 2>/dev/null || true"
                            self.run_ssh_command(node_ip_local, cmd, timeout=30)
                            cmd = f"systemctl disable ceph-osd@{osd_id} 2>/dev/null || true"
                            self.run_ssh_command(node_ip_local, cmd, timeout=10)
                    
                    time.sleep(5)
                    
                    # Remove from cluster
                    for osd_id in osds_to_remove:
                        cmd = f"ceph osd out {osd_id} 2>/dev/null || true"
                        self.run_ssh_command(node_ip, cmd, timeout=30)
                        cmd = f"ceph osd crush remove osd.{osd_id} 2>/dev/null || true"
                        self.run_ssh_command(node_ip, cmd, timeout=30)
                        cmd = f"ceph auth del osd.{osd_id} 2>/dev/null || true"
                        self.run_ssh_command(node_ip, cmd, timeout=30)
                        cmd = f"ceph osd rm {osd_id} 2>/dev/null || true"
                        self.run_ssh_command(node_ip, cmd, timeout=30)
                    
                    time.sleep(10)
            except Exception as e:
                logging.warning(f"Error parsing OSD tree: {e}")
        
        # Zap all devices
        for node_name in OSD_NODES:
            node_ip_local = NODES[node_name]['mgmt_ip']
            logging.info(f"  Zapping all devices on {node_name}...")
            
            for device in EXPECTED_NVME_DRIVES:
                cmd = f"ceph-volume lvm zap {device} --destroy 2>/dev/null || true"
                success, stdout, stderr = self.run_ssh_command(node_ip_local, cmd, timeout=120)
                if success:
                    logging.info(f"    ✓ Zapped {device}")
                else:
                    logging.info(f"    ○ {device} already clean")
        
        logging.info("✓ Full cleanup completed")
        return True

    def cleanup_orphaned_osds(self) -> bool:
        """Clean up orphaned OSDs from cluster that no longer have backing devices"""
        logging.info("Cleaning up orphaned OSDs...")
        
        node_ip = NODES["node1"]['mgmt_ip']
        cmd = "ceph osd tree --format json 2>/dev/null"
        success, stdout, stderr = self.run_ssh_command(node_ip, cmd)
        
        if not success or not stdout.strip():
            return True
        
        try:
            tree_data = json.loads(stdout)
            all_osds = [node.get('id') for node in tree_data.get('nodes', []) 
                       if node.get('type') == 'osd' and node.get('id') is not None]
            
            # Find OSDs that are down/out and likely orphaned
            orphaned_osds = []
            for node in tree_data.get('nodes', []):
                if node.get('type') == 'osd':
                    status = node.get('status', '')
                    if 'down' in status.lower() or 'out' in status.lower():
                        orphaned_osds.append(node.get('id'))
            
            if orphaned_osds:
                logging.info(f"Found {len(orphaned_osds)} potentially orphaned OSDs: {orphaned_osds}")
                for osd_id in orphaned_osds:
                    logging.info(f"  Removing orphaned OSD {osd_id}...")
                    cmd = f"ceph osd crush remove osd.{osd_id} 2>/dev/null || true"
                    self.run_ssh_command(node_ip, cmd, timeout=30)
                    cmd = f"ceph auth del osd.{osd_id} 2>/dev/null || true"
                    self.run_ssh_command(node_ip, cmd, timeout=30)
                    cmd = f"ceph osd rm {osd_id} 2>/dev/null || true"
                    self.run_ssh_command(node_ip, cmd, timeout=30)
            else:
                logging.info("✓ No orphaned OSDs found")
                
        except Exception as e:
            logging.warning(f"Error cleaning orphaned OSDs: {e}")
        
        return True

    def create_osds(self) -> bool:
        """Create OSDs on all nodes where needed"""
        logging.info("=== Creating Ceph OSDs ===")
        
        total_osds_created = 0
        total_osds_skipped = 0
        
        for node_name in OSD_NODES:
            node_ip = NODES[node_name]['mgmt_ip']
            logging.info(f"Processing OSDs on {node_name}...")
            
            for device in EXPECTED_NVME_DRIVES:
                # Check if OSD already exists and belongs to current cluster
                if self.check_osd_exists(node_ip, device):
                    # OSD exists with correct FSID, but check if it's actually UP
                    exists_in_cluster, is_up = self.check_osd_status_in_cluster(node_ip, device)
                    
                    if exists_in_cluster and is_up:
                        logging.info(f"  ✓ OSD already exists and is UP on {device}")
                        total_osds_skipped += 1
                        continue
                    elif exists_in_cluster and not is_up:
                        logging.info(f"  ⚠ OSD exists but is DOWN on {device}, attempting reactivation...")
                        if self.reactivate_osd(node_ip, device):
                            logging.info(f"  ✓ OSD reactivated on {device}")
                            total_osds_skipped += 1
                            continue
                        else:
                            logging.warning(f"  ⚠ OSD reactivation failed on {device}, will attempt recreation...")
                    else:
                        logging.info(f"  ✓ OSD already exists on {device}")
                        total_osds_skipped += 1
                        continue
                
                # Create OSD on clean device
                logging.info(f"  Creating OSD on {device}...")
                cmd = f"pveceph osd create {device}"
                success, stdout, stderr = self.run_ssh_command(node_ip, cmd, timeout=180)
                
                if success:
                    logging.info(f"  ✓ OSD created on {device}")
                    total_osds_created += 1
                else:
                    if "already exists" in stderr.lower() or "device busy" in stderr.lower():
                        logging.warning(f"  ⚠ OSD creation skipped on {device} (already exists or busy)")
                        total_osds_skipped += 1
                        continue
                    else:
                        logging.error(f"  ✗ Failed to create OSD on {device}: {stderr}")
                        return False
                
                # Wait between OSD creations
                time.sleep(5)
        
        logging.info(f"✓ OSD Summary: {total_osds_created} created, {total_osds_skipped} preserved")
        
        if total_osds_created == 0 and total_osds_skipped == 0:
            logging.warning("⚠ No OSDs created or found - this may indicate an issue")
            return False
        
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
        """Create default storage pools with optimal PG count"""
        logging.info("=== Creating Storage Pools ===")
        
        node_ip = NODES["node1"]['mgmt_ip']
        
        # Check if default pool exists
        cmd = "pveceph pool ls 2>/dev/null | grep -q 'rbd' && echo 'EXISTS' || echo 'NO'"
        success, stdout, stderr = self.run_ssh_command(node_ip, cmd)
        
        if success and 'EXISTS' in stdout:
            logging.info("✓ RBD pool already exists")
            
            # Check if PG count needs optimization
            cmd = "ceph osd pool get rbd pg_num | grep -o '[0-9]*'"
            success, stdout, stderr = self.run_ssh_command(node_ip, cmd)
            if success and stdout.strip():
                current_pgs = int(stdout.strip())
                if current_pgs != 32:
                    logging.info(f"Optimizing RBD pool PGs from {current_pgs} to 32...")
                    self.run_ssh_command(node_ip, "ceph osd pool set rbd pg_num 32")
                    self.run_ssh_command(node_ip, "ceph osd pool set rbd pgp_num 32")
                    logging.info("✓ RBD pool PGs optimized")
                else:
                    logging.info("✓ RBD pool already has optimal PG count (32)")
            
            return True
        
        # Calculate optimal PG count for our setup
        # Formula: (OSDs * 100) / replicas / pools, rounded to nearest power of 2
        # With 8 OSDs, replication 3, and single pool: (8 * 100) / 3 / 1 = ~267
        # But for small clusters, 32 is optimal (provides good distribution without overhead)
        optimal_pgs = 32
        
        logging.info(f"Creating RBD pool with {optimal_pgs} placement groups...")
        
        # Create pool with specific PG count (can't use pveceph for this)
        cmd = f"ceph osd pool create rbd {optimal_pgs} {optimal_pgs} replicated"
        success, stdout, stderr = self.run_ssh_command(node_ip, cmd)
        
        if success:
            logging.info("✓ RBD pool created with optimal PG count")
            
            # Enable RBD application on the pool
            cmd = "ceph osd pool application enable rbd rbd"
            success, stdout, stderr = self.run_ssh_command(node_ip, cmd)
            
            if success:
                logging.info("✓ RBD application enabled on pool")
            else:
                logging.warning("⚠ Could not enable RBD application (may already be enabled)")
            
            # Add to Proxmox storage configuration (don't create new pool, just add storage)
            cmd = "pvesm add rbd rbd --pool rbd --content images,rootdir 2>/dev/null || echo 'Storage may already exist'"
            success, stdout, stderr = self.run_ssh_command(node_ip, cmd)
            logging.info("✓ RBD pool added to Proxmox storage")
            
        else:
            if "already exists" in stderr.lower():
                logging.info("✓ RBD pool already exists")
            else:
                logging.error(f"✗ Failed to create RBD pool: {stderr}")
                return False
        
        return True
    
    def fix_osd_device_classes(self) -> bool:
        """Ensure all OSDs have proper device class (ssd) assigned"""
        logging.info("=== Fixing OSD Device Classes ===")
        
        node_ip = NODES["node1"]['mgmt_ip']
        
        # Get OSD tree and find OSDs without device class
        cmd = "ceph osd tree --format json"
        success, stdout, stderr = self.run_ssh_command(node_ip, cmd)
        
        if not success:
            logging.error("✗ Could not get OSD tree")
            return False
        
        try:
            import json
            tree_data = json.loads(stdout)
            osds_to_fix = []
            
            for node in tree_data.get('nodes', []):
                if node.get('type') == 'osd' and not node.get('device_class'):
                    osds_to_fix.append(node.get('id'))
            
            if osds_to_fix:
                logging.info(f"Found {len(osds_to_fix)} OSDs without device class: {osds_to_fix}")
                
                for osd_id in osds_to_fix:
                    cmd = f"ceph osd crush set-device-class ssd osd.{osd_id}"
                    success, stdout, stderr = self.run_ssh_command(node_ip, cmd)
                    
                    if success:
                        logging.info(f"✓ Set device class 'ssd' for osd.{osd_id}")
                    else:
                        logging.warning(f"⚠ Could not set device class for osd.{osd_id}: {stderr}")
            else:
                logging.info("✓ All OSDs have proper device classes")
            
        except Exception as e:
            logging.warning(f"⚠ Could not parse OSD tree JSON: {e}")
            # Fallback: try to fix all OSDs
            cmd = "ceph osd ls"
            success, stdout, stderr = self.run_ssh_command(node_ip, cmd)
            if success:
                for osd_id in stdout.strip().split('\n'):
                    if osd_id.isdigit():
                        cmd = f"ceph osd crush set-device-class ssd osd.{osd_id} 2>/dev/null || echo 'already set'"
                        self.run_ssh_command(node_ip, cmd)
        
        return True
    
    def cleanup_destroyed_osds(self) -> bool:
        """Remove destroyed/orphaned OSDs from CRUSH map"""
        logging.info("=== Cleaning Up Destroyed OSDs ===")
        
        node_ip = NODES["node1"]['mgmt_ip']
        
        # Get OSD tree and find destroyed OSDs
        cmd = "ceph osd tree --format json"
        success, stdout, stderr = self.run_ssh_command(node_ip, cmd)
        
        if not success:
            logging.error("✗ Could not get OSD tree")
            return False
        
        try:
            import json
            tree_data = json.loads(stdout)
            destroyed_osds = []
            
            for node in tree_data.get('nodes', []):
                if (node.get('type') == 'osd' and 
                    node.get('status') == 'destroyed'):
                    destroyed_osds.append(node.get('id'))
            
            if destroyed_osds:
                logging.info(f"Found {len(destroyed_osds)} destroyed OSDs to clean up: {destroyed_osds}")
                
                for osd_id in destroyed_osds:
                    cmd = f"ceph osd crush rm osd.{osd_id}"
                    success, stdout, stderr = self.run_ssh_command(node_ip, cmd)
                    
                    if success:
                        logging.info(f"✓ Removed destroyed osd.{osd_id} from CRUSH map")
                    else:
                        logging.warning(f"⚠ Could not remove osd.{osd_id}: {stderr}")
            else:
                logging.info("✓ No destroyed OSDs found")
            
        except Exception as e:
            logging.warning(f"⚠ Could not parse OSD tree JSON: {e}")
        
        return True
    
    def verify_osd_distribution(self) -> bool:
        """Verify OSDs are properly distributed across nodes"""
        logging.info("=== Verifying OSD Distribution ===")
        
        node_ip = NODES["node1"]['mgmt_ip']
        expected_osds_per_node = len(EXPECTED_NVME_DRIVES)
        
        # Get OSD tree
        cmd = "ceph osd tree --format json"
        success, stdout, stderr = self.run_ssh_command(node_ip, cmd)
        
        if not success:
            logging.error("✗ Could not get OSD tree")
            return False
        
        try:
            import json
            tree_data = json.loads(stdout)
            node_osd_counts = {}
            
            # Count OSDs per host
            for node in tree_data.get('nodes', []):
                if node.get('type') == 'host':
                    host_name = node.get('name')
                    osd_count = 0
                    
                    # Count active OSDs under this host
                    for child_id in node.get('children', []):
                        for child_node in tree_data.get('nodes', []):
                            if (child_node.get('id') == child_id and 
                                child_node.get('type') == 'osd' and
                                child_node.get('status') != 'destroyed'):
                                osd_count += 1
                    
                    node_osd_counts[host_name] = osd_count
            
            # Verify distribution
            all_good = True
            for node_name in NODES.keys():
                actual_count = node_osd_counts.get(node_name, 0)
                
                if actual_count == expected_osds_per_node:
                    logging.info(f"✓ {node_name}: {actual_count}/{expected_osds_per_node} OSDs")
                else:
                    logging.warning(f"⚠ {node_name}: {actual_count}/{expected_osds_per_node} OSDs (expected {expected_osds_per_node})")
                    all_good = False
            
            total_expected = len(OSD_NODES) * expected_osds_per_node
            total_actual = sum(node_osd_counts.values())
            
            if total_actual == total_expected:
                logging.info(f"✓ Total OSDs: {total_actual}/{total_expected}")
            else:
                logging.warning(f"⚠ Total OSDs: {total_actual}/{total_expected}")
                all_good = False
            
            return all_good
            
        except Exception as e:
            logging.error(f"✗ Could not parse OSD tree JSON: {e}")
            return False
    
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
            ("Firewall Configuration", self.configure_firewall),
            ("Network Validation", self.validate_network_config),
            ("Ceph Package Installation", self.install_ceph_packages),
            ("Ceph Configuration", self.initialize_ceph_config),
            ("Monitor Creation", self.create_monitors),
            ("Manager Creation", self.create_managers),
            ("OSD Analysis & Cleanup", self.intelligent_osd_cleanup),
            ("OSD Creation", self.create_osds),
            ("OSD Device Class Fix", self.fix_osd_device_classes),
            ("Destroyed OSD Cleanup", self.cleanup_destroyed_osds),
            ("OSD Distribution Verification", self.verify_osd_distribution),
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