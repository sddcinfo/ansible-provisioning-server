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
import argparse
import subprocess
from typing import Dict, List, Optional, Tuple
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
    
    def get_task_status(self, task_id: str) -> Optional[Dict]:
        """Get status of a running task by UPID"""
        # Extract node from UPID format: UPID:node:pid:starttime:type:id:user:status
        try:
            parts = task_id.split(':')
            if len(parts) >= 2:
                node = parts[1]
                endpoint = f"nodes/{node}/tasks/{task_id}/status"
                return self.get(endpoint)
        except Exception:
            pass
        return None
    
    def get_task_log(self, task_id: str, limit: int = 50) -> Optional[List[Dict]]:
        """Get log entries for a task"""
        try:
            parts = task_id.split(':')
            if len(parts) >= 2:
                node = parts[1]
                endpoint = f"nodes/{node}/tasks/{task_id}/log?limit={limit}"
                result = self.get(endpoint)
                if result and 'data' in result:
                    return result['data']
        except Exception:
            pass
        return None

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

def monitor_task_completion(node_ip: str, task_id: str, timeout: int = 180) -> Tuple[bool, str]:
    """Monitor a Proxmox task until completion"""
    logging.info(f"Monitoring task: {task_id}")
    
    api = SimpleProxmoxAPI(node_ip)
    if not api.authenticate():
        return False, "Cannot authenticate to monitor task"
    
    start_time = time.time()
    last_status = None
    
    while time.time() - start_time < timeout:
        task_status = api.get_task_status(task_id)
        
        if task_status and 'data' in task_status:
            data = task_status['data']
            status = data.get('status', 'unknown')
            exitstatus = data.get('exitstatus')
            
            # Log status changes
            if status != last_status:
                elapsed = int(time.time() - start_time)
                logging.info(f"Task status: {status} (elapsed: {elapsed}s)")
                last_status = status
            
            # Check completion
            if status == 'stopped':
                if exitstatus == 'OK' or exitstatus is None:
                    api.close()
                    return True, "Task completed successfully"
                else:
                    error_msg = f"Task failed with exit status: {exitstatus}"
                    # Try to get task log for more details
                    try:
                        task_log = api.get_task_log(task_id, limit=10)
                        if task_log:
                            # Get the last few log entries
                            log_msgs = [entry.get('t', '') for entry in task_log[-3:] if 't' in entry]
                            if log_msgs:
                                error_msg += f" Log: {'; '.join(log_msgs)}"
                    except Exception:
                        pass
                    
                    api.close()
                    return False, error_msg
        
        time.sleep(3)  # Check every 3 seconds
    
    api.close()
    return False, f"Task monitoring timeout after {timeout}s"

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
            
            # Monitor task completion instead of sleeping
            success, message = monitor_task_completion(mgmt_ip, task_id, timeout=180)
            
            if success:
                logging.info(f"✓ Join task completed: {message}")
                
                # Verify node is actually in cluster
                for retry in range(3):  # Quick retries for verification
                    time.sleep(5)  # Brief pause for cluster state propagation
                    status = check_node_status(node_name, mgmt_ip)
                    if status['in_cluster']:
                        logging.info(f"✓ {node_name} successfully joined cluster")
                        return True
                    logging.debug(f"Cluster verification attempt {retry + 1}/3 failed, retrying...")
                
                logging.error(f"✗ {node_name} task completed but cluster verification failed")
                
                # Try to get more diagnostic info
                try:
                    api_diag = SimpleProxmoxAPI(mgmt_ip)
                    if api_diag.authenticate():
                        cluster_info = api_diag.get('cluster/status')
                        if cluster_info:
                            logging.debug(f"Cluster status on {node_name}: {cluster_info}")
                        api_diag.close()
                except Exception as e:
                    logging.debug(f"Failed to get diagnostic info from {node_name}: {e}")
                
                return False
            else:
                logging.error(f"✗ Join task failed: {message}")
                return False
        else:
            logging.error(f"✗ Unexpected join response: {result}")
            return False
    
    logging.error(f"✗ Failed to join {node_name} to cluster - no response")
    return False

def check_post_install_completion(node_name: str, node_ip: str) -> Tuple[bool, str]:
    """Check if post-installation script completed successfully via SSH"""
    try:
        # Check for success/error message in log file
        cmd = [
            'ssh', '-o', 'ConnectTimeout=10', '-o', 'StrictHostKeyChecking=no',
            f'root@{node_ip}',
            "tail -n 50 /var/log/proxmox-post-install.log 2>/dev/null | grep -E 'SUCCESS:|ERROR:' | tail -1"
        ]
        
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        
        if result.returncode == 0:
            output = result.stdout.strip()
            if "SUCCESS: Post-installation script completed successfully!" in output:
                return True, "completed"
            elif "ERROR:" in output:
                return False, f"error: {output[:100]}"
        
        # Check if script is still running
        cmd_check = [
            'ssh', '-o', 'ConnectTimeout=10', '-o', 'StrictHostKeyChecking=no',
            f'root@{node_ip}',
            "pgrep -f proxmox-post-install.sh"
        ]
        
        result_check = subprocess.run(cmd_check, capture_output=True, text=True, timeout=15)
        
        if result_check.returncode == 0 and result_check.stdout.strip():
            return False, "running"
        else:
            return False, "not started"
                
    except subprocess.TimeoutExpired:
        return False, "connection timeout"
    except Exception as e:
        return False, f"connection failed: {str(e)}"

