#!/usr/bin/env python3
"""
Proxmox Cluster Formation Script - Python Implementation
Uses Proxmox REST API for reliable cluster formation without SSH dependencies
"""

import requests
import json
import time
import sys
import logging
from typing import Dict, List, Tuple, Optional
from datetime import datetime
import subprocess

# Disable SSL warnings for self-signed certificates
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Configuration
PROVISION_SERVER = "10.10.1.1"
CLUSTER_NAME = "sddc-cluster"
LOG_FILE = "/tmp/proxmox-cluster-formation-python.log"

# Node configuration
NODES = {
    "node1": {"mgmt_ip": "10.10.1.21", "ceph_ip": "10.10.2.21"},
    "node2": {"mgmt_ip": "10.10.1.22", "ceph_ip": "10.10.2.22"},
    "node3": {"mgmt_ip": "10.10.1.23", "ceph_ip": "10.10.2.23"},
    "node4": {"mgmt_ip": "10.10.1.24", "ceph_ip": "10.10.2.24"}
}

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

class ProxmoxAPI:
    """Proxmox API client for cluster operations"""
    
    def __init__(self, host: str):
        self.host = host
        self.base_url = f"https://{host}:8006/api2/json"
        self.session = requests.Session()
        self.session.verify = False
        self.token_id = None
        self.token_secret = None
        self.ticket = None
    
    def load_token(self) -> bool:
        """Load API token from node's token file"""
        try:
            # Get token file from node
            result = subprocess.run([
                'scp', '-q', '-o', 'ConnectTimeout=5', 
                '-o', 'StrictHostKeyChecking=no',
                f'root@{self.host}:/etc/proxmox-cluster-token',
                f'/tmp/node_{self.host}_token'
            ], capture_output=True)
            
            if result.returncode != 0:
                logging.warning(f"Could not retrieve token from {self.host}")
                return False
            
            # Parse token file
            with open(f'/tmp/node_{self.host}_token', 'r') as f:
                for line in f:
                    line = line.strip()
                    if line.startswith('TOKEN_ID='):
                        self.token_id = line.split('=', 1)[1]
                    elif line.startswith('TOKEN_SECRET='):
                        self.token_secret = line.split('=', 1)[1]
            
            # Clean up temp file
            subprocess.run(['rm', '-f', f'/tmp/node_{self.host}_token'])
            
            if not self.token_id or not self.token_secret:
                logging.warning(f"Invalid token format from {self.host}")
                return False
            
            # Set authentication header
            self.session.headers.update({
                'Authorization': f'PVEAPIToken={self.token_id}={self.token_secret}',
                'Content-Type': 'application/json'
            })
            
            return True
            
        except Exception as e:
            logging.error(f"Error loading token from {self.host}: {e}")
            return False
    
    def authenticate_root(self) -> bool:
        """Authenticate as root@pam using the known installation password"""
        try:
            # Use the root password set during installation (from Ansible vars)
            auth_data = {
                'username': 'root@pam',
                'password': 'proxmox123'  # Password from ansible vars/main.yml
            }
            
            response = self.session.post(f"{self.base_url}/access/ticket", json=auth_data)
            if response.status_code == 200:
                ticket_data = response.json()
                if 'data' in ticket_data:
                    self.ticket = ticket_data['data']['ticket']
                    csrf_token = ticket_data['data']['CSRFPreventionToken']
                    
                    # Set ticket-based authentication  
                    self.session.headers.update({
                        'Authorization': f'PVEAuthCookie={self.ticket}',
                        'CSRFPreventionToken': csrf_token,
                        'Content-Type': 'application/json'
                    })
                    return True
            
            logging.error(f"Authentication failed for root@pam on {self.host}: HTTP {response.status_code}")
            return False
            
        except Exception as e:
            logging.error(f"Error authenticating as root@pam on {self.host}: {e}")
            return False
    
    def get(self, endpoint: str) -> Optional[Dict]:
        """Make GET request to Proxmox API"""
        try:
            response = self.session.get(f"{self.base_url}/{endpoint}", timeout=30)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logging.error(f"API GET {endpoint} failed for {self.host}: {e}")
            return None
    
    def post(self, endpoint: str, data: Dict) -> Optional[Dict]:
        """Make POST request to Proxmox API"""
        try:
            logging.debug(f"POST {endpoint} data: {data}")
            response = self.session.post(
                f"{self.base_url}/{endpoint}", 
                json=data, 
                timeout=30
            )
            
            # Log response for debugging
            logging.debug(f"POST {endpoint} response: {response.status_code}, {response.text[:500]}")
            
            response.raise_for_status()
            return response.json()
        except requests.exceptions.HTTPError as e:
            # Get detailed error response
            error_detail = ""
            if hasattr(e.response, 'text'):
                error_detail = e.response.text[:500]
            logging.error(f"API POST {endpoint} failed for {self.host}: {e}, Response: {error_detail}")
            return None
        except Exception as e:
            logging.error(f"API POST {endpoint} failed for {self.host}: {e}")
            return None

