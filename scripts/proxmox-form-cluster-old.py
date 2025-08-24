#!/usr/bin/env python3
"""
Simple Proxmox Cluster Formation Script
- Uses ONLY root@pam authentication (no token complexity)
- Steps through nodes one by one with verification
- Uses SSH ONLY from mgmt server for emergency operations
- Focuses on reliability over speed
"""

import requests
import json
import time
import sys
import logging
import subprocess
from typing import Dict, List, Optional
from datetime import datetime

# Disable SSL warnings
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Configuration
CLUSTER_NAME = "sddc-cluster"
ROOT_PASSWORD = "proxmox123"
LOG_FILE = "/tmp/proxmox-cluster-simple.log"

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

class SimpleProxmoxAPI:
    """Simple Proxmox API client - root@pam only, no tokens"""
    
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

def emergency_ssh_restart(node_ip: str, node_name: str) -> bool:
    """EMERGENCY ONLY: Restart PVE services via SSH from mgmt"""
    logging.warning(f"Emergency SSH restart for {node_name} - API is unresponsive")
    
    try:
        # Only use SSH from management server in emergencies
        ssh_cmd = f'ssh -o ConnectTimeout=10 -o StrictHostKeyChecking=no root@{node_ip} "systemctl restart pve-cluster.service && sleep 5 && systemctl restart pveproxy.service"'
        
        result = subprocess.run(ssh_cmd, shell=True, capture_output=True, text=True, timeout=30)
        if result.returncode == 0:
            logging.info(f"Emergency restart completed for {node_name}")
            time.sleep(15)  # Give services time to start
            return True
        else:
            logging.error(f"Emergency restart failed for {node_name}: {result.stderr}")
            return False
            
    except Exception as e:
        logging.error(f"Emergency restart exception for {node_name}: {e}")
        return False

def check_node_accessible(node_name: str, node_ip: str) -> str:
    """Check if node is accessible and determine its cluster status"""
    logging.info(f"Checking {node_name} ({node_ip})...")
    
    api = SimpleProxmoxAPI(node_ip)
    
    # Test basic connectivity
    if not api.authenticate():
        logging.error(f"[FAIL] Cannot authenticate to {node_name}")
        return 'failed'
    
    # Test API functionality
    version_result = api.get('version')
    if not version_result:
        logging.error(f"[FAIL] API not responding on {node_name}")
        return 'failed'
    
    version = version_result.get('data', {}).get('version', 'unknown')
    logging.info(f"[OK] {node_name} accessible (Proxmox {version})")
    
    # Check cluster status
    cluster_result = api.get('cluster/status')
    if not cluster_result or 'data' not in cluster_result:
        logging.info(f"[OK] {node_name} not in cluster (ready)")
        return 'ready'
    
    # Parse cluster data
    cluster_data = cluster_result['data']
    cluster_objects = [item for item in cluster_data if item.get('type') == 'cluster']
    
    if cluster_objects:
        cluster_name = cluster_objects[0].get('name', 'unknown')
        logging.info(f"[OK] {node_name} in cluster: {cluster_name}")
        return 'clustered'
    else:
        logging.info(f"[OK] {node_name} not in cluster (ready)")
        return 'ready'

def create_cluster_on_node(node_name: str, node_config: Dict) -> bool:
    """Create cluster on the primary node"""
    mgmt_ip = node_config['mgmt_ip']
    ceph_ip = node_config['ceph_ip']
    
    logging.info(f"Creating cluster '{CLUSTER_NAME}' on {node_name}...")
    
    api = SimpleProxmoxAPI(mgmt_ip)
    if not api.authenticate():
        logging.error(f"[FAIL] Cannot authenticate to {node_name}")
        return False
    
    cluster_data = {
        "clustername": CLUSTER_NAME,
        "link0": mgmt_ip,
        "link1": ceph_ip
    }
    
    result = api.post('cluster/config', cluster_data)
    if not result:
        logging.error(f"[FAIL] No response from cluster creation")
        return False
    
    if 'data' in result:
        logging.info(f"[SUCCESS] Cluster '{CLUSTER_NAME}' created on {node_name}")
        return True
    elif result.get('error'):
        error_msg = result.get('message', 'Unknown error')
        if 'already exists' in str(error_msg).lower():
            logging.info(f"[OK] Cluster already exists on {node_name}")
            return True
        else:
            logging.error(f"[FAIL] Cluster creation failed: {error_msg}")
            return False
    
    logging.error(f"[FAIL] Unexpected cluster creation response")
    return False

