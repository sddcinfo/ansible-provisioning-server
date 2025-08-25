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
    level=logging.INFO,  # Standard logging level
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
    """Create cluster on first node with smart verification"""
    for attempt in range(1, max_attempts + 1):
        logging.info(f"Creating cluster '{CLUSTER_NAME}' on {node_name} (attempt {attempt}/{max_attempts})...")
        
        api = SimpleProxmoxAPI(node_ip)
        if not api.authenticate():
            logging.error(f"Cannot authenticate to {node_name}")
            if attempt < max_attempts:
                time.sleep(5)  # Short wait for auth issues
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
            
            # Smart verification with progressive delays
            for verify_attempt in range(1, 4):  # Max 3 verification attempts
                delay = verify_attempt + 1  # 2s, 3s, 4s
                time.sleep(delay)
                
                status = check_node_status(node_name, node_ip)
                if status['in_cluster']:
                    logging.info(f"✓ Cluster creation verified on {node_name} (after {verify_attempt} attempts)")
                    return True
                
                logging.debug(f"Cluster verification attempt {verify_attempt}/3 for creation")
            
            logging.error(f"✗ Cluster creation not verified on {node_name}")
            if attempt < max_attempts:
                time.sleep(10)  # Only if we're retrying the whole creation
                continue
        else:
            logging.error(f"✗ Failed to create cluster on {node_name} (attempt {attempt})")
            if attempt < max_attempts:
                time.sleep(10)
    
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

def force_certificate_update(cluster_nodes: List[str]) -> bool:
    """Force certificate update on all cluster nodes using pvecm updatecerts -f"""
    if not cluster_nodes:
        return False
    
    logging.info("Forcing certificate synchronization across cluster nodes...")
    
    # Run updatecerts on the first available cluster node
    for node_name in cluster_nodes:
        if node_name in NODES:
            node_ip = NODES[node_name]['mgmt_ip']
            
            try:
                # SSH to node and run pvecm updatecerts -f
                import subprocess
                cmd = [
                    'ssh', '-o', 'ConnectTimeout=10', '-o', 'StrictHostKeyChecking=no',
                    f'root@{node_ip}',
                    'pvecm updatecerts -f'
                ]
                
                logging.info(f"Running certificate update on {node_name}...")
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
                
                if result.returncode == 0:
                    logging.info(f"✓ Certificate update completed on {node_name}")
                    # Allow time for certificates to propagate
                    time.sleep(5)
                    return True
                else:
                    logging.warning(f"Certificate update failed on {node_name}: {result.stderr}")
                    
            except subprocess.TimeoutExpired:
                logging.warning(f"Certificate update timeout on {node_name}")
            except Exception as e:
                logging.warning(f"Certificate update error on {node_name}: {e}")
                
    logging.warning("Unable to force certificate update on any node")
    return False

def get_consensus_fingerprint(cluster_nodes: List[str]) -> Optional[str]:
    """Get fingerprint consensus from all cluster nodes to handle certificate changes"""
    fingerprint_votes = {}
    successful_queries = 0
    
    for node_name in cluster_nodes:
        if node_name in NODES:
            node_ip = NODES[node_name]['mgmt_ip']
            status = check_node_status(node_name, node_ip)
            
            if status['in_cluster']:
                fingerprint = get_cluster_fingerprint(node_ip)
                if fingerprint:
                    fingerprint_votes[fingerprint] = fingerprint_votes.get(fingerprint, 0) + 1
                    successful_queries += 1
                    logging.debug(f"Node {node_name} reports fingerprint: {fingerprint[:20]}...")
    
    if not fingerprint_votes:
        logging.warning("No fingerprint votes collected from cluster nodes")
        return None
    
    # Get the most voted fingerprint
    consensus_fingerprint = max(fingerprint_votes.keys(), key=lambda k: fingerprint_votes[k])
    votes = fingerprint_votes[consensus_fingerprint]
    
    logging.info(f"Fingerprint consensus: {consensus_fingerprint[:20]}... ({votes}/{successful_queries} votes)")
    
    # Log any conflicts for debugging
    if len(fingerprint_votes) > 1:
        logging.warning(f"Fingerprint conflicts detected:")
        for fp, count in fingerprint_votes.items():
            logging.warning(f"  {fp[:20]}...: {count} votes")
        
        # If there are conflicts, try to force certificate update
        if len(cluster_nodes) > 1:
            logging.info("Attempting to resolve fingerprint conflicts with certificate update...")
            force_certificate_update(cluster_nodes)
    
    return consensus_fingerprint