def check_node_status(node_name: str, node_config: Dict) -> str:
    """
    Check node status via API
    Returns: 'ready', 'clustered', or 'failed'
    """
    mgmt_ip = node_config['mgmt_ip']
    
    logging.info(f"Checking {node_name} ({mgmt_ip}) status via API...")
    
    # Create API client
    api = ProxmoxAPI(mgmt_ip)
    
    # Load authentication token
    if not api.load_token():
        logging.error(f"[FAIL] Cannot load API token for {node_name}")
        return 'failed'
    
    # Test basic API connectivity
    version_result = api.get('version')
    if not version_result or 'data' not in version_result:
        logging.error(f"[FAIL] Cannot connect to API on {node_name}")
        return 'failed'
    
    version = version_result['data'].get('version', 'unknown')
    logging.info(f"[OK] {node_name} API accessible (Proxmox {version})")
    
    # Check cluster status
    cluster_result = api.get('cluster/status')
    if not cluster_result or 'data' not in cluster_result:
        logging.warning(f"[WARN] Could not determine cluster status for {node_name}")
        return 'ready'
    
    # Look for cluster object in status
    cluster_objects = [item for item in cluster_result['data'] if item.get('type') == 'cluster']
    
    if cluster_objects:
        cluster_name = cluster_objects[0].get('name', 'unknown')
        logging.info(f"[OK] {node_name} is in cluster: {cluster_name}")
        return 'clustered'
    else:
        logging.info(f"[OK] {node_name} is not in a cluster (ready to join)")
        return 'ready'

def create_cluster(node_name: str, node_config: Dict) -> bool:
    """Create cluster on the specified node using root@pam authentication"""
    mgmt_ip = node_config['mgmt_ip']
    ceph_ip = node_config['ceph_ip']
    
    logging.info(f"Creating cluster '{CLUSTER_NAME}' on {node_name} via API with root@pam...")
    
    # Create API client and authenticate as root@pam
    api = ProxmoxAPI(mgmt_ip)
    
    if not api.authenticate_root():
        logging.error(f"[FAIL] Cannot authenticate as root@pam for {node_name}")
        return False
    
    # Create cluster
    cluster_data = {
        "clustername": CLUSTER_NAME,
        "link0": mgmt_ip,
        "link1": ceph_ip
    }
    
    result = api.post('cluster/config', cluster_data)
    
    if result and 'data' in result:
        logging.info(f"[OK] Cluster '{CLUSTER_NAME}' created successfully on {node_name}")
        return True
    elif result and 'errors' in result:
        error_msg = result.get('errors', 'Unknown error')
        if 'already exists' in str(error_msg).lower():
            logging.info(f"[OK] Cluster already exists on {node_name}")
            return True
        else:
            logging.error(f"[FAIL] Cluster creation failed: {error_msg}")
            return False
    else:
        logging.error(f"[FAIL] API call failed for cluster creation on {node_name}")
        return False

