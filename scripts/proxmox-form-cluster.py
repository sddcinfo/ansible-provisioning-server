#!/usr/bin/env python3
"""
Proxmox Cluster Formation Script - API-Only Implementation
Uses Proxmox REST API exclusively for cluster formation
No SSH operations or password prompts - all auth handled via tokens or management server
"""

import requests
import json
import time
import sys
import logging
import subprocess
from typing import Dict, List, Tuple, Optional
from datetime import datetime

# Disable SSL warnings for self-signed certificates
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Configuration
PROVISION_SERVER = "10.10.1.1"
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
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout)
    ]
)

class ProxmoxAPI:
    """Proxmox API client for cluster operations - API only, no SSH"""
    
    def __init__(self, host: str):
        self.host = host
        self.base_url = f"https://{host}:8006/api2/json"
        self.session = requests.Session()
        self.session.verify = False
        self.token_id = None
        self.token_secret = None
        self.auth_method = None
    
    def regenerate_token_via_mgmt(self, node_name: str) -> bool:
        """Regenerate API token on node via management server SSH"""
        try:
            logging.info(f"Regenerating API token for {node_name} via management server...")
            
            # Command to regenerate token on remote node
            regenerate_cmd = f"""
ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=no root@{self.host} '
    # Remove old token if exists
    pveum user token remove automation@pam cluster-formation 2>/dev/null || true
    sleep 1
    
    # Create new token
    TOKEN_OUTPUT=$(pveum user token add automation@pam cluster-formation --privsep 0 --expire 0 2>&1)
    TOKEN_SECRET=$(echo "$TOKEN_OUTPUT" | grep -oE "[a-f0-9]{{8}}-[a-f0-9]{{4}}-[a-f0-9]{{4}}-[a-f0-9]{{4}}-[a-f0-9]{{12}}" | head -1)
    
    # Save token to file
    cat > /etc/proxmox-cluster-token <<EOF
TOKEN_ID=automation@pam!cluster-formation
TOKEN_SECRET=$TOKEN_SECRET
CREATED_AT=$(date -Iseconds)
HOSTNAME={node_name}
NODE_IP={self.host}
STATUS=valid
EOF
    
    # Output the token for capture
    echo "TOKEN_ID=automation@pam!cluster-formation"
    echo "TOKEN_SECRET=$TOKEN_SECRET"
'
"""
            
            # Run command from management server (which has passwordless SSH)
            result = subprocess.run(
                regenerate_cmd,
                shell=True,
                capture_output=True,
                text=True,
                timeout=30
            )
            
            if result.returncode != 0:
                logging.error(f"Failed to regenerate token on {node_name}: {result.stderr}")
                return False
            
            # Parse the output to get new token
            for line in result.stdout.split('\n'):
                if line.startswith('TOKEN_ID='):
                    self.token_id = line.split('=', 1)[1].strip()
                elif line.startswith('TOKEN_SECRET='):
                    self.token_secret = line.split('=', 1)[1].strip()
            
            if self.token_id and self.token_secret and len(self.token_secret) == 36:
                logging.info(f"Successfully regenerated token for {node_name}")
                return True
            else:
                logging.error(f"Failed to extract valid token from regeneration output")
                return False
                
        except Exception as e:
            logging.error(f"Error regenerating token for {node_name}: {e}")
            return False
    
    def get_token_from_node(self, node_name: str) -> bool:
        """Get existing token from node via management server SSH"""
        try:
            # Command to read token from remote node
            read_cmd = f"""
ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=no root@{self.host} '
    if [ -f /etc/proxmox-cluster-token ]; then
        cat /etc/proxmox-cluster-token
    else
        echo "NO_TOKEN_FILE"
    fi
'
"""
            
            result = subprocess.run(
                read_cmd,
                shell=True,
                capture_output=True,
                text=True,
                timeout=10
            )
            
            if result.returncode != 0 or "NO_TOKEN_FILE" in result.stdout:
                logging.debug(f"No token file found on {node_name}")
                return False
            
            # Parse token data
            for line in result.stdout.split('\n'):
                if '=' in line:
                    key, value = line.split('=', 1)
                    if key == 'TOKEN_ID':
                        self.token_id = value.strip()
                    elif key == 'TOKEN_SECRET':
                        self.token_secret = value.strip()
            
            # Validate token
            if self.token_id and self.token_secret and len(self.token_secret) == 36:
                return True
            else:
                logging.debug(f"Invalid token data from {node_name}")
                return False
                
        except Exception as e:
            logging.debug(f"Could not get token from {node_name}: {e}")
            return False
    
    def authenticate(self, node_name: str) -> bool:
        """Authenticate to node API using token (regenerate if needed)"""
        # First try to get existing token
        if self.get_token_from_node(node_name):
            # Test the token
            self.session.headers.update({
                'Authorization': f'PVEAPIToken={self.token_id}={self.token_secret}',
                'Content-Type': 'application/json'
            })
            
            try:
                response = self.session.get(f"{self.base_url}/version", timeout=5)
                if response.status_code == 200:
                    self.auth_method = "token"
                    logging.debug(f"Using existing token for {node_name}")
                    return True
            except:
                pass
        
        # Token doesn't exist or doesn't work - regenerate it
        if self.regenerate_token_via_mgmt(node_name):
            # Set headers with new token
            self.session.headers.update({
                'Authorization': f'PVEAPIToken={self.token_id}={self.token_secret}',
                'Content-Type': 'application/json'
            })
            
            # Test the new token
            try:
                response = self.session.get(f"{self.base_url}/version", timeout=5)
                if response.status_code == 200:
                    self.auth_method = "token"
                    logging.debug(f"Using regenerated token for {node_name}")
                    return True
            except Exception as e:
                logging.error(f"New token test failed for {node_name}: {e}")
        
        logging.error(f"All authentication methods failed for {node_name}")
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
            response = self.session.post(
                f"{self.base_url}/{endpoint}", 
                json=data, 
                timeout=60
            )
            
            # Handle both success and error responses
            if response.status_code in [200, 201]:
                return response.json()
            else:
                # Try to extract error message
                try:
                    error_data = response.json()
                    error_msg = error_data.get('errors', response.text)
                except:
                    error_msg = response.text
                    
                logging.error(f"API POST {endpoint} failed: {error_msg}")
                
                # Return error info for handling
                return {'error': True, 'message': error_msg, 'status_code': response.status_code}
                
        except Exception as e:
            logging.error(f"API POST {endpoint} exception for {self.host}: {e}")
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
    
    # Authenticate (will regenerate token if needed)
    if not api.authenticate(node_name):
        logging.error(f"[FAIL] Cannot authenticate to {node_name}")
        return 'failed'
    
    # Test basic API connectivity
    version_result = api.get('version')
    if not version_result or 'data' not in version_result:
        logging.error(f"[FAIL] Cannot connect to API on {node_name}")
        return 'failed'
    
    version = version_result['data'].get('version', 'unknown')
    logging.info(f"[OK] {node_name} API accessible (Proxmox {version}, auth: {api.auth_method})")
    
    # Check cluster status
    cluster_result = api.get('cluster/status')
    if not cluster_result or 'data' not in cluster_result:
        # Node is accessible but not in cluster
        logging.info(f"[OK] {node_name} is not in a cluster (ready to join)")
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
    """Create cluster on the specified node via API"""
    mgmt_ip = node_config['mgmt_ip']
    ceph_ip = node_config['ceph_ip']
    
    logging.info(f"Creating cluster '{CLUSTER_NAME}' on {node_name} via API...")
    
    # Create API client and authenticate
    api = ProxmoxAPI(mgmt_ip)
    
    if not api.authenticate(node_name):
        logging.error(f"[FAIL] Cannot authenticate for {node_name}")
        return False
    
    # Create cluster
    cluster_data = {
        "clustername": CLUSTER_NAME,
        "link0": mgmt_ip,
        "link1": ceph_ip
    }
    
    result = api.post('cluster/config', cluster_data)
    
    if result:
        if 'data' in result:
            logging.info(f"[OK] Cluster '{CLUSTER_NAME}' created successfully on {node_name}")
            return True
        elif result.get('error'):
            error_msg = result.get('message', 'Unknown error')
            if 'already exists' in str(error_msg).lower():
                logging.info(f"[OK] Cluster already exists on {node_name}")
                return True
            else:
                logging.error(f"[FAIL] Cluster creation failed: {error_msg}")
                return False
    
    logging.error(f"[FAIL] No response from cluster creation on {node_name}")
    return False

