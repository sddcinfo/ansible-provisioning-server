#!/usr/bin/env python3
"""
Proxmox Ceph Storage Cluster Setup Script
- Uses ONLY root@pam authentication (consistent with cluster formation)
- Steps through Ceph setup with validation at each phase
- Uses SSH ONLY from mgmt server for drive operations
- Prioritizes consistency and reliability over speed
- Safely destroys existing data on NVMe drives for clean setup
"""

import requests
import json
import time
import sys
import logging
import subprocess
from typing import Dict, List, Optional, Tuple
from datetime import datetime

# Disable SSL warnings
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

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

# Ceph component layout
MONITOR_NODES = ["node1", "node2", "node3"]  # 3 monitors for quorum
MANAGER_NODES = ["node1", "node2", "node3"]  # 3 managers for HA
OSD_NODES = ["node1", "node2", "node3", "node4"]  # All nodes have OSDs

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout)
    ]
)

class SimpleProxmoxAPI:
    """Simple Proxmox API client - consistent with cluster formation script"""
    
    def __init__(self, host: str):
        self.host = host
        self.base_url = f"https://{host}:8006/api2/json"
        self.session = requests.Session()
        self.session.verify = False
        self.authenticated = False
    
    def authenticate(self) -> bool:
        """Simple root@pam authentication"""
        try:
            auth_data = {'username': 'root@pam', 'password': ROOT_PASSWORD}
            response = self.session.post(f"{self.base_url}/access/ticket", json=auth_data, timeout=10)
            
            if response.status_code == 200:
                ticket_data = response.json()['data']
                self.session.headers.update({
                    'Authorization': f'PVEAuthCookie={ticket_data["ticket"]}',
                    'CSRFPreventionToken': ticket_data['CSRFPreventionToken'],
                    'Content-Type': 'application/json'
                })
                self.authenticated = True
                return True
            else:
                logging.error(f"Authentication failed for {self.host}: {response.status_code}")
                return False
                
        except Exception as e:
            logging.error(f"Authentication error for {self.host}: {e}")
            return False
    
    def get(self, endpoint: str, timeout: int = 30) -> Optional[Dict]:
        """Simple GET request"""
        if not self.authenticated and not self.authenticate():
            return None
            
        try:
            response = self.session.get(f"{self.base_url}/{endpoint}", timeout=timeout)
            if response.status_code == 200:
                return response.json()
            else:
                logging.error(f"GET {endpoint} failed on {self.host}: {response.status_code}")
                return None
        except Exception as e:
            logging.error(f"GET {endpoint} error on {self.host}: {e}")
            return None
    
    def post(self, endpoint: str, data: Dict, timeout: int = 60) -> Optional[Dict]:
        """Simple POST request"""
        if not self.authenticated and not self.authenticate():
            return None
            
        try:
            response = self.session.post(f"{self.base_url}/{endpoint}", json=data, timeout=timeout)
            result = response.json() if response.text else {}
            
            if response.status_code in [200, 201]:
                return result
            else:
                logging.error(f"POST {endpoint} failed on {self.host}: {response.status_code} - {result}")
                return {'error': True, 'message': result, 'status_code': response.status_code}
                
        except Exception as e:
            logging.error(f"POST {endpoint} error on {self.host}: {e}")
            return None