def get_current_cluster_nodes() -> List[str]:
    """Get list of nodes currently in the cluster"""
    cluster_nodes = []
    
    for node_name, node_config in NODES.items():
        status = check_node_status(node_name, node_config['mgmt_ip'])
        if status['in_cluster']:
            cluster_nodes.append(node_name)
    
    return cluster_nodes


def wait_for_cluster_sync(cluster_nodes: List[str], timeout: int = 30) -> bool:
    """Wait for cluster nodes to synchronize after a join operation"""
    logging.info(f"Waiting for cluster synchronization across {len(cluster_nodes)} nodes...")
    
    start_time = time.time()
    sync_checks = 0
    
    while time.time() - start_time < timeout:
        # Check if all nodes have the same fingerprint
        fingerprint_consensus = get_consensus_fingerprint(cluster_nodes)
        
        if fingerprint_consensus:
            # Get fingerprint votes to check consistency
            fingerprint_votes = {}
            for node_name in cluster_nodes:
                if node_name in NODES:
                    node_ip = NODES[node_name]['mgmt_ip']
                    status = check_node_status(node_name, node_ip)
                    if status['in_cluster']:
                        fp = get_cluster_fingerprint(node_ip)
                        if fp:
                            fingerprint_votes[fp] = fingerprint_votes.get(fp, 0) + 1
            
            # Check if all nodes agree on fingerprint
            if len(fingerprint_votes) == 1:
                logging.info(f"✓ Cluster synchronized after {sync_checks + 1} checks")
                return True
            else:
                sync_checks += 1
                logging.debug(f"Sync check {sync_checks}: {len(fingerprint_votes)} different fingerprints")
        
        time.sleep(2)
    
    logging.warning(f"Cluster sync timeout after {timeout}s")
    return False

def monitor_task_completion(node_ip: str, task_id: str, timeout: int = 120) -> Tuple[bool, str]:
    """Monitor a Proxmox task with proper completion detection"""
    logging.info(f"Monitoring task: {task_id}")
    
    api = SimpleProxmoxAPI(node_ip)
    if not api.authenticate():
        return False, "Cannot authenticate to monitor task"
    
    start_time = time.time()
    last_status = None
    node_name = None
    check_count = 0
    
    # Extract node name from task_id for cluster checks
    try:
        parts = task_id.split(':')
        if len(parts) >= 2:
            node_name = parts[1]
    except Exception:
        pass
    
    while time.time() - start_time < timeout:
        check_count += 1
        task_status = api.get_task_status(task_id)
        elapsed = int(time.time() - start_time)
        
        if task_status and 'data' in task_status:
            data = task_status['data']
            status = data.get('status', 'unknown')
            exitstatus = data.get('exitstatus')
            
            # Log status changes and periodic updates
            if status != last_status or check_count % 10 == 0:
                logging.info(f"Task status: {status} (elapsed: {elapsed}s, check: {check_count})")
                last_status = status
            
            # Task completed successfully
            if status == 'stopped' and (exitstatus == 'OK' or exitstatus is None):
                logging.info(f"✓ Task completed successfully after {elapsed}s")
                api.close()
                return True, "Task completed successfully"
            
            # Task failed
            elif status == 'stopped' and exitstatus != 'OK':
                error_msg = f"Task failed with exit status: {exitstatus}"
                # Get detailed error from task log
                try:
                    task_log = api.get_task_log(task_id, limit=20)
                    if task_log:
                        # Look for actual error messages in log
                        error_lines = []
                        for entry in task_log[-10:]:
                            if 't' in entry:
                                line = entry['t'].strip()
                                if any(keyword in line.lower() for keyword in ['error', 'failed', 'timeout', 'invalid']):
                                    error_lines.append(line)
                        
                        if error_lines:
                            error_msg += f" Errors: {'; '.join(error_lines[-3:])}"
                except Exception:
                    pass
                
                logging.error(f"✗ {error_msg}")
                api.close()
                return False, error_msg
            
            # Task is running - check cluster status for early detection
            elif status == 'running':
                # After some time, check if join actually succeeded even if task still running
                if elapsed > 15 and node_name and node_name in NODES:
                    node_status = check_node_status(node_name, NODES[node_name]['mgmt_ip'])
                    if node_status['in_cluster']:
                        logging.info(f"✓ Node {node_name} successfully joined cluster (task still running)")
                        api.close()
                        return True, "Node joined cluster successfully"
        
        else:
            # Task might not exist or API call failed (common during cluster join due to cert changes)
            if check_count <= 5:
                logging.info(f"Task status temporarily unavailable (check {check_count}) - certificates may be updating")
            
            # After multiple failed checks, verify if join succeeded anyway
            if check_count > 5 and node_name and node_name in NODES:
                node_status = check_node_status(node_name, NODES[node_name]['mgmt_ip'])
                if node_status['in_cluster']:
                    logging.info(f"✓ Node {node_name} found in cluster despite task status issues")
                    api.close()
                    return True, "Node joined cluster (verified via cluster status)"
        
        # Adaptive sleep - start quick, then slower
        if check_count < 10:
            time.sleep(2)  # Quick checks first 20 seconds
        elif check_count < 20:
            time.sleep(3)  # Medium checks next 30 seconds  
        else:
            time.sleep(5)  # Slower checks after 50 seconds
    
    api.close()
    
    # Final verification before declaring timeout
    if node_name and node_name in NODES:
        node_status = check_node_status(node_name, NODES[node_name]['mgmt_ip'])
        if node_status['in_cluster']:
            logging.info(f"✓ Node {node_name} successfully joined (discovered after timeout)")
            return True, "Node joined cluster successfully"
    
    return False, f"Task monitoring timeout after {timeout}s (no completion detected)"