def wait_for_post_install_completion(timeout: int = 600, check_interval: int = 10) -> bool:
    """Wait for all nodes to complete post-installation"""
    logging.info("\n=== Waiting for Post-Installation Completion ===")
    logging.info(f"Monitoring /var/log/proxmox-post-install.log on all nodes...")
    logging.info(f"Timeout: {timeout}s, Check interval: {check_interval}s")
    
    start_time = time.time()
    completed_nodes = set()
    failed_nodes = set()
    
    while time.time() - start_time < timeout:
        all_completed = True
        status_summary = []
        
        for node_name, node_config in NODES.items():
            if node_name in completed_nodes:
                continue
                
            completed, status = check_post_install_completion(node_name, node_config['mgmt_ip'])
            
            if completed:
                if node_name not in completed_nodes:
                    logging.info(f"✓ {node_name}: Post-installation completed successfully")
                    completed_nodes.add(node_name)
            else:
                all_completed = False
                if "error" in status:
                    if node_name not in failed_nodes:
                        logging.error(f"✗ {node_name}: Post-installation failed - {status}")
                        failed_nodes.add(node_name)
                else:
                    status_summary.append(f"{node_name}: {status}")
        
        if all_completed:
            logging.info(f"\n✓ All {len(NODES)} nodes completed post-installation successfully!")
            return True
        
        if failed_nodes:
            logging.error(f"\n✗ {len(failed_nodes)} node(s) failed post-installation")
            return False
        
        # Show current status
        if status_summary:
            remaining = len(NODES) - len(completed_nodes)
            elapsed = int(time.time() - start_time)
            logging.info(f"[{elapsed}s] Waiting for {remaining} node(s): {', '.join(status_summary)}")
        
        time.sleep(check_interval)
    
    logging.error(f"\n✗ Timeout: Not all nodes completed post-installation within {timeout}s")
    logging.error(f"Completed: {completed_nodes}")
    logging.error(f"Failed: {failed_nodes}")
    logging.error(f"Incomplete: {set(NODES.keys()) - completed_nodes - failed_nodes}")
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
    # Parse command line arguments
    parser = argparse.ArgumentParser(
        description='Proxmox Cluster Formation Script',
        epilog='''
Examples:
  %(prog)s                                    # Form cluster immediately
  %(prog)s --wait-post-install               # Wait for post-install completion first
  %(prog)s --wait-post-install --post-install-timeout 900  # Wait up to 15 minutes
  
The script will SSH to each node and check /var/log/proxmox-post-install.log for:
- SUCCESS: Post-installation script completed successfully!
- ERROR: messages indicating failures
        ''',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument('--wait-post-install', action='store_true',
                        help='Wait for post-installation to complete on all nodes before proceeding')
    parser.add_argument('--post-install-timeout', type=int, default=600,
                        help='Timeout in seconds for post-installation completion (default: 600)')
    parser.add_argument('--check-interval', type=int, default=10,
                        help='Interval in seconds between post-install checks (default: 10)')
    args = parser.parse_args()
    
    start_time = datetime.now()
    
    logging.info("=== Proxmox Cluster Formation (Improved) ===")
    logging.info(f"Target: {len(NODES)} nodes")
    logging.info(f"Cluster: {CLUSTER_NAME}")
    
    # Optional: Wait for post-installation completion
    if args.wait_post_install:
        if not wait_for_post_install_completion(args.post_install_timeout, args.check_interval):
            logging.error("Post-installation did not complete successfully on all nodes")
            return False
        # Add a small delay after post-install completion
        logging.info("Waiting 10 seconds for services to stabilize...")
        time.sleep(10)
    
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
        
        # Brief pause for cluster to initialize
        logging.info("Waiting for cluster to initialize...")
        time.sleep(10)
        
        # Join remaining nodes
        remaining_nodes = [n for n in ready_nodes if n != primary_node]
        if remaining_nodes:
            logging.info(f"\nStep 3: Joining {len(remaining_nodes)} nodes to new cluster...")
            
            for i, node_name in enumerate(remaining_nodes, 1):
                logging.info(f"\n--- Node {i}/{len(remaining_nodes)}: {node_name} ---")
                
                if not join_node_to_cluster(node_name, primary_ip):
                    logging.error(f"Failed to join {node_name}")
                    # Continue with other nodes even if one fails
    else:
        logging.error("No accessible nodes found")
        return False
    
    # Step 4: Verify final state
    logging.info("\nFinalizing cluster formation...")
    time.sleep(5)  # Brief pause for cluster state to propagate
    success = verify_cluster()
    
    # Summary
    duration = datetime.now() - start_time
    logging.info(f"\nDuration: {duration}")
    logging.info(f"Log file: {LOG_FILE}")
    
    return success

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)