def join_cluster(node_name: str, node_config: Dict, primary_ip: str) -> bool:
    """Join node to existing cluster using root@pam authentication"""
    mgmt_ip = node_config['mgmt_ip']
    ceph_ip = node_config['ceph_ip']
    
    logging.info(f"Joining {node_name} to cluster via API with root@pam...")
    
    # Get join information from primary node using root@pam (with retry)
    primary_api = ProxmoxAPI(primary_ip)
    if not primary_api.authenticate_root():
        logging.error(f"[FAIL] Cannot authenticate as root@pam on primary node")
        return False
    
    # Retry getting join info (cluster might be syncing)
    join_info = None
    for attempt in range(3):
        join_info = primary_api.get('cluster/config/join')
        if join_info and 'data' in join_info:
            break
        if attempt < 2:
            logging.warning(f"Attempt {attempt + 1} to get join info failed, retrying in 10 seconds...")
            time.sleep(10)
    
    if not join_info or 'data' not in join_info:
        logging.error(f"[FAIL] Could not get join information from primary node after retries")
        return False
    
    # Create API client for joining node using root@pam
    node_api = ProxmoxAPI(mgmt_ip)
    if not node_api.authenticate_root():
        logging.error(f"[FAIL] Cannot authenticate as root@pam for {node_name}")
        return False
    
    # Extract fingerprint from nodelist
    nodelist = join_info['data'].get('nodelist', [])
    fingerprint = None
    if nodelist:
        fingerprint = nodelist[0].get('pve_fp')
    
    if not fingerprint:
        logging.error(f"[FAIL] Could not get fingerprint from primary node join info")
        return False
    
    # Join cluster with required parameters (including root password of primary node)
    join_data = {
        "hostname": primary_ip,
        "password": "proxmox123",  # Root password of primary node (from ansible vars)
        "fingerprint": fingerprint,
        "link0": mgmt_ip,
        "link1": ceph_ip
    }
    
    result = node_api.post('cluster/config/join', join_data)
    
    if result and 'data' in result:
        logging.info(f"[OK] {node_name} successfully joined cluster")
        return True
    elif result and 'errors' in result:
        logging.error(f"[FAIL] Join failed: {result.get('errors', 'Unknown error')}")
        return False
    else:
        logging.error(f"[FAIL] API call failed for joining {node_name}")
        return False

def verify_cluster(node_name: str, mgmt_ip: str) -> bool:
    """Verify cluster status using token authentication (sufficient for read operations)"""
    logging.info(f"Verifying cluster status on {node_name}...")
    
    api = ProxmoxAPI(mgmt_ip)
    if not api.load_token():
        logging.error(f"[FAIL] Cannot load API token for {node_name}")
        return False
    
    cluster_result = api.get('cluster/status')
    if not cluster_result or 'data' not in cluster_result:
        logging.error(f"[FAIL] Could not verify cluster status on {node_name}")
        return False
    
    # Count cluster members
    node_members = [item for item in cluster_result['data'] if item.get('type') == 'node']
    cluster_info = [item for item in cluster_result['data'] if item.get('type') == 'cluster']
    
    member_count = len(node_members)
    quorate = cluster_info[0].get('quorate', False) if cluster_info else False
    
    logging.info(f"[OK] Cluster status from {node_name}: {member_count} members, quorate: {quorate}")
    
    # List all members
    for member in node_members:
        node_name_member = member.get('name', 'unknown')
        node_ip = member.get('ip', 'unknown')
        level = member.get('level', 'unknown')
        logging.info(f"  Node: {node_name_member} ({node_ip}) - {level}")
    
    return True

def update_node_registration(node_name: str, node_config: Dict):
    """Update node registration status with provision server"""
    mgmt_ip = node_config['mgmt_ip']
    ceph_ip = node_config['ceph_ip']
    
    registration_data = {
        "hostname": node_name,
        "ip": mgmt_ip,
        "ceph_ip": ceph_ip,
        "type": "proxmox",
        "status": "clustered",
        "stage": "2-complete",
        "cluster_name": CLUSTER_NAME,
        "formation_method": "python-api"
    }
    
    try:
        response = requests.post(
            f"http://{PROVISION_SERVER}/api/register-node.php",
            json=registration_data,
            timeout=10
        )
        if response.status_code == 200:
            logging.info(f"Updated registration for {node_name}")
        else:
            logging.warning(f"Could not update registration for {node_name}")
    except Exception as e:
        logging.warning(f"Could not update registration for {node_name}: {e}")

