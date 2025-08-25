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

def create_cluster(node_name: str, node_ip: str, max_attempts: int = 2) -> bool:
    """Create cluster on first node with retry logic"""
    for attempt in range(1, max_attempts + 1):
        logging.info(f"Creating cluster '{CLUSTER_NAME}' on {node_name} (attempt {attempt}/{max_attempts})...")
        
        api = SimpleProxmoxAPI(node_ip)
        if not api.authenticate():
            logging.error(f"Cannot authenticate to {node_name}")
            if attempt < max_attempts:
                time.sleep(10)
                continue
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
            
            # Verify cluster was actually created
            time.sleep(5)
            status = check_node_status(node_name, node_ip)
            if status['in_cluster']:
                logging.info(f"✓ Cluster creation verified on {node_name}")
                return True
            else:
                logging.error(f"✗ Cluster creation not verified on {node_name}")
                if attempt < max_attempts:
                    time.sleep(15)
                    continue
        else:
            logging.error(f"✗ Failed to create cluster on {node_name} (attempt {attempt})")
            if attempt < max_attempts:
                time.sleep(15)
    
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
            # Log all available fingerprints for debugging
            for i, node_info in enumerate(nodelist):
                fp = node_info.get('pve_fp', 'N/A')
                name = node_info.get('name', 'unknown')
                logging.debug(f"Nodelist[{i}]: {name} -> {fp[:20] if fp != 'N/A' else fp}...")
            
            # Get fingerprint from first node in list
            fingerprint = nodelist[0].get('pve_fp')
            if fingerprint:
                logging.debug(f"Selected fingerprint from {nodelist[0].get('name', 'node0')}: {fingerprint}")
                return fingerprint
    
    return None