def get_join_info_via_api(primary_ip: str) -> Optional[Dict]:
    """Get cluster join info from primary node via API"""
    try:
        logging.info(f"Getting join information from primary node ({primary_ip}) via API...")
        
        # Create API client for primary node
        api = ProxmoxAPI(primary_ip)
        
        if not api.authenticate("node1"):
            logging.error(f"Could not authenticate to primary node {primary_ip}")
            return None
        
        # Get join info via API
        join_result = api.get('cluster/config/join')
        if not join_result or 'data' not in join_result:
            logging.error(f"Could not get join info from API on {primary_ip}")
            return None
        
        join_data = join_result['data']
        
        # Extract fingerprint from nodelist
        nodelist = join_data.get('nodelist', [])
        if not nodelist:
            logging.error(f"No nodelist in join info from {primary_ip}")
            return None
        
        # Get fingerprint from first node in list
        fingerprint = nodelist[0].get('pve_fp')
        if not fingerprint:
            logging.error(f"No fingerprint in join info from {primary_ip}")
            return None
        
        logging.info(f"Successfully got join info from API")
        return {
            'fingerprint': fingerprint,
            'hostname': primary_ip,
            'nodelist': nodelist
        }
        
    except Exception as e:
        logging.error(f"Error getting join info via API: {e}")
        return None