def get_actual_ssl_fingerprint(joining_node_ip: str, cluster_master_ip: str) -> Optional[str]:
    """Get the actual SSL fingerprint that the joining node sees from the cluster master"""
    try:
        import subprocess
        cmd = [
            'ssh', '-o', 'ConnectTimeout=10', '-o', 'StrictHostKeyChecking=no',
            f'root@{joining_node_ip}',
            f'openssl s_client -connect {cluster_master_ip}:8006 -servername {cluster_master_ip} 2>/dev/null | openssl x509 -fingerprint -sha256 -noout'
        ]
        
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        
        if result.returncode == 0:
            output = result.stdout.strip()
            # Parse "sha256 Fingerprint=XX:XX:XX..." format
            if 'Fingerprint=' in output:
                fingerprint = output.split('Fingerprint=')[1].strip()
                if fingerprint and len(fingerprint) > 40:
                    logging.info(f"Got actual SSL fingerprint from {joining_node_ip} -> {cluster_master_ip}: {fingerprint[:40]}...")
                    return fingerprint
        
        logging.warning(f"Failed to get SSL fingerprint from {joining_node_ip} -> {cluster_master_ip}: {result.stderr}")
        return None
        
    except subprocess.TimeoutExpired:
        logging.warning(f"Timeout getting SSL fingerprint from {joining_node_ip}")
        return None
    except Exception as e:
        logging.warning(f"Error getting SSL fingerprint from {joining_node_ip}: {e}")
        return None

def get_cluster_join_fingerprint(node_ip: str) -> Optional[str]:
    """Get fingerprint from cluster join API endpoint - this should be the correct one"""
    try:
        api = SimpleProxmoxAPI(node_ip)
        if not api.authenticate():
            return None
        
        # Get join information from cluster master
        result = api.get('cluster/config/join')
        api.close()
        
        if result and 'data' in result:
            nodelist = result['data'].get('nodelist', [])
            if nodelist and len(nodelist) > 0:
                # Get fingerprint from first node in nodelist
                fingerprint = nodelist[0].get('pve_fp')
                if fingerprint:
                    logging.info(f"Got cluster join fingerprint from {node_ip}: {fingerprint[:40]}...")
                    return fingerprint
        
        logging.warning(f"Could not get join fingerprint from {node_ip}")
        return None
        
    except Exception as e:
        logging.warning(f"Error getting join fingerprint from {node_ip}: {e}")
        return None