def wait_for_stable_fingerprint(primary_ip: str, timeout: int = 60) -> Optional[str]:
    """Wait for cluster fingerprint to stabilize after changes"""
    logging.info("Waiting for cluster certificate to stabilize...")
    
    stable_count = 0
    last_fingerprint = None
    fingerprint_history = []
    
    for i in range(timeout // 3):  # Check every 3 seconds instead of 2
        current_fingerprint = get_cluster_fingerprint(primary_ip)
        
        if current_fingerprint:
            fingerprint_history.append(current_fingerprint)
            # Keep only last 5 entries
            if len(fingerprint_history) > 5:
                fingerprint_history.pop(0)
            
            if current_fingerprint == last_fingerprint:
                stable_count += 1
                if stable_count >= 3:  # Require 3 consecutive stable checks (9 seconds)
                    logging.info(f"✓ Certificate stabilized: {current_fingerprint[:20]}...")
                    
                    # Double-check by getting it one more time
                    time.sleep(2)
                    verify_fingerprint = get_cluster_fingerprint(primary_ip)
                    if verify_fingerprint == current_fingerprint:
                        logging.info("✓ Certificate stability verified")
                        return current_fingerprint
                    else:
                        logging.warning("Certificate changed during verification, continuing to wait...")
                        stable_count = 0
                        last_fingerprint = verify_fingerprint
                        continue
            else:
                if last_fingerprint:
                    logging.debug(f"Certificate changed: {last_fingerprint[:20]} → {current_fingerprint[:20]}")
                stable_count = 0
                last_fingerprint = current_fingerprint
        else:
            logging.debug("No fingerprint returned, retrying...")
            stable_count = 0
        
        time.sleep(3)
    
    logging.warning("Certificate did not stabilize in time")
    if fingerprint_history:
        # Return the most recent fingerprint even if not fully stable
        logging.info(f"Using most recent fingerprint: {fingerprint_history[-1][:20]}...")
        return fingerprint_history[-1]
    
    return None

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

def get_fingerprint_from_joining_node(joining_node_ip: str, primary_ip: str) -> Optional[str]:
    """Get fingerprint as seen from the joining node's perspective"""
    try:
        api = SimpleProxmoxAPI(joining_node_ip)
        if not api.authenticate():
            return None
        
        # Query the primary node's fingerprint from the joining node
        result = api.get(f'cluster/config/join?node={primary_ip}')
        api.close()
        
        if result and 'data' in result:
            nodelist = result['data'].get('nodelist', [])
            for node_info in nodelist:
                if node_info.get('name') == primary_ip.split('.')[-1] or node_info.get('nodeid') == '1':
                    fp = node_info.get('pve_fp')
                    if fp:
                        return fp
        
        return None
    except Exception:
        return None

def join_node_to_cluster_single_attempt(node_name: str, primary_ip: str) -> Tuple[bool, str]:
    """Join a node to the cluster with improved fingerprint handling"""
    node_config = NODES[node_name]
    mgmt_ip = node_config['mgmt_ip']
    ceph_ip = node_config['ceph_ip']
    
    # Try multiple approaches to get the correct fingerprint
    fingerprint = None
    
    # Method 1: Get fingerprint from primary node
    primary_fingerprint = wait_for_stable_fingerprint(primary_ip)
    
    # Method 2: Get fingerprint from joining node's perspective
    joining_fingerprint = get_fingerprint_from_joining_node(mgmt_ip, primary_ip)
    
    # Choose the best fingerprint
    if primary_fingerprint and joining_fingerprint:
        if primary_fingerprint == joining_fingerprint:
            fingerprint = primary_fingerprint
            logging.info(f"✓ Fingerprints match from both perspectives: {fingerprint[:40]}...")
        else:
            # They differ - use the joining node's view as it's more likely correct
            fingerprint = joining_fingerprint
            logging.warning(f"Fingerprint mismatch detected:")
            logging.warning(f"  Primary view: {primary_fingerprint[:40]}...")
            logging.warning(f"  Joining view: {joining_fingerprint[:40]}...")
            logging.warning(f"  Using joining node's perspective")
    elif joining_fingerprint:
        fingerprint = joining_fingerprint
        logging.info(f"Using fingerprint from joining node: {fingerprint[:40]}...")
    elif primary_fingerprint:
        fingerprint = primary_fingerprint
        logging.info(f"Using fingerprint from primary node: {fingerprint[:40]}...")
    else:
        return False, "Cannot get fingerprint from either node"
    
    # Connect to node to join
    api = SimpleProxmoxAPI(mgmt_ip)
    if not api.authenticate():
        return False, f"Cannot authenticate to {node_name}"
    
    # Prepare join data
    join_data = {
        "hostname": primary_ip,
        "password": ROOT_PASSWORD,
        "fingerprint": fingerprint,
        "link0": mgmt_ip,
        "link1": ceph_ip
    }
    
    # Final fingerprint verification right before join
    logging.debug("Final fingerprint verification before join...")
    final_check_fingerprint = get_cluster_fingerprint(primary_ip)
    if final_check_fingerprint and final_check_fingerprint != fingerprint:
        logging.warning(f"Fingerprint changed at last moment: {fingerprint[:20]} → {final_check_fingerprint[:20]}")
        logging.warning("Updating to latest fingerprint")
        join_data["fingerprint"] = final_check_fingerprint
        fingerprint = final_check_fingerprint
    
    # Send join request
    logging.info(f"Sending join request with fingerprint: {fingerprint[:40]}...")
    result = api.post('cluster/config/join', join_data, timeout=120)
    api.close()
    
    if result:
        if 'data' in result and 'UPID:' in str(result['data']):
            task_id = result['data']
            logging.info(f"✓ Join task started on {node_name}: {task_id}")
            
            # Monitor task completion instead of sleeping
            success, message = monitor_task_completion(mgmt_ip, task_id, timeout=180)
            
            if success:
                # Verify node is actually in cluster
                for retry in range(3):  # Quick retries for verification
                    time.sleep(5)  # Brief pause for cluster state propagation
                    status = check_node_status(node_name, mgmt_ip)
                    if status['in_cluster']:
                        return True, "Successfully joined cluster"
                    logging.debug(f"Cluster verification attempt {retry + 1}/3 failed, retrying...")
                
                # Try to get more diagnostic info
                diagnostic_info = ""
                try:
                    api_diag = SimpleProxmoxAPI(mgmt_ip)
                    if api_diag.authenticate():
                        cluster_info = api_diag.get('cluster/status')
                        if cluster_info and 'data' in cluster_info:
                            cluster_nodes = [item.get('name', '') for item in cluster_info['data'] 
                                           if item.get('type') == 'node']
                            diagnostic_info = f" (cluster has nodes: {', '.join(cluster_nodes)})"
                        api_diag.close()
                except Exception:
                    pass
                
                return False, f"Task completed but cluster verification failed{diagnostic_info}"
            else:
                return False, f"Join task failed: {message}"
        else:
            return False, f"Unexpected join response: {result}"
    
    return False, "No response from join API call"

def join_node_to_cluster(node_name: str, primary_ip: str, max_attempts: int = 3, backoff_delay: int = 30) -> bool:
    """Join a node to cluster with retry logic"""
    logging.info(f"Joining {node_name} to cluster (max {max_attempts} attempts)...")
    
    for attempt in range(1, max_attempts + 1):
        logging.info(f"--- Attempt {attempt}/{max_attempts} for {node_name} ---")
        
        success, message = join_node_to_cluster_single_attempt(node_name, primary_ip)
        
        if success:
            logging.info(f"✓ {node_name} successfully joined cluster on attempt {attempt}")
            return True
        else:
            logging.error(f"✗ Attempt {attempt} failed: {message}")
            
            if attempt < max_attempts:
                # Check if node is somehow already in cluster (race condition)
                node_config = NODES[node_name]
                status = check_node_status(node_name, node_config['mgmt_ip'])
                if status['in_cluster']:
                    logging.info(f"✓ {node_name} is already in cluster (race condition)")
                    return True
                
                # Determine if we should retry based on error type
                if "fingerprint" in message.lower():
                    if "not verified" in message.lower():
                        logging.info(f"Fingerprint verification failed - waiting {backoff_delay + 10}s for certificate to fully stabilize...")
                        time.sleep(backoff_delay + 10)  # Extra time for fingerprint issues
                    else:
                        logging.info(f"Fingerprint issue - waiting {backoff_delay}s for certificate stabilization...")
                        time.sleep(backoff_delay)
                elif "authenticate" in message.lower():
                    logging.info(f"Authentication issue - waiting {backoff_delay//2}s and retrying...")
                    time.sleep(backoff_delay // 2)
                elif "task failed" in message.lower() and "fingerprint" in message.lower():
                    # Special case: task failed due to fingerprint - need longer wait
                    logging.info(f"Task failed with fingerprint error - waiting {backoff_delay + 15}s for certificate synchronization...")
                    time.sleep(backoff_delay + 15)
                elif "task failed" in message.lower():
                    logging.info(f"Task execution failed - waiting {backoff_delay}s before retry...")
                    time.sleep(backoff_delay)
                elif "verification failed" in message.lower():
                    logging.info(f"Verification failed - waiting {backoff_delay//3}s for cluster sync...")
                    time.sleep(backoff_delay // 3)
                else:
                    logging.info(f"Generic failure - waiting {backoff_delay}s before retry...")
                    time.sleep(backoff_delay)
                
                backoff_delay = min(backoff_delay * 2, 180)  # Exponential backoff, max 3 minutes
            else:
                logging.error(f"✗ {node_name} failed to join after {max_attempts} attempts")
    
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
    failed_nodes = []
    
    for node_name, node_config in NODES.items():
        status = check_node_status(node_name, node_config['mgmt_ip'])
        if status['in_cluster']:
            cluster_nodes.append(node_name)
            logging.info(f"✓ {node_name} is in cluster")
        else:
            failed_nodes.append(node_name)
            logging.error(f"✗ {node_name} is NOT in cluster")
    
    if len(cluster_nodes) == len(NODES):
        logging.info(f"\n✓ SUCCESS: All {len(NODES)} nodes are clustered!")
        return True
    else:
        logging.error(f"\n✗ FAILURE: Only {len(cluster_nodes)}/{len(NODES)} nodes are clustered")
        logging.info(f"Clustered nodes: {', '.join(cluster_nodes)}")
        logging.info(f"Failed nodes: {', '.join(failed_nodes)}")
        return False

def recovery_attempt(failed_nodes: List[str], primary_ip: str, max_attempts: int = 2, retry_delay: int = 60) -> List[str]:
    """Attempt to recover failed nodes with more aggressive retry logic"""
    if not failed_nodes:
        return []
    
    logging.info(f"\n=== Recovery Attempt for Failed Nodes ===")
    logging.info(f"Attempting recovery for: {', '.join(failed_nodes)}")
    
    recovered_nodes = []
    
    for node_name in failed_nodes:
        logging.info(f"\n--- Recovery attempt for {node_name} ---")
        
        # First check if node somehow got into cluster during other operations
        node_config = NODES[node_name]
        status = check_node_status(node_name, node_config['mgmt_ip'])
        if status['in_cluster']:
            logging.info(f"✓ {node_name} is now in cluster (recovered automatically)")
            recovered_nodes.append(node_name)
            continue
        
        # Try to join with more aggressive settings
        if join_node_to_cluster(node_name, primary_ip, max_attempts, retry_delay):
            logging.info(f"✓ {node_name} recovered successfully")
            recovered_nodes.append(node_name)
        else:
            logging.error(f"✗ {node_name} recovery failed")
    
    return recovered_nodes

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
  %(prog)s --max-join-attempts 5 --join-retry-delay 45     # More aggressive retry settings
  
The script includes robust retry logic:
- Each node join is attempted up to --max-join-attempts times (default: 3)
- Failed attempts wait with exponential backoff starting at --join-retry-delay seconds
- Different error types use optimized retry delays (auth vs fingerprint vs task failures)
- Final recovery attempt is made for any remaining failed nodes
- Race condition detection prevents duplicate joins

SSH monitoring checks /var/log/proxmox-post-install.log for:
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
    parser.add_argument('--max-join-attempts', type=int, default=3,
                        help='Maximum attempts to join each node to cluster (default: 3)')
    parser.add_argument('--join-retry-delay', type=int, default=30,
                        help='Initial delay between join retry attempts in seconds (default: 30)')
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
                
                if not join_node_to_cluster(node_name, primary_ip, args.max_join_attempts, args.join_retry_delay):
                    logging.error(f"Failed to join {node_name} after all retry attempts")
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
                
                if not join_node_to_cluster(node_name, primary_ip, args.max_join_attempts, args.join_retry_delay):
                    logging.error(f"Failed to join {node_name} after all retry attempts")
                    # Continue with other nodes even if one fails
    else:
        logging.error("No accessible nodes found")
        return False
    
    # Step 4: Verify final state
    logging.info("\nFinalizing cluster formation...")
    time.sleep(5)  # Brief pause for cluster state to propagate
    success = verify_cluster()
    
    # Step 5: Recovery attempt for failed nodes
    if not success and primary_ip:
        # Get list of failed nodes
        failed_nodes = []
        for node_name, node_config in NODES.items():
            status = check_node_status(node_name, node_config['mgmt_ip'])
            if not status['in_cluster']:
                failed_nodes.append(node_name)
        
        if failed_nodes:
            recovered_nodes = recovery_attempt(failed_nodes, primary_ip, 2, 60)
            if recovered_nodes:
                logging.info(f"\n=== Final Verification After Recovery ===")
                time.sleep(10)  # Allow time for cluster to stabilize
                success = verify_cluster()
    
    # Summary
    duration = datetime.now() - start_time
    logging.info(f"\nDuration: {duration}")
    logging.info(f"Log file: {LOG_FILE}")
    
    return success

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)