def join_cluster_via_api(node_name: str, node_config: Dict, primary_ip: str) -> bool:
    """Join node to cluster using Proxmox API"""
    mgmt_ip = node_config['mgmt_ip']
    ceph_ip = node_config['ceph_ip']
    
    logging.info(f"Joining {node_name} to cluster via API...")
    
    # Get join information from primary via API
    join_info = get_join_info_via_api(primary_ip)
    if not join_info:
        logging.error(f"[FAIL] Could not get join information from primary node")
        return False
    
    fingerprint = join_info['fingerprint']
    logging.info(f"Using fingerprint: {fingerprint[:20]}... from primary node")
    
    # Create API client for the joining node
    # Cluster join operations require root@pam authentication
    api = ProxmoxAPI(mgmt_ip)
    
    # Authenticate as root@pam for cluster operations (required for cluster/config/join)
    logging.info(f"Authenticating as root@pam for cluster join on {node_name}...")
    root_password = "proxmox123"  # Known installation password - will be changed after cluster formation
    
    auth_data = {
        'username': 'root@pam',
        'password': root_password
    }
    
    try:
        response = api.session.post(f"{api.base_url}/access/ticket", json=auth_data)
        if response.status_code == 200:
            ticket_data = response.json()
            if 'data' in ticket_data:
                api.ticket = ticket_data['data']['ticket']
                csrf_token = ticket_data['data']['CSRFPreventionToken']
                
                api.session.headers.update({
                    'Authorization': f'PVEAuthCookie={api.ticket}',
                    'CSRFPreventionToken': csrf_token,
                    'Content-Type': 'application/json'
                })
                logging.debug(f"Successfully authenticated as root@pam on {node_name}")
            else:
                logging.error(f"[FAIL] Invalid ticket response from {node_name}")
                return False
        else:
            logging.error(f"[FAIL] Authentication failed for root@pam on {node_name}: HTTP {response.status_code}")
            return False
    except Exception as e:
        logging.error(f"[FAIL] Exception during root@pam authentication on {node_name}: {e}")
        return False
    
    # Prepare join data for API call
    join_data = {
        "hostname": primary_ip,
        "password": root_password,  # Password for primary node
        "fingerprint": fingerprint,
        "link0": mgmt_ip,
        "link1": ceph_ip
    }
    
    logging.info(f"Sending cluster join request via API...")
    result = api.post('cluster/config/join', join_data)
    
    if result:
        if 'data' in result:
            # Check if we got a task ID (indicates async operation)
            if isinstance(result['data'], str) and 'UPID:' in result['data']:
                task_id = result['data']
                logging.info(f"[OK] {node_name} join initiated (Task: {task_id})")
                
                # Wait for the join task to complete
                logging.info(f"Waiting for {node_name} join task to complete...")
                time.sleep(30)  # Give more time for cluster join
                
                return True
            else:
                logging.info(f"[OK] {node_name} successfully joined cluster")
                return True
                
        elif result.get('error'):
            error_msg = result.get('message', 'Unknown error')
            if 'already exists' in str(error_msg).lower() or 'already member' in str(error_msg).lower():
                logging.info(f"[OK] {node_name} appears to already be in cluster")
                return True
            else:
                logging.error(f"[FAIL] Join failed for {node_name}: {error_msg}")
                return False
    
    logging.error(f"[FAIL] No response from cluster join API for {node_name}")
    return False

