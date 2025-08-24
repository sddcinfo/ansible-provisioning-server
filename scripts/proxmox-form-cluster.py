#!/usr/bin/env python3
"""
Improved Proxmox Cluster Formation Script
Fixes fingerprint race conditions that occur when certificates update after node joins
"""

import requests
import json
import time
import sys
import logging
from typing import Dict, List, Optional
from datetime import datetime

# Disable SSL warnings
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Configuration
ROOT_PASSWORD = "proxmox123"
CLUSTER_NAME = "sddc-cluster"
LOG_FILE = "/tmp/proxmox-cluster-formation.log"

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
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_FILE)
    ]
)

class SimpleProxmoxAPI:
    """Simple Proxmox API client using root@pam auth"""
    
    def __init__(self, host: str):
        self.host = host
        self.base_url = f"https://{host}:8006/api2/json"
        self.session = requests.Session()
        self.session.verify = False
        self.authenticated = False
        self.auth_ticket = None
        self.csrf_token = None
        
    def authenticate(self) -> bool:
        """Authenticate using root@pam"""
        try:
            response = self.session.post(
                f"{self.base_url}/access/ticket",
                data={"username": "root@pam", "password": ROOT_PASSWORD},
                timeout=10
            )
            
            if response.status_code == 200:
                data = response.json()['data']
                self.auth_ticket = data['ticket']
                self.csrf_token = data['CSRFPreventionToken']
                self.session.cookies.set('PVEAuthCookie', self.auth_ticket)
                self.session.headers.update({'CSRFPreventionToken': self.csrf_token})
                self.authenticated = True
                return True
                
        except Exception as e:
            logging.debug(f"Auth failed for {self.host}: {e}")
            
        return False
    
    def get(self, endpoint: str, timeout: int = 30) -> Optional[Dict]:
        """GET request to API"""
        if not self.authenticated and not self.authenticate():
            return None
        
        try:
            response = self.session.get(f"{self.base_url}/{endpoint}", timeout=timeout)
            if response.status_code == 200:
                return response.json()
        except Exception as e:
            logging.debug(f"GET {endpoint} failed on {self.host}: {e}")
        
        return None
    
    def post(self, endpoint: str, data: Dict = None, timeout: int = 30) -> Optional[Dict]:
        """POST request to API"""
        if not self.authenticated and not self.authenticate():
            return None
        
        try:
            response = self.session.post(
                f"{self.base_url}/{endpoint}",
                data=data or {},
                timeout=timeout
            )
            if response.status_code in [200, 201]:
                return response.json()
        except Exception as e:
            logging.debug(f"POST {endpoint} failed on {self.host}: {e}")
        
        return None
    
    def close(self):
        """Close session"""
        self.session.close()

def check_node_status(node_name: str, node_ip: str) -> Dict:
    """Check if node is accessible and in cluster"""
    api = SimpleProxmoxAPI(node_ip)
    
    if not api.authenticate():
        return {"accessible": False, "in_cluster": False}
    
    # Check cluster status
    result = api.get('cluster/status')
    api.close()
    
    if result and 'data' in result:
        # Check if node is in a cluster
        for item in result['data']:
            if item.get('type') == 'cluster':
                return {"accessible": True, "in_cluster": True, "cluster_name": item.get('name')}
        
        return {"accessible": True, "in_cluster": False}
    
    return {"accessible": True, "in_cluster": False}

def create_cluster(node_name: str, node_ip: str) -> bool:
    """Create cluster on first node"""
    logging.info(f"Creating cluster '{CLUSTER_NAME}' on {node_name}...")
    
    api = SimpleProxmoxAPI(node_ip)
    if not api.authenticate():
        logging.error(f"Cannot authenticate to {node_name}")
        return False
    
    # Create cluster with both networks
    cluster_data = {
        "clustername": CLUSTER_NAME,
        "link0": node_ip,
        "link1": NODES[node_name]['ceph_ip']
    }
    
    result = api.post('cluster/config', cluster_data)
    api.close()
    
    if result:
        logging.info(f"✓ Cluster '{CLUSTER_NAME}' created on {node_name}")
        return True
    
    logging.error(f"✗ Failed to create cluster on {node_name}")
    return False