def get_node_certificate_fingerprint(node_ip: str) -> Optional[str]:
    """Get the certificate fingerprint directly from the joining node using pvenode cert info"""
    try:
        import subprocess
        
        # First, let's see what the actual output looks like
        cmd = [
            'ssh', '-o', 'ConnectTimeout=10', '-o', 'StrictHostKeyChecking=no',
            f'root@{node_ip}',
            'pvenode cert info'
        ]
        
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        
        if result.returncode == 0:
            output = result.stdout.strip()
            
            # Parse the fingerprint from the output - need SSL certificate, not CA certificate
            lines = output.split('\n')
            fingerprints = []
            
            # Collect all fingerprints
            for line in lines:
                line = line.strip()
                if 'fingerprint' in line.lower() and '│' in line:
                    # Extract fingerprint from table format
                    parts = line.split('│')
                    if len(parts) >= 3:
                        fingerprint = parts[2].strip()
                        # Validate fingerprint format (should be hex with colons)
                        if fingerprint and len(fingerprint) > 40 and ':' in fingerprint:
                            fingerprints.append(fingerprint)
            
            # We want the SSL certificate fingerprint (usually the second one)
            if len(fingerprints) >= 2:
                ssl_fingerprint = fingerprints[1]  # Second fingerprint should be SSL cert
                logging.info(f"Got SSL fingerprint from cert info: {ssl_fingerprint[:40]}...")
                return ssl_fingerprint
            elif len(fingerprints) == 1:
                logging.info(f"Got fingerprint from cert info: {fingerprints[0][:40]}...")
                return fingerprints[0]
            
            # If we can't parse it, show the full output for debugging
            logging.warning(f"Could not parse fingerprint from {node_ip}. Full output:\n{output}")
        else:
            logging.warning(f"pvenode cert info failed on {node_ip}: {result.stderr}")
        
        return None
        
    except subprocess.TimeoutExpired:
        logging.warning(f"Timeout getting fingerprint from {node_ip}")
        return None
    except Exception as e:
        logging.warning(f"Error getting fingerprint from {node_ip}: {e}")
        return None

def join_node_to_cluster_single_attempt(node_name: str, primary_ip: str, task_timeout: int = 120) -> Tuple[bool, str]:
    """Join a node to the cluster using API with fresh fingerprint from CLUSTER master node"""
    node_config = NODES[node_name]
    mgmt_ip = node_config['mgmt_ip']
    ceph_ip = node_config['ceph_ip']
    
    # Connect to node to join using API first
    api = SimpleProxmoxAPI(mgmt_ip)
    if not api.authenticate():
        return False, f"Cannot authenticate to {node_name}"
    
    # Get the ACTUAL SSL fingerprint that the joining node sees from cluster master
    logging.info(f"Getting ACTUAL SSL fingerprint that {node_name} sees from cluster master ({primary_ip})...")
    fingerprint = get_actual_ssl_fingerprint(mgmt_ip, primary_ip)
    
    if not fingerprint:
        logging.info(f"Fallback: Getting fingerprint from CLUSTER MASTER join API ({primary_ip})...")
        fingerprint = get_cluster_join_fingerprint(primary_ip)
    
    if not fingerprint:
        logging.info(f"Final fallback: Getting certificate fingerprint from CLUSTER MASTER node ({primary_ip})...")
        fingerprint = get_node_certificate_fingerprint(primary_ip)
    
    if not fingerprint:
        api.close()
        return False, f"Cannot get fingerprint from cluster master {primary_ip}"
    
    logging.info(f"Using fresh cluster master fingerprint: {fingerprint[:40]}...")
    
    # Prepare join data with the FRESH cluster master's fingerprint
    join_data = {
        "hostname": primary_ip,
        "password": ROOT_PASSWORD,
        "fingerprint": fingerprint,
        "link0": mgmt_ip,
        "link1": ceph_ip
    }
    
    # Send join request via API
    logging.info(f"Sending API join request to {node_name}...")
    
    try:
        result = api.post('cluster/config/join', join_data, timeout=120)
        api.close()
        
        if result:
            if 'data' in result and 'UPID:' in str(result['data']):
                task_id = result['data']
                logging.info(f"✓ Join task started on {node_name}: {task_id}")
                
                # Monitor task completion
                success, message = monitor_task_completion(mgmt_ip, task_id, timeout=task_timeout)
                
                if success:
                    # Verify node is actually in cluster
                    success_result, verify_message = verify_node_in_cluster(node_name, mgmt_ip)
                    
                    if success_result:
                        return True, verify_message
                    else:
                        return False, f"Join task completed but verification failed: {verify_message}"
                else:
                    return False, f"Join task failed: {message}"
            else:
                logging.error(f"Unexpected API response format: {result}")
                return False, f"Unexpected join response: {result}"
        else:
            logging.error("API call returned None - possible authentication or network issue")
            return False, "No response from join API call - check authentication and network connectivity"
            
    except Exception as e:
        api.close()
        logging.error(f"Exception during API call: {str(e)}")
        return False, f"API call failed: {str(e)}"