def verify_cluster_on_node(node_name: str, node_ip: str) -> bool:
    """Verify cluster exists and is functional on node"""
    logging.info(f"Verifying cluster on {node_name}...")
    
    api = SimpleProxmoxAPI(node_ip)
    if not api.authenticate():
        logging.error(f"Cluster verification failed - cannot authenticate to {node_name}")
        return False
    
    cluster_result = api.get('cluster/status')
    if not cluster_result or 'data' not in cluster_result:
        logging.error(f"Cluster verification failed - no cluster status from {node_name}")
        return False
    
    cluster_data = cluster_result['data']
    cluster_objects = [item for item in cluster_data if item.get('type') == 'cluster']
    
    if cluster_objects:
        cluster_name = cluster_objects[0].get('name', 'unknown')
        logging.info(f"[SUCCESS] Cluster '{cluster_name}' verified on {node_name}")
        return True
    else:
        logging.error(f"Cluster verification failed - no cluster found on {node_name}")
        return False

def wait_for_cluster_certificate_update(primary_ip: str, expected_node_count: int, max_attempts: int = 10) -> bool:
    """Wait for cluster certificates to update after a node join"""
    logging.info(f"Waiting for cluster to update certificates (expecting {expected_node_count} nodes)...")
    
    for attempt in range(max_attempts):
        api = SimpleProxmoxAPI(primary_ip)
        if not api.authenticate():
            logging.warning(f"Auth failed during certificate wait (attempt {attempt + 1})")
            time.sleep(3)
            continue
            
        cluster_result = api.get('cluster/status')
        if cluster_result and 'data' in cluster_result:
            nodes = [item for item in cluster_result['data'] if item.get('type') == 'node']
            current_count = len(nodes)
            
            if current_count == expected_node_count:
                logging.info(f"Cluster updated: now has {current_count} nodes")
                time.sleep(5)  # Brief wait for certificate propagation
                return True
            else:
                logging.debug(f"Cluster still updating: {current_count}/{expected_node_count} nodes (attempt {attempt + 1})")
                time.sleep(3)
        else:
            logging.warning(f"Could not get cluster status (attempt {attempt + 1})")
            time.sleep(3)
    
    logging.warning(f"Cluster may not have fully updated after {max_attempts} attempts")
    return False

def get_cluster_join_info_with_retry(primary_ip: str, requesting_node: str, expected_node_count: int) -> Optional[Dict]:
    """Get FRESH join information with retry to ensure certificates are current"""
    logging.info(f"Getting verified fresh join info from primary node for {requesting_node}...")
    
    # First, ensure cluster has updated to expected state
    if not wait_for_cluster_certificate_update(primary_ip, expected_node_count):
        logging.warning("Proceeding despite cluster state uncertainty")
    
    # Try multiple times to get consistent join info
    max_attempts = 3
    for attempt in range(max_attempts):
        logging.info(f"Attempt {attempt + 1}/{max_attempts} to get join info for {requesting_node}")
        
        # Create fresh API connection
        api = SimpleProxmoxAPI(primary_ip)
        if not api.authenticate():
            logging.error(f"Cannot authenticate to primary node (attempt {attempt + 1})")
            if attempt < max_attempts - 1:
                time.sleep(3)
                continue
            return None
        
        # Get current cluster status
        cluster_result = api.get('cluster/status')
        if cluster_result and 'data' in cluster_result:
            nodes = [item for item in cluster_result['data'] if item.get('type') == 'node']
            node_count = len(nodes)
            node_names = [n.get('name') for n in nodes]
            logging.info(f"Cluster currently has {node_count} nodes: {node_names}")
        
        # Get join information
        join_result = api.get('cluster/config/join')
        if not join_result or 'data' not in join_result:
            logging.error(f"Cannot get join info from primary node (attempt {attempt + 1})")
            if attempt < max_attempts - 1:
                time.sleep(3)
                continue
            return None
        
        join_data = join_result['data']
        nodelist = join_data.get('nodelist', [])
        
        if not nodelist:
            logging.error(f"No nodelist in join info (attempt {attempt + 1})")
            if attempt < max_attempts - 1:
                time.sleep(3)
                continue
            return None
        
        fingerprint = nodelist[0].get('pve_fp')
        if not fingerprint:
            logging.error(f"No fingerprint in join info (attempt {attempt + 1})")
            if attempt < max_attempts - 1:
                time.sleep(3)
                continue
            return None
        
        logging.info(f"Got join info for {requesting_node}:")
        logging.info(f"  Fingerprint: {fingerprint}")
        logging.info(f"  Nodelist count: {len(nodelist)}")
        
        return {'fingerprint': fingerprint, 'nodelist': nodelist, 'node_count': len(nodelist)}
    
    logging.error(f"Failed to get valid join info after {max_attempts} attempts")
    return None