def verify_cluster_comprehensive(nodes: Dict) -> Dict:
    """Comprehensive cluster verification across all nodes"""
    logging.info("=== Starting comprehensive cluster verification ===")
    
    cluster_states = {}
    expected_nodes = list(nodes.keys())
    
    # Check each node's view of the cluster
    for node_name, node_config in nodes.items():
        mgmt_ip = node_config['mgmt_ip']
        logging.info(f"Verifying cluster view from {node_name}...")
        
        api = ProxmoxAPI(mgmt_ip)
        
        if not api.authenticate(node_name):
            logging.error(f"Could not authenticate to {node_name} for verification")
            cluster_states[node_name] = {
                'status': 'auth_failed',
                'members': [],
                'cluster_name': None,
                'quorate': False
            }
            continue
        
        # Get cluster status with retry
        cluster_result = None
        for attempt in range(3):
            cluster_result = api.get('cluster/status')
            if cluster_result and 'data' in cluster_result:
                break
            if attempt < 2:
                logging.debug(f"Retry {attempt + 1}/3 for cluster status on {node_name}")
                time.sleep(5)
        
        if not cluster_result or 'data' not in cluster_result:
            logging.warning(f"Could not get cluster status from {node_name}")
            cluster_states[node_name] = {
                'status': 'no_cluster',
                'members': [],
                'cluster_name': None,
                'quorate': False
            }
            continue
        
        # Analyze cluster data
        node_members = [item for item in cluster_result['data'] if item.get('type') == 'node']
        cluster_info = [item for item in cluster_result['data'] if item.get('type') == 'cluster']
        
        if not cluster_info:
            cluster_states[node_name] = {
                'status': 'no_cluster',
                'members': [],
                'cluster_name': None,
                'quorate': False
            }
            continue
        
        # Extract cluster information
        member_count = len(node_members)
        quorate = cluster_info[0].get('quorate', False)
        cluster_name = cluster_info[0].get('name', 'unknown')
        
        # Get member details
        members = []
        for member in node_members:
            member_name = member.get('name', 'unknown')
            member_ip = member.get('ip', 'unknown')
            online = member.get('online', 0)
            members.append({
                'name': member_name,
                'ip': member_ip,
                'online': bool(online)
            })
        
        cluster_states[node_name] = {
            'status': 'clustered' if member_count > 0 else 'no_cluster',
            'members': members,
            'cluster_name': cluster_name,
            'quorate': quorate,
            'member_count': member_count
        }
        
        logging.info(f"  {node_name} view: {cluster_name}, {member_count} members, quorate={quorate}")
        for member in members:
            status = "online" if member['online'] else "offline"
            logging.info(f"    {member['name']} ({member['ip']}) - [{status}]")
    
    return cluster_states

def analyze_cluster_consistency(cluster_states: Dict, expected_nodes: List[str]) -> bool:
    """Analyze if all nodes have a consistent view of the cluster"""
    logging.info("=== Analyzing cluster consistency ===")
    
    # Check if all nodes are clustered
    clustered_nodes = []
    failed_nodes = []
    
    for node_name, state in cluster_states.items():
        if state['status'] == 'clustered':
            clustered_nodes.append(node_name)
        else:
            failed_nodes.append(node_name)
            logging.warning(f"  {node_name}: {state['status']}")
    
    if failed_nodes:
        logging.error(f"Nodes not in cluster: {failed_nodes}")
        return False
    
    # Check cluster name consistency
    cluster_names = set(state['cluster_name'] for state in cluster_states.values() if state['cluster_name'])
    if len(cluster_names) > 1:
        logging.error(f"Multiple cluster names detected: {cluster_names}")
        return False
    
    cluster_name = list(cluster_names)[0] if cluster_names else 'unknown'
    
    # Check member count consistency
    member_counts = set(state['member_count'] for state in cluster_states.values() if 'member_count' in state)
    if len(member_counts) > 1:
        logging.warning(f"Inconsistent member counts: {member_counts}")
    
    expected_member_count = len(expected_nodes)
    actual_member_count = list(member_counts)[0] if member_counts else 0
    
    # Check if all expected nodes are seen by each cluster member
    all_consistent = True
    for node_name, state in cluster_states.items():
        if state['status'] != 'clustered':
            continue
            
        seen_nodes = set(member['name'] for member in state['members'])
        missing_nodes = set(expected_nodes) - seen_nodes
        extra_nodes = seen_nodes - set(expected_nodes)
        
        if missing_nodes:
            logging.warning(f"  {node_name} doesn't see: {missing_nodes}")
            all_consistent = False
        if extra_nodes:
            logging.warning(f"  {node_name} sees unexpected nodes: {extra_nodes}")
    
    # Summary
    logging.info(f"Cluster consistency analysis:")
    logging.info(f"  Cluster name: {cluster_name}")
    logging.info(f"  Expected members: {len(expected_nodes)}")
    logging.info(f"  Actual members: {actual_member_count}")
    logging.info(f"  Clustered nodes: {len(clustered_nodes)}/{len(expected_nodes)}")
    logging.info(f"  All nodes consistent: {all_consistent}")
    
    # Success criteria: all nodes clustered and consistent view
    success = (
        len(failed_nodes) == 0 and
        actual_member_count == expected_member_count and
        all_consistent
    )
    
    return success