def verify_node_in_cluster(node_name: str, mgmt_ip: str, max_attempts: int = 5) -> Tuple[bool, str]:
    """Robust verification that node is actually in cluster with quick detection"""
    logging.info(f"Verifying {node_name} cluster membership...")
    
    for attempt in range(1, max_attempts + 1):
        # Quick initial check, then progressive delay
        if attempt == 1:
            time.sleep(1)  # Quick first check
        elif attempt == 2:
            time.sleep(2)  # Still quick
        else:
            time.sleep(3)  # Slower for remaining attempts
        
        status = check_node_status(node_name, mgmt_ip)
        if status['in_cluster']:
            logging.info(f"✓ {node_name} verified in cluster on attempt {attempt}")
            return True, "Successfully joined cluster"
        
        logging.debug(f"Cluster verification attempt {attempt}/{max_attempts} - not yet in cluster")
        
        # Get diagnostic info on final attempt
        if attempt == max_attempts:
            diagnostic_info = ""
            try:
                api_diag = SimpleProxmoxAPI(mgmt_ip)
                if api_diag.authenticate():
                    cluster_info = api_diag.get('cluster/status')
                    if cluster_info and 'data' in cluster_info:
                        cluster_nodes = []
                        for item in cluster_info['data']:
                            if item.get('type') == 'node':
                                cluster_nodes.append(item.get('name', 'unknown'))
                        diagnostic_info = f" (cluster nodes: {', '.join(cluster_nodes)})"
                    api_diag.close()
            except Exception as e:
                diagnostic_info = f" (diagnostic failed: {str(e)})"
            
            return False, f"Verification failed after {max_attempts} attempts{diagnostic_info}"
    
    return False, "Verification failed"

def join_node_to_cluster(node_name: str, primary_ip: str, max_attempts: int = 3, backoff_delay: int = 30, task_timeout: int = 120) -> bool:
    """Join a node to cluster with retry logic"""
    logging.info(f"Joining {node_name} to cluster (max {max_attempts} attempts)...")
    
    for attempt in range(1, max_attempts + 1):
        logging.info(f"--- Attempt {attempt}/{max_attempts} for {node_name} ---")
        
        success, message = join_node_to_cluster_single_attempt(node_name, primary_ip, task_timeout)
        
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
                
                # For fingerprint errors, just wait - don't force updates that change fingerprints
                error_category = get_error_category(message)
                if error_category in ["fingerprint_verification", "fingerprint_generic", "task_fingerprint"]:
                    logging.info(f"Fingerprint error detected, will retry with fresh fingerprint...")
                    # Don't run certificate update as it changes the fingerprint we need
                
                # Smart retry delay based on error type
                delay = calculate_retry_delay(message, backoff_delay, attempt)
                logging.info(f"Waiting {delay}s before retry (error type: {error_category})...")
                time.sleep(delay)
                
                backoff_delay = min(backoff_delay * 2, 180)  # Exponential backoff, max 3 minutes
            else:
                logging.error(f"✗ {node_name} failed to join after {max_attempts} attempts")
    
    return False

def get_error_category(message: str) -> str:
    """Categorize error messages for smart retry logic"""
    message_lower = message.lower()
    if "fingerprint" in message_lower:
        if "not verified" in message_lower:
            return "fingerprint_verification"
        return "fingerprint_generic"
    elif "authenticate" in message_lower:
        return "authentication"
    elif "task failed" in message_lower and "fingerprint" in message_lower:
        return "task_fingerprint"
    elif "task failed" in message_lower:
        return "task_execution"
    elif "verification failed" in message_lower:
        return "cluster_verification"
    else:
        return "generic"

def calculate_retry_delay(message: str, base_delay: int, attempt: int) -> int:
    """Calculate optimal retry delay based on error type and attempt number"""
    category = get_error_category(message)
    
    # Base delays for different error categories
    delay_multipliers = {
        "fingerprint_verification": 1.0,  # Reduced since we now use updatecerts
        "fingerprint_generic": 1.0,       # Reduced since we now use updatecerts
        "authentication": 0.3,            # Quick retry for auth issues
        "task_fingerprint": 1.0,          # Reduced since we now use updatecerts
        "task_execution": 1.0,            # Standard delay
        "cluster_verification": 0.5,      # Quick retry for verification
        "generic": 1.0
    }
    
    multiplier = delay_multipliers.get(category, 1.0)
    calculated_delay = int(base_delay * multiplier)
    
    # Minimum delays based on attempt number
    min_delay = max(3, attempt * 2)
    
    return max(calculated_delay, min_delay)

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