def main():
    """Main cluster formation logic"""
    logging.info("=== Starting Python-Based Proxmox Cluster Formation ===")
    
    # Phase 1: Check all nodes are accessible
    logging.info("Phase 1: Checking node status via API...")
    
    ready_nodes = []
    clustered_nodes = []
    failed_nodes = []
    
    for node_name, node_config in NODES.items():
        status = check_node_status(node_name, node_config)
        if status == 'ready':
            ready_nodes.append(node_name)
        elif status == 'clustered':
            clustered_nodes.append(node_name)
        else:
            failed_nodes.append(node_name)
    
    if failed_nodes:
        logging.error(f"Failed nodes: {failed_nodes}")
        logging.error("Some nodes are not accessible. Please check and run post-install on failed nodes.")
        sys.exit(1)
    
    logging.info(f"Ready nodes: {ready_nodes}")
    logging.info(f"Already clustered: {clustered_nodes}")
    
    # Phase 2: Create cluster on node1 if needed
    node1_config = NODES['node1']
    
    if 'node1' not in clustered_nodes:
        logging.info("Phase 2: Creating cluster on node1...")
        
        if not create_cluster('node1', node1_config):
            logging.error("Failed to create cluster on node1")
            sys.exit(1)
        
        # Wait for cluster to stabilize
        logging.info("Waiting 10 seconds for cluster to stabilize...")
        time.sleep(10)
        
        # Verify cluster creation
        if not verify_cluster('node1', node1_config['mgmt_ip']):
            logging.error("Cluster creation verification failed")
            sys.exit(1)
    else:
        logging.info("Phase 2: Skipped - node1 already in cluster")
    
    # Phase 3: Join remaining nodes
    logging.info("Phase 3: Joining remaining nodes to cluster...")
    
    primary_ip = node1_config['mgmt_ip']
    join_failed = []
    
    for node_name in ready_nodes:
        if node_name == 'node1':
            continue  # Skip primary node
        
        node_config = NODES[node_name]
        
        logging.info(f"Joining {node_name} to cluster...")
        if join_cluster(node_name, node_config, primary_ip):
            logging.info(f"[OK] {node_name} successfully joined")
            time.sleep(15)  # Wait longer for cluster to stabilize and sync
        else:
            logging.error(f"[FAIL] Failed to join {node_name}")
            join_failed.append(node_name)
    
    # Phase 4: Final verification
    logging.info("Phase 4: Final cluster verification...")
    verify_cluster('node1', primary_ip)
    
    # Update node registration status
    logging.info("Updating node registration status...")
    for node_name, node_config in NODES.items():
        if node_name not in join_failed:
            update_node_registration(node_name, node_config)
    
    # Summary
    logging.info("=== Cluster Formation Complete ===")
    logging.info(f"Cluster Name: {CLUSTER_NAME}")
    logging.info(f"Formation Method: Python API (no SSH required)")
    
    successful_nodes = [name for name in NODES.keys() if name not in join_failed]
    logging.info(f"Successfully joined: {successful_nodes}")
    
    if join_failed:
        logging.warning(f"Failed nodes: {join_failed}")
        logging.warning("These nodes may need manual intervention")
    
    logging.info("Next steps:")
    logging.info(f"1. Access Proxmox web interface at https://{primary_ip}:8006")
    logging.info("2. Configure storage (local, Ceph, etc.)")
    logging.info("3. Create VMs and containers")
    logging.info("4. Set up monitoring and backups")
    
    logging.info("Python-based cluster formation completed successfully!")

if __name__ == "__main__":
    main()