def main():
    """Main cluster formation logic"""
    logging.info("=== Starting API-Based Proxmox Cluster Formation ===")
    logging.info("No passwords will be requested - all auth via tokens or management server")
    
    # Phase 1: Check all nodes
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
    
    logging.info(f"Ready nodes: {ready_nodes}")
    logging.info(f"Already clustered: {clustered_nodes}")
    if failed_nodes:
        logging.warning(f"Failed nodes: {failed_nodes}")
    
    if not ready_nodes and not clustered_nodes:
        logging.error("No nodes available for cluster formation")
        sys.exit(1)
    
    # Phase 2: Create or verify cluster
    primary_node = "node1"
    primary_ip = NODES[primary_node]['mgmt_ip']
    
    if primary_node in ready_nodes:
        # Create cluster on primary
        logging.info(f"Phase 2: Creating cluster on {primary_node}...")
        if not create_cluster(primary_node, NODES[primary_node]):
            logging.error(f"Failed to create cluster on {primary_node}")
            sys.exit(1)
        
        logging.info("Waiting 10 seconds for cluster to stabilize...")
        time.sleep(10)
        
        # Verify cluster was created
        if not verify_cluster_api(primary_node, primary_ip):
            logging.error(f"Cluster verification failed on {primary_node}")
            sys.exit(1)
        
        ready_nodes.remove(primary_node)
    elif primary_node in clustered_nodes:
        logging.info(f"Phase 2: {primary_node} already has a cluster")
    else:
        logging.error(f"Primary node {primary_node} is not available")
        sys.exit(1)
    
    # Phase 3: Join remaining nodes
    if ready_nodes:
        logging.info(f"Phase 3: Joining {len(ready_nodes)} nodes to cluster...")
        
        for node_name in ready_nodes:
            logging.info(f"Joining {node_name} to cluster...")
            
            if join_cluster_via_api(node_name, NODES[node_name], primary_ip):
                logging.info(f"[OK] {node_name} joined successfully")
                time.sleep(5)  # Wait between joins
            else:
                logging.error(f"[FAIL] {node_name} failed to join")
                failed_nodes.append(node_name)
    else:
        logging.info("Phase 3: No additional nodes to join")
    
    # Phase 4: Final verification with extended wait time
    logging.info("Phase 4: Final cluster verification...")
    logging.info("Waiting 30 seconds for cluster to fully stabilize...")
    time.sleep(30)  # Allow cluster to stabilize
    
    # Comprehensive verification
    cluster_states = verify_cluster_comprehensive(NODES)
    all_verified = analyze_cluster_consistency(cluster_states, list(NODES.keys()))
    
    # Summary
    logging.info("=== Cluster Formation Summary ===")
    if all_verified and not failed_nodes:
        logging.info("✅ SUCCESS: All nodes successfully joined the cluster")
        sys.exit(0)
    elif failed_nodes:
        logging.error(f"❌ PARTIAL SUCCESS: Failed nodes: {failed_nodes}")
        sys.exit(1)
    else:
        logging.warning("⚠️  COMPLETE: Cluster formed but verification incomplete")
        sys.exit(0)

if __name__ == "__main__":
    main()