def join_node_to_cluster(node_name: str, node_config: Dict, primary_ip: str, expected_node_count: int) -> bool:
    """Join a node to the cluster"""
    mgmt_ip = node_config['mgmt_ip']
    ceph_ip = node_config['ceph_ip']
    
    logging.info(f"Joining {node_name} to cluster...")
    
    # Get fresh join info with proper certificate handling
    join_info = get_cluster_join_info_with_retry(primary_ip, node_name, expected_node_count - 1)
    if not join_info:
        logging.error(f"[FAIL] Cannot get join info for {node_name}")
        return False
    
    fingerprint = join_info['fingerprint']
    logging.info(f"FULL fingerprint for {node_name}: {fingerprint}")
    logging.info(f"Using fingerprint: {fingerprint[:20]}...")
    
    # Authenticate to joining node
    api = SimpleProxmoxAPI(mgmt_ip)
    if not api.authenticate():
        logging.error(f"[FAIL] Cannot authenticate to {node_name}")
        return False
    
    # Submit join request
    join_data = {
        "hostname": primary_ip,
        "password": ROOT_PASSWORD,
        "fingerprint": fingerprint,
        "link0": mgmt_ip,
        "link1": ceph_ip
    }
    
    logging.info(f"Join data for {node_name}:")
    logging.info(f"  hostname: {join_data['hostname']}")
    logging.info(f"  fingerprint: {join_data['fingerprint']}")
    logging.info(f"  link0: {join_data['link0']}")
    logging.info(f"  link1: {join_data['link1']}")
    
    # Force fresh API session for join request to avoid stale data
    api.session.close()
    api.authenticated = False
    if not api.authenticate():
        logging.error(f"[FAIL] Cannot re-authenticate to {node_name} for join")
        return False
    
    logging.info(f"Sending join request to {node_name} with verified fresh session...")
    result = api.post('cluster/config/join', join_data, timeout=120)
    if not result:
        logging.error(f"[FAIL] No response from join request for {node_name}")
        return False
    
    if 'data' in result:
        if 'UPID:' in str(result['data']):
            task_id = result['data']
            logging.info(f"[OK] {node_name} join task started: {task_id}")
            logging.info(f"Waiting for {node_name} join to complete...")
            time.sleep(90)  # Give plenty of time for join
        else:
            logging.info(f"[OK] {node_name} join completed immediately")
            time.sleep(20)
        
        return True
        
    elif result.get('error'):
        error_msg = result.get('message', 'Unknown error')
        if 'already' in str(error_msg).lower():
            logging.info(f"[OK] {node_name} already in cluster")
            return True
        else:
            logging.error(f"[FAIL] {node_name} join failed: {error_msg}")
            return False
    
    logging.error(f"[FAIL] Unexpected join response for {node_name}")
    return False