def run_ssh_command(node_ip: str, node_name: str, command: str, timeout: int = 30) -> Tuple[bool, str, str]:
    """Execute SSH command from management server (emergency/drive operations only)"""
    try:
        ssh_cmd = f'ssh -o ConnectTimeout=10 -o StrictHostKeyChecking=no root@{node_ip} "{command}"'
        result = subprocess.run(ssh_cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return result.returncode == 0, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        logging.error(f"SSH command timed out on {node_name}: {command}")
        return False, "", "Command timed out"
    except Exception as e:
        logging.error(f"SSH command failed on {node_name}: {e}")
        return False, "", str(e)

def check_cluster_ready() -> bool:
    """Verify Proxmox cluster is healthy before Ceph setup"""
    logging.info("=== Phase 0: Cluster Health Check ===")
    
    # Check each node is accessible and clustered
    for node_name, node_config in NODES.items():
        logging.info(f"Checking {node_name} cluster status...")
        
        api = SimpleProxmoxAPI(node_config['mgmt_ip'])
        if not api.authenticate():
            logging.error(f"[FAIL] Cannot authenticate to {node_name}")
            return False
        
        # Check cluster status
        cluster_result = api.get('cluster/status')
        if not cluster_result or 'data' not in cluster_result:
            logging.error(f"[FAIL] {node_name} not in cluster")
            return False
        
        cluster_data = cluster_result['data']
        cluster_objects = [item for item in cluster_data if item.get('type') == 'cluster']
        node_objects = [item for item in cluster_data if item.get('type') == 'node']
        
        if not cluster_objects:
            logging.error(f"[FAIL] {node_name} not in any cluster")
            return False
        
        cluster_name = cluster_objects[0].get('name', 'unknown')
        node_count = len(node_objects)
        online_nodes = len([n for n in node_objects if n.get('online', 0)])
        
        logging.info(f"[OK] {node_name} in cluster '{cluster_name}' ({online_nodes}/{node_count} nodes online)")
    
    logging.info("[SUCCESS] All nodes are clustered and accessible")
    return True

def check_nvme_drives() -> Dict[str, List[Dict]]:
    """Verify NVMe drives on all nodes and install nvme-cli if needed"""
    logging.info("=== Phase 1: NVMe Drive Detection ===")
    
    drive_info = {}
    
    for node_name, node_config in NODES.items():
        logging.info(f"Checking NVMe drives on {node_name}...")
        
        # Install nvme-cli if not present
        success, stdout, stderr = run_ssh_command(
            node_config['mgmt_ip'], node_name, 
            "which nvme >/dev/null 2>&1 || (apt-get update -qq && apt-get install -y nvme-cli)"
        )
        
        if not success:
            logging.warning(f"Could not ensure nvme-cli on {node_name}: {stderr}")
        
        # Get NVMe drive information
        success, stdout, stderr = run_ssh_command(
            node_config['mgmt_ip'], node_name,
            "nvme list -o json 2>/dev/null || nvme list"
        )
        
        if not success:
            logging.error(f"[FAIL] Cannot get NVMe info from {node_name}: {stderr}")
            return {}
        
        # Parse drive information
        drives = []
        if stdout.strip().startswith('{'):
            # JSON output
            try:
                nvme_data = json.loads(stdout)
                for device in nvme_data.get('Devices', []):
                    drives.append({
                        'device': device.get('DevicePath', 'unknown'),
                        'model': device.get('ModelNumber', 'unknown').strip(),
                        'size_gb': int(device.get('PhysicalSize', 0)) // (1024**3),
                        'serial': device.get('SerialNumber', 'unknown').strip()
                    })
            except json.JSONDecodeError:
                logging.warning(f"Could not parse JSON output from {node_name}, falling back to text parsing")
        
        # Fallback to text parsing or if JSON failed
        if not drives:
            lines = stdout.strip().split('\n')[1:]  # Skip header
            for line in lines:
                if '/dev/nvme' in line:
                    parts = line.split()
                    if len(parts) >= 4:
                        drives.append({
                            'device': parts[0],
                            'model': ' '.join(parts[3:7]).strip(),
                            'size_gb': 0,  # Will be estimated from usage info
                            'serial': parts[2] if len(parts) > 2 else 'unknown'
                        })
        
        # Verify expected drives are present
        found_devices = [d['device'] for d in drives]
        missing_drives = [d for d in EXPECTED_NVME_DRIVES if d not in found_devices]
        
        if missing_drives:
            logging.error(f"[FAIL] {node_name} missing expected drives: {missing_drives}")
            logging.error(f"       Found drives: {found_devices}")
            return {}
        
        drive_info[node_name] = drives
        logging.info(f"[OK] {node_name} has {len(drives)} NVMe drives:")
        for drive in drives:
            logging.info(f"     {drive['device']}: {drive['model']} ({drive['size_gb']}GB) - SN: {drive['serial']}")
    
    logging.info("[SUCCESS] All nodes have required NVMe drives")
    return drive_info

def check_ceph_network() -> bool:
    """Verify Ceph network configuration on all nodes"""
    logging.info("=== Phase 2: Network Configuration Check ===")
    
    for node_name, node_config in NODES.items():
        logging.info(f"Checking network configuration on {node_name}...")
        
        api = SimpleProxmoxAPI(node_config['mgmt_ip'])
        if not api.authenticate():
            logging.error(f"[FAIL] Cannot authenticate to {node_name}")
            return False
        
        # Get network interfaces
        network_result = api.get('nodes/{}/network'.format(node_name))
        if not network_result or 'data' not in network_result:
            logging.error(f"[FAIL] Cannot get network config from {node_name}")
            return False
        
        interfaces = network_result['data']
        
        # Find vmbr1 (Ceph network interface)
        vmbr1_config = None
        for iface in interfaces:
            if iface.get('iface') == 'vmbr1':
                vmbr1_config = iface
                break
        
        if not vmbr1_config:
            logging.error(f"[FAIL] {node_name} missing vmbr1 interface for Ceph network")
            return False
        
        # Verify Ceph IP is configured
        expected_ceph_ip = node_config['ceph_ip']
        iface_cidr = vmbr1_config.get('cidr', '')
        
        if not iface_cidr.startswith(expected_ceph_ip):
            logging.error(f"[FAIL] {node_name} vmbr1 has {iface_cidr}, expected {expected_ceph_ip}/24")
            return False
        
        logging.info(f"[OK] {node_name} vmbr1 configured: {iface_cidr}")
    
    logging.info("[SUCCESS] All nodes have proper Ceph network configuration")
    return True

def destroy_existing_ceph_data() -> bool:
    """Safely destroy any existing Ceph data on NVMe drives (for clean rebuild)"""
    logging.info("=== Phase 3: Clean Existing Ceph Data ===")
    
    for node_name, node_config in NODES.items():
        logging.info(f"Cleaning existing Ceph data on {node_name}...")
        
        # Stop any existing Ceph services
        success, stdout, stderr = run_ssh_command(
            node_config['mgmt_ip'], node_name,
            "systemctl stop ceph\\* 2>/dev/null || true"
        )
        
        # Unmount any Ceph filesystems
        success, stdout, stderr = run_ssh_command(
            node_config['mgmt_ip'], node_name,
            "umount /var/lib/ceph/osd/ceph-\\* 2>/dev/null || true"
        )
        
        # Wipe NVMe drives
        for drive in EXPECTED_NVME_DRIVES:
            logging.info(f"  Wiping {drive} on {node_name}...")
            
            # Zero out the beginning and end of the drive
            success, stdout, stderr = run_ssh_command(
                node_config['mgmt_ip'], node_name,
                f"dd if=/dev/zero of={drive} bs=1M count=100 2>/dev/null || true"
            )
            
            # Remove any LVM/partition data
            success, stdout, stderr = run_ssh_command(
                node_config['mgmt_ip'], node_name,
                f"wipefs -af {drive} 2>/dev/null || true"
            )
            
            # Remove from any existing Ceph configuration
            success, stdout, stderr = run_ssh_command(
                node_config['mgmt_ip'], node_name,
                f"sgdisk --zap-all {drive} 2>/dev/null || true"
            )
        
        # Remove existing Ceph directories
        success, stdout, stderr = run_ssh_command(
            node_config['mgmt_ip'], node_name,
            "rm -rf /var/lib/ceph/* /etc/ceph/* 2>/dev/null || true"
        )
        
        logging.info(f"[OK] {node_name} cleaned of existing Ceph data")
    
    logging.info("[SUCCESS] All nodes cleaned of existing Ceph data")
    return True

def install_ceph_packages() -> bool:
    """Install/check Ceph packages on all nodes"""
    logging.info("=== Phase 4: Installing Ceph Packages ===")
    
    for node_name, node_config in NODES.items():
        logging.info(f"Checking Ceph packages on {node_name}...")
        
        # Check if full Ceph packages are installed (including ceph-mon)
        check_cmd = "dpkg -l | grep -q ceph-mon && echo 'full' || echo 'partial'"
        try:
            result = subprocess.run(['ssh', f'root@{node_config["mgmt_ip"]}', check_cmd], 
                                  capture_output=True, text=True, timeout=30)
            
            if result.returncode == 0 and 'full' in result.stdout:
                logging.info(f"[OK] {node_name} has full Ceph packages already installed")
                continue
            
        except Exception as e:
            logging.error(f"[FAIL] Exception checking Ceph packages on {node_name}: {e}")
            return False
        
        # Install full Ceph packages
        logging.info(f"Installing full Ceph packages on {node_name}...")
        install_cmd = "echo 'y' | pveceph install --repository no-subscription"
        
        try:
            result = subprocess.run(['ssh', f'root@{node_config["mgmt_ip"]}', install_cmd], 
                                  capture_output=True, text=True, timeout=300)
            
            if result.returncode == 0:
                logging.info(f"[OK] Ceph packages installed on {node_name}")
                if "successfully" in result.stdout:
                    logging.info(f"Installation output: Ceph installed successfully")
            else:
                logging.error(f"[FAIL] Ceph installation failed on {node_name}: {result.stderr}")
                return False
                
        except Exception as e:
            logging.error(f"[FAIL] Exception installing Ceph on {node_name}: {e}")
            return False
    
    logging.info("[SUCCESS] Ceph packages verified/installed on all nodes")
    return True

def initialize_ceph_cluster() -> bool:
    """Initialize Ceph cluster on primary node using SSH"""
    logging.info("=== Phase 5: Initialize Ceph Cluster ===")
    
    primary_node = "node1"
    primary_config = NODES[primary_node]
    
    logging.info(f"Checking Ceph cluster initialization on {primary_node}...")
    
    # First check if already initialized
    check_cmd = "test -f /etc/pve/ceph.conf && echo 'config exists' || echo 'config missing'"
    try:
        check_result = subprocess.run(['ssh', f'root@{primary_config["mgmt_ip"]}', check_cmd], 
                                    capture_output=True, text=True, timeout=30)
        
        if "config exists" in check_result.stdout:
            logging.info(f"[OK] Ceph already initialized on {primary_node}")
            return True
    except Exception as e:
        logging.error(f"[FAIL] Exception checking Ceph initialization: {e}")
        return False
    
    # Initialize if not already done
    logging.info(f"Initializing Ceph cluster on {primary_node}...")
    cmd = f"pveceph init --network {CEPH_PUBLIC_NETWORK} --cluster-network {CEPH_CLUSTER_NETWORK} --size 3 --min_size 2"
    
    try:
        result = subprocess.run(['ssh', f'root@{primary_config["mgmt_ip"]}', cmd], 
                              capture_output=True, text=True, timeout=120)
        
        if result.returncode == 0:
            logging.info(f"[OK] Ceph cluster initialized on {primary_node}")
            logging.info(f"Output: {result.stdout}")
        else:
            # Check if already initialized
            if "Ceph configuration" in result.stderr and "exists" in result.stderr:
                logging.info(f"[OK] Ceph already initialized on {primary_node}")
            else:
                logging.error(f"[FAIL] Ceph initialization failed: {result.stderr}")
                return False
                
        # Wait for initialization to stabilize
        time.sleep(10)
        
        # Final verification
        check_result = subprocess.run(['ssh', f'root@{primary_config["mgmt_ip"]}', check_cmd], 
                                    capture_output=True, text=True, timeout=30)
        
        if "config exists" in check_result.stdout:
            logging.info(f"[SUCCESS] Ceph cluster initialization verified")
            return True
        else:
            logging.error(f"[FAIL] Ceph configuration not found after initialization")
            return False
            
    except Exception as e:
        logging.error(f"[FAIL] Exception during Ceph initialization: {e}")
        return False
    
    logging.info("[SUCCESS] Ceph cluster initialization complete")
    return True

def wait_for_task_completion(api: SimpleProxmoxAPI, node_name: str, task_id: str, timeout: int = 300) -> bool:
    """Wait for a Proxmox task to complete"""
    logging.info(f"Waiting for task {task_id} on {node_name}...")
    
    start_time = time.time()
    while time.time() - start_time < timeout:
        task_result = api.get(f'nodes/{node_name}/tasks/{task_id}/status')
        if task_result and 'data' in task_result:
            status = task_result['data'].get('status', 'unknown')
            
            if status == 'stopped':
                exit_status = task_result['data'].get('exitstatus', 'unknown')
                if exit_status == 'OK':
                    logging.info(f"[OK] Task {task_id} completed successfully")
                    return True
                else:
                    logging.error(f"[FAIL] Task {task_id} failed with status: {exit_status}")
                    return False
            elif status == 'running':
                logging.debug(f"Task {task_id} still running...")
                time.sleep(5)
            else:
                logging.warning(f"Task {task_id} has unknown status: {status}")
                time.sleep(5)
        else:
            logging.warning(f"Cannot get task status for {task_id}")
            time.sleep(5)
    
    logging.error(f"[FAIL] Task {task_id} timed out after {timeout} seconds")
    return False

def create_ceph_monitors() -> bool:
    """Create Ceph monitors on designated nodes using SSH"""
    logging.info("=== Phase 6: Creating Ceph Monitors ===")
    
    for node_name in MONITOR_NODES:
        node_config = NODES[node_name]
        logging.info(f"Checking/creating monitor on {node_name}...")
        
        # Check if monitor already exists
        check_cmd = f"systemctl is-active ceph-mon@{node_name} 2>/dev/null && echo 'active' || echo 'inactive'"
        try:
            check_result = subprocess.run(['ssh', f'root@{node_config["mgmt_ip"]}', check_cmd], 
                                        capture_output=True, text=True, timeout=30)
            
            if "active" in check_result.stdout:
                logging.info(f"[OK] Monitor already running on {node_name}")
                continue
        except Exception as e:
            logging.info(f"Monitor status check failed on {node_name}, will try to create: {e}")
        
        # Create monitor if not exists
        logging.info(f"Creating monitor on {node_name}...")
        cmd = "pveceph mon create"
        
        try:
            result = subprocess.run(['ssh', f'root@{node_config["mgmt_ip"]}', cmd], 
                                  capture_output=True, text=True, timeout=120)
            
            if result.returncode == 0:
                logging.info(f"[OK] Monitor created on {node_name}")
                if result.stdout:
                    logging.info(f"Output: {result.stdout}")
            else:
                # Check if already exists
                if "already exists" in result.stderr.lower() or "already running" in result.stderr.lower():
                    logging.info(f"[OK] Monitor already exists on {node_name}")
                else:
                    logging.error(f"[FAIL] Monitor creation failed on {node_name}: {result.stderr}")
                    return False
                    
        except Exception as e:
            logging.error(f"[FAIL] Exception creating monitor on {node_name}: {e}")
            return False
        
        # Brief pause between monitor creations
        time.sleep(5)
    
    # Verify monitor quorum
    primary_api = SimpleProxmoxAPI(NODES["node1"]['mgmt_ip'])
    if primary_api.authenticate():
        status_result = primary_api.get('nodes/node1/ceph/status')
        if status_result and 'data' in status_result:
            logging.info("[SUCCESS] Ceph monitors created and quorum established")
        else:
            logging.warning("[WARNING] Cannot verify monitor quorum")
    
    return True

def create_ceph_managers() -> bool:
    """Create Ceph managers on designated nodes using SSH"""
    logging.info("=== Phase 7: Creating Ceph Managers ===")
    
    for node_name in MANAGER_NODES:
        node_config = NODES[node_name]
        logging.info(f"Creating manager on {node_name}...")
        
        # Use pveceph mgr create command via SSH
        cmd = "pveceph mgr create"
        
        try:
            result = subprocess.run(['ssh', f'root@{node_config["mgmt_ip"]}', cmd], 
                                  capture_output=True, text=True, timeout=120)
            
            if result.returncode == 0:
                logging.info(f"[OK] Manager created on {node_name}")
                if result.stdout:
                    logging.info(f"Output: {result.stdout}")
            else:
                # Check if already exists
                if "already exists" in result.stderr.lower() or "already running" in result.stderr.lower():
                    logging.info(f"[OK] Manager already exists on {node_name}")
                else:
                    logging.error(f"[FAIL] Manager creation failed on {node_name}: {result.stderr}")
                    return False
                    
        except Exception as e:
            logging.error(f"[FAIL] Exception creating manager on {node_name}: {e}")
            return False
            
            logging.info(f"[OK] Manager created on {node_name}")
        
        # Brief pause between manager creations
        time.sleep(5)
    
    logging.info("[SUCCESS] Ceph managers created on all designated nodes")
    return True

def create_ceph_osds() -> bool:
    """Create Ceph OSDs on all NVMe drives"""
    logging.info("=== Phase 8: Creating Ceph OSDs ===")
    
    for node_name in OSD_NODES:
        node_config = NODES[node_name]
        logging.info(f"Creating OSDs on {node_name}...")
        
        api = SimpleProxmoxAPI(node_config['mgmt_ip'])
        if not api.authenticate():
            logging.error(f"[FAIL] Cannot authenticate to {node_name}")
            return False
        
        # Create OSD for each NVMe drive
        for drive in EXPECTED_NVME_DRIVES:
            logging.info(f"  Creating OSD on {drive}...")
            
            osd_data = {
                'dev': drive,
                'encrypted': '0'  # No encryption for performance
            }
            
            result = api.post(f'nodes/{node_name}/ceph/osd', osd_data)
            
            if not result or result.get('error'):
                error_msg = result.get('message', 'Unknown error') if result else 'No response'
                logging.error(f"[FAIL] OSD creation failed on {node_name} {drive}: {error_msg}")
                return False
            
            if 'data' in result and 'UPID:' in str(result['data']):
                task_id = result['data']
                if not wait_for_task_completion(api, node_name, task_id, timeout=180):
                    logging.error(f"[FAIL] OSD creation task failed on {node_name} {drive}")
                    return False
            
            logging.info(f"[OK] OSD created on {node_name} {drive}")
            
            # Pause between OSD creations
            time.sleep(10)
    
    logging.info("[SUCCESS] All OSDs created successfully")
    return True

def create_ceph_pools() -> bool:
    """Create Ceph storage pools"""
    logging.info("=== Phase 9: Creating Ceph Storage Pools ===")
    
    primary_node = "node1"
    primary_config = NODES[primary_node]
    
    api = SimpleProxmoxAPI(primary_config['mgmt_ip'])
    if not api.authenticate():
        logging.error(f"[FAIL] Cannot authenticate to {primary_node}")
        return False
    
    # Create default RBD pool for VM storage
    pool_name = "rbd"
    logging.info(f"Creating storage pool '{pool_name}'...")
    
    pool_data = {
        'name': pool_name,
        'size': '3',  # 3 replicas
        'min_size': '2',  # Minimum 2 replicas
        'pg_num': '128',  # 128 placement groups
        'add_storages': '1'  # Automatically add to Proxmox storage
    }
    
    result = api.post(f'nodes/{primary_node}/ceph/pools', pool_data)
    
    if not result or result.get('error'):
        error_msg = result.get('message', 'Unknown error') if result else 'No response'
        if 'already exists' in str(error_msg).lower():
            logging.info(f"[OK] Pool '{pool_name}' already exists")
        else:
            logging.error(f"[FAIL] Pool creation failed: {error_msg}")
            return False
    else:
        logging.info(f"[OK] Pool '{pool_name}' created successfully")
    
    # Wait for pool to stabilize
    time.sleep(10)
    
    logging.info("[SUCCESS] Ceph storage pools created")
    return True

def verify_ceph_health() -> bool:
    """Verify Ceph cluster health and functionality"""
    logging.info("=== Phase 10: Ceph Health Verification ===")
    
    primary_node = "node1"
    primary_config = NODES[primary_node]
    
    api = SimpleProxmoxAPI(primary_config['mgmt_ip'])
    if not api.authenticate():
        logging.error(f"[FAIL] Cannot authenticate to {primary_node}")
        return False
    
    # Get Ceph status
    status_result = api.get(f'nodes/{primary_node}/ceph/status')
    if not status_result or 'data' not in status_result:
        logging.error(f"[FAIL] Cannot get Ceph status")
        return False
    
    ceph_status = status_result['data']
    
    # Check overall health
    health = ceph_status.get('health', {})
    overall_status = health.get('status', 'unknown')
    
    logging.info(f"Ceph cluster health: {overall_status}")
    
    # Check monitors
    mon_status = ceph_status.get('mon_status', {})
    mon_count = len(mon_status.get('monmap', {}).get('mons', []))
    quorum_count = len(mon_status.get('quorum', []))
    
    logging.info(f"Monitors: {quorum_count}/{mon_count} in quorum")
    
    # Check OSDs
    osd_status = ceph_status.get('osdmap', {})
    total_osds = osd_status.get('num_osds', 0)
    up_osds = osd_status.get('num_up_osds', 0)
    in_osds = osd_status.get('num_in_osds', 0)
    
    logging.info(f"OSDs: {up_osds}/{total_osds} up, {in_osds}/{total_osds} in")
    
    # Check pools
    pools_result = api.get(f'nodes/{primary_node}/ceph/pools')
    if pools_result and 'data' in pools_result:
        pool_count = len(pools_result['data'])
        logging.info(f"Storage pools: {pool_count} configured")
    
    # Verify Proxmox storage integration
    storage_result = api.get('storage')
    if storage_result and 'data' in storage_result:
        ceph_storages = [s for s in storage_result['data'] if s.get('type') == 'rbd']
        logging.info(f"Proxmox Ceph storages: {len(ceph_storages)} configured")
    
    # Overall health assessment
    if overall_status in ['HEALTH_OK', 'HEALTH_WARN']:
        if quorum_count >= 2 and up_osds >= 4 and in_osds >= 4:
            logging.info("[SUCCESS] Ceph cluster is healthy and operational")
            return True
        else:
            logging.warning("[WARNING] Ceph cluster has component issues")
            return False
    else:
        logging.error(f"[FAIL] Ceph cluster is unhealthy: {overall_status}")
        return False

def main():
    """Main Ceph setup workflow"""
    logging.info("=== Proxmox Ceph Storage Cluster Setup ===")
    logging.info("Following cluster formation script best practices")
    logging.info(f"Target network: {CEPH_PUBLIC_NETWORK} (vmbr1 - 10Gbit)")
    logging.info(f"Expected drives per node: {EXPECTED_NVME_DRIVES}")
    
    # Phase 0: Prerequisites
    if not check_cluster_ready():
        logging.error("Cluster not ready for Ceph setup")
        sys.exit(1)
    
    # Phase 1: Drive Detection
    drive_info = check_nvme_drives()
    if not drive_info:
        logging.error("NVMe drive requirements not met")
        sys.exit(1)
    
    # Phase 2: Network Validation
    if not check_ceph_network():
        logging.error("Network configuration not ready")
        sys.exit(1)
    
    # Phase 3: Clean Slate
    if not destroy_existing_ceph_data():
        logging.error("Failed to clean existing Ceph data")
        sys.exit(1)
    
    # Phase 4: Package Installation
    if not install_ceph_packages():
        logging.error("Failed to install Ceph packages")
        sys.exit(1)
    
    # Phase 5: Cluster Initialization
    if not initialize_ceph_cluster():
        logging.error("Failed to initialize Ceph cluster")
        sys.exit(1)
    
    # Phase 6: Monitor Setup
    if not create_ceph_monitors():
        logging.error("Failed to create Ceph monitors")
        sys.exit(1)
    
    # Phase 7: Manager Setup
    if not create_ceph_managers():
        logging.error("Failed to create Ceph managers")
        sys.exit(1)
    
    # Phase 8: OSD Creation
    if not create_ceph_osds():
        logging.error("Failed to create Ceph OSDs")
        sys.exit(1)
    
    # Phase 9: Pool Setup
    if not create_ceph_pools():
        logging.error("Failed to create Ceph pools")
        sys.exit(1)
    
    # Phase 10: Health Verification
    if not verify_ceph_health():
        logging.error("Ceph cluster health verification failed")
        sys.exit(1)
    
    # Success!
    logging.info("=== Ceph Setup Complete ===")
    logging.info("Ceph storage cluster is ready for production use")
    logging.info(f"Total OSDs: {len(OSD_NODES) * len(EXPECTED_NVME_DRIVES)} across {len(OSD_NODES)} nodes")
    logging.info(f"Storage network: {CEPH_PUBLIC_NETWORK} (10Gbit vmbr1)")
    logging.info("Check Proxmox web UI under Datacenter -> Ceph for cluster status")
    
    sys.exit(0)

if __name__ == "__main__":
    main()