def get_cluster_fingerprint(node_ip: str) -> Optional[str]:
    """Get current cluster certificate fingerprint"""
    api = SimpleProxmoxAPI(node_ip)
    if not api.authenticate():
        return None
    
    result = api.get('cluster/config/join')
    api.close()
    
    if result and 'data' in result:
        nodelist = result['data'].get('nodelist', [])
        if nodelist and len(nodelist) > 0:
            # Get fingerprint from first node in list
            fingerprint = nodelist[0].get('pve_fp')
            if fingerprint:
                return fingerprint
    
    return None

def wait_for_stable_fingerprint(primary_ip: str, timeout: int = 30) -> Optional[str]:
    """Wait for cluster fingerprint to stabilize after changes"""
    logging.info("Waiting for cluster certificate to stabilize...")
    
    stable_count = 0
    last_fingerprint = None
    
    for i in range(timeout // 2):
        current_fingerprint = get_cluster_fingerprint(primary_ip)
        
        if current_fingerprint:
            if current_fingerprint == last_fingerprint:
                stable_count += 1
                if stable_count >= 2:  # Stable for 2 checks
                    logging.info(f"✓ Certificate stabilized: {current_fingerprint[:20]}...")
                    return current_fingerprint
            else:
                stable_count = 0
                last_fingerprint = current_fingerprint
                logging.debug(f"Certificate changed: {current_fingerprint[:20]}...")
        
        time.sleep(2)
    
    logging.warning("Certificate did not stabilize in time")
    return last_fingerprint

def join_node_to_cluster(node_name: str, primary_ip: str) -> bool:
    """Join a node to the cluster with improved fingerprint handling"""
    node_config = NODES[node_name]
    mgmt_ip = node_config['mgmt_ip']
    ceph_ip = node_config['ceph_ip']
    
    logging.info(f"Joining {node_name} to cluster...")
    
    # Wait for stable fingerprint
    fingerprint = wait_for_stable_fingerprint(primary_ip)
    if not fingerprint:
        logging.error(f"✗ Cannot get stable fingerprint for {node_name}")
        return False
    
    logging.info(f"Using fingerprint: {fingerprint[:40]}...")
    
    # Connect to node to join
    api = SimpleProxmoxAPI(mgmt_ip)
    if not api.authenticate():
        logging.error(f"✗ Cannot authenticate to {node_name}")
        return False
    
    # Prepare join data
    join_data = {
        "hostname": primary_ip,
        "password": ROOT_PASSWORD,
        "fingerprint": fingerprint,
        "link0": mgmt_ip,
        "link1": ceph_ip
    }
    
    # Send join request
    result = api.post('cluster/config/join', join_data, timeout=120)
    api.close()
    
    if result:
        if 'data' in result and 'UPID:' in str(result['data']):
            task_id = result['data']
            logging.info(f"✓ Join task started on {node_name}: {task_id}")
            
            # Wait for join to complete
            logging.info(f"Waiting for {node_name} to join (this may take 90 seconds)...")
            time.sleep(90)
            
            # Verify join
            status = check_node_status(node_name, mgmt_ip)
            if status['in_cluster']:
                logging.info(f"✓ {node_name} successfully joined cluster")
                return True
            else:
                logging.error(f"✗ {node_name} join verification failed")
                return False
        else:
            logging.info(f"Join response: {result}")
            return False
    
    logging.error(f"✗ Failed to join {node_name} to cluster")
    return False

def verify_cluster() -> bool:
    """Verify final cluster state"""
    logging.info("\n=== Verifying Cluster ===")
    
    cluster_nodes = []
    for node_name, node_config in NODES.items():
        status = check_node_status(node_name, node_config['mgmt_ip'])
        if status['in_cluster']:
            cluster_nodes.append(node_name)
            logging.info(f"✓ {node_name} is in cluster")
        else:
            logging.error(f"✗ {node_name} is NOT in cluster")
    
    if len(cluster_nodes) == len(NODES):
        logging.info(f"\n✓ SUCCESS: All {len(NODES)} nodes are clustered!")
        return True
    else:
        logging.error(f"\n✗ FAILURE: Only {len(cluster_nodes)}/{len(NODES)} nodes are clustered")
        return False

def main():
    """Main execution"""
    start_time = datetime.now()
    
    logging.info("=== Proxmox Cluster Formation (Improved) ===")
    logging.info(f"Target: {len(NODES)} nodes")
    logging.info(f"Cluster: {CLUSTER_NAME}")
    
    # Step 1: Check all nodes and look for existing cluster
    logging.info("\nStep 1: Checking node status...")
    ready_nodes = []
    clustered_nodes = []
    primary_node = None
    primary_ip = None
    
    # Always check node1 first as it should be the primary
    for node_name in ['node1', 'node2', 'node3', 'node4']:
        node_config = NODES[node_name]
        status = check_node_status(node_name, node_config['mgmt_ip'])
        
        if status['accessible']:
            if status['in_cluster']:
                logging.info(f"✓ {node_name} already in cluster '{status.get('cluster_name')}'")
                clustered_nodes.append(node_name)
                # Use first clustered node as primary for joins
                if not primary_node:
                    primary_node = node_name
                    primary_ip = node_config['mgmt_ip']
            else:
                logging.info(f"✓ {node_name} ready to join")
                ready_nodes.append(node_name)
        else:
            logging.error(f"✗ {node_name} not accessible")
    
    # Step 2: Decide action based on cluster state
    if clustered_nodes:
        # Existing cluster found - join ready nodes to it
        logging.info(f"\nStep 2: Found existing cluster with {len(clustered_nodes)} nodes")
        logging.info(f"Primary node: {primary_node} ({primary_ip})")
        
        if ready_nodes:
            logging.info(f"\nStep 3: Joining {len(ready_nodes)} nodes to existing cluster...")
            
            for i, node_name in enumerate(ready_nodes, 1):
                logging.info(f"\n--- Node {i}/{len(ready_nodes)}: {node_name} ---")
                
                if not join_node_to_cluster(node_name, primary_ip):
                    logging.error(f"Failed to join {node_name}")
                    # Continue with other nodes even if one fails
                
                # Wait between joins for certificate updates
                if i < len(ready_nodes):
                    logging.info("Pausing before next node...")
                    time.sleep(15)
        else:
            logging.info("All nodes already in cluster")
    
    elif ready_nodes:
        # No existing cluster - create new one
        if len(ready_nodes) < 2:
            logging.error("Need at least 2 nodes to form new cluster")
            return False
        
        # Always prefer node1 as primary if available
        if 'node1' in ready_nodes:
            primary_node = 'node1'
        else:
            primary_node = ready_nodes[0]
        
        primary_ip = NODES[primary_node]['mgmt_ip']
        
        logging.info(f"\nStep 2: Creating new cluster on {primary_node}...")
        if not create_cluster(primary_node, primary_ip):
            return False
        
        # Wait for cluster to initialize
        time.sleep(20)
        
        # Join remaining nodes
        remaining_nodes = [n for n in ready_nodes if n != primary_node]
        if remaining_nodes:
            logging.info(f"\nStep 3: Joining {len(remaining_nodes)} nodes to new cluster...")
            
            for i, node_name in enumerate(remaining_nodes, 1):
                logging.info(f"\n--- Node {i}/{len(remaining_nodes)}: {node_name} ---")
                
                if not join_node_to_cluster(node_name, primary_ip):
                    logging.error(f"Failed to join {node_name}")
                    # Continue with other nodes even if one fails
                
                # Wait between joins for certificate updates
                if i < len(remaining_nodes):
                    logging.info("Pausing before next node...")
                    time.sleep(15)
    else:
        logging.error("No accessible nodes found")
        return False
    
    # Step 4: Verify final state
    time.sleep(10)
    success = verify_cluster()
    
    # Summary
    duration = datetime.now() - start_time
    logging.info(f"\nDuration: {duration}")
    logging.info(f"Log file: {LOG_FILE}")
    
    return success

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)