def wait_for_post_install_completion(timeout: int = 900, check_interval: int = 10) -> bool:
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

def verify_cluster_with_retry(max_attempts: int = 3) -> bool:
    """Smart cluster verification with progressive delays"""
    for attempt in range(1, max_attempts + 1):
        if attempt > 1:
            delay = attempt * 2  # 2s, 4s, 6s
            logging.info(f"Waiting {delay}s before verification attempt {attempt}...")
            time.sleep(delay)
        
        if verify_cluster():
            if attempt > 1:
                logging.info(f"✓ Cluster verified successfully on attempt {attempt}")
            return True
        
        if attempt < max_attempts:
            logging.debug(f"Cluster verification attempt {attempt}/{max_attempts} failed, retrying...")
    
    return False

def recovery_attempt(failed_nodes: List[str], primary_ip: str, max_attempts: int = 2, retry_delay: int = 60, task_timeout: int = 120) -> List[str]:
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
        if join_node_to_cluster(node_name, primary_ip, max_attempts, retry_delay, task_timeout):
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
  %(prog)s --wait-post-install --post-install-timeout 1200  # Wait up to 20 minutes
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
    parser.add_argument('--post-install-timeout', type=int, default=900,
                        help='Timeout in seconds for post-installation completion (default: 900)')
    parser.add_argument('--check-interval', type=int, default=10,
                        help='Interval in seconds between post-install checks (default: 10)')
    parser.add_argument('--max-join-attempts', type=int, default=3,
                        help='Maximum attempts to join each node to cluster (default: 3)')
    parser.add_argument('--join-retry-delay', type=int, default=30,
                        help='Initial delay between join retry attempts in seconds (default: 30)')
    parser.add_argument('--join-task-timeout', type=int, default=120,
                        help='Timeout for individual join task monitoring in seconds (default: 120)')
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
        # Brief pause only if needed for service stabilization
        logging.info("Allowing services to stabilize...")
        time.sleep(3)
    
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
                
                if not join_node_to_cluster(node_name, primary_ip, args.max_join_attempts, args.join_retry_delay, args.join_task_timeout):
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
        
        # Allow cluster to initialize with simple verification
        logging.info("Verifying cluster initialization...")
        cluster_ready = False
        for init_check in range(1, 4):  # Check 3 times with progressive delays
            time.sleep(init_check * 2)  # 2s, 4s, 6s
            
            # Check if primary node is accessible and in cluster
            status = check_node_status(primary_node, primary_ip)
            if status['in_cluster']:
                cluster_ready = True
                logging.info(f"✓ Cluster ready for node joins (after {init_check} checks)")
                break
            logging.debug(f"Cluster initialization check {init_check}/3")
        
        if not cluster_ready:
            logging.warning("Cluster may not be fully ready, but proceeding with joins")
        
        # Join remaining nodes
        remaining_nodes = [n for n in ready_nodes if n != primary_node]
        if remaining_nodes:
            logging.info(f"\nStep 3: Joining {len(remaining_nodes)} nodes to new cluster...")
            
            for i, node_name in enumerate(remaining_nodes, 1):
                logging.info(f"\n--- Node {i}/{len(remaining_nodes)}: {node_name} ---")
                
                if not join_node_to_cluster(node_name, primary_ip, args.max_join_attempts, args.join_retry_delay, args.join_task_timeout):
                    logging.error(f"Failed to join {node_name} after all retry attempts")
                    # Continue with other nodes even if one fails
    else:
        logging.error("No accessible nodes found")
        return False
    
    # Step 4: Verify final state with smart timing
    logging.info("\nFinalizing cluster formation...")
    success = verify_cluster_with_retry()
    
    # Step 5: Recovery attempt for failed nodes
    if not success and primary_ip:
        # Get list of failed nodes
        failed_nodes = []
        for node_name, node_config in NODES.items():
            status = check_node_status(node_name, node_config['mgmt_ip'])
            if not status['in_cluster']:
                failed_nodes.append(node_name)
        
        if failed_nodes:
            recovered_nodes = recovery_attempt(failed_nodes, primary_ip, 2, 60, args.join_task_timeout)
            if recovered_nodes:
                logging.info(f"\n=== Final Verification After Recovery ===")
                success = verify_cluster_with_retry()
    
    # Summary
    duration = datetime.now() - start_time
    logging.info(f"\nDuration: {duration}")
    logging.info(f"Log file: {LOG_FILE}")
    
    return success

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)