def verify_node_in_cluster(node_name: str, node_ip: str, max_attempts: int = 5) -> bool:
    """Verify node successfully joined cluster"""
    logging.info(f"Verifying {node_name} cluster membership...")
    
    for attempt in range(max_attempts):
        api = SimpleProxmoxAPI(node_ip)
        
        if not api.authenticate():
            logging.warning(f"Auth failed for {node_name} (attempt {attempt + 1})")
            time.sleep(10)
            continue
        
        cluster_result = api.get('cluster/status')
        if not cluster_result or 'data' not in cluster_result:
            logging.warning(f"{node_name} no cluster status (attempt {attempt + 1})")
            time.sleep(10)
            continue
        
        cluster_data = cluster_result['data']
        cluster_objects = [item for item in cluster_data if item.get('type') == 'cluster']
        node_objects = [item for item in cluster_data if item.get('type') == 'node']
        
        if cluster_objects and node_objects:
            cluster_name = cluster_objects[0].get('name', 'unknown')
            node_names = [n.get('name') for n in node_objects]
            node_count = len(node_objects)
            
            if node_name.replace('console-', '') in node_names:
                logging.info(f"[SUCCESS] {node_name} in cluster '{cluster_name}' with {node_count} nodes")
                return True
        
        logging.warning(f"{node_name} not properly in cluster (attempt {attempt + 1})")
        time.sleep(15)
    
    logging.error(f"[FAIL] {node_name} verification failed after {max_attempts} attempts")
    return False

def main():
    """Simple main cluster formation logic"""
    logging.info("=== Simple Proxmox Cluster Formation ===")
    logging.info("Using root@pam authentication only - no token complexity")
    
    # Step 1: Check all nodes
    logging.info("Step 1: Checking all nodes...")
    
    ready_nodes = []
    clustered_nodes = []
    failed_nodes = []
    
    for node_name, node_config in NODES.items():
        status = check_node_accessible(node_name, node_config['mgmt_ip'])
        if status == 'ready':
            ready_nodes.append(node_name)
        elif status == 'clustered':
            clustered_nodes.append(node_name)
        else:
            failed_nodes.append(node_name)
    
    logging.info(f"Ready: {ready_nodes}")
    logging.info(f"Clustered: {clustered_nodes}")
    logging.info(f"Failed: {failed_nodes}")
    
    if not ready_nodes and not clustered_nodes:
        logging.error("No nodes available for cluster formation")
        sys.exit(1)
    
    # Step 2: Create or verify cluster
    primary_node = "node1"
    primary_ip = NODES[primary_node]['mgmt_ip']
    
    if primary_node in ready_nodes:
        logging.info(f"Step 2: Creating cluster on {primary_node}...")
        if not create_cluster_on_node(primary_node, NODES[primary_node]):
            logging.error(f"Failed to create cluster on {primary_node}")
            sys.exit(1)
        
        logging.info("Waiting for cluster to stabilize...")
        time.sleep(20)
        
        if not verify_cluster_on_node(primary_node, primary_ip):
            logging.error(f"Cluster verification failed on {primary_node}")
            sys.exit(1)
        
        ready_nodes.remove(primary_node)
        clustered_nodes.append(primary_node)
    
    elif primary_node in clustered_nodes:
        logging.info(f"Step 2: {primary_node} already has cluster")
    else:
        logging.error(f"Primary node {primary_node} not available")
        sys.exit(1)
    
    # Step 3: Join remaining nodes ONE BY ONE
    if ready_nodes:
        logging.info(f"Step 3: Joining {len(ready_nodes)} nodes to cluster...")
        
        for i, node_name in enumerate(ready_nodes):
            expected_nodes_after_join = len(clustered_nodes) + 1  # Current clustered + this node
            logging.info(f"--- Joining {node_name} (will be node #{expected_nodes_after_join}) ---")
            
            if not join_node_to_cluster(node_name, NODES[node_name], primary_ip, expected_nodes_after_join):
                logging.error(f"Failed to join {node_name}")
                failed_nodes.append(node_name)
                continue
            
            # CRITICAL: Verify each node before moving to next
            if not verify_node_in_cluster(node_name, NODES[node_name]['mgmt_ip']):
                logging.error(f"Failed to verify {node_name}")
                failed_nodes.append(node_name)
                continue
            
            logging.info(f"[SUCCESS] {node_name} successfully joined and verified")
            clustered_nodes.append(node_name)
            
            # Brief pause before next join (certificate update is handled in join logic)
            if i < len(ready_nodes) - 1:  # Not the last node
                logging.info(f"Brief pause before joining next node...")
                time.sleep(10)
    
    # Step 4: Final summary
    logging.info("=== Final Summary ===")
    logging.info(f"Successfully clustered: {clustered_nodes}")
    if failed_nodes:
        logging.error(f"Failed nodes: {failed_nodes}")
        sys.exit(1)
    else:
        logging.info("All nodes successfully joined cluster")
        sys.exit(0)

if __name__ == "__main__":
    main()