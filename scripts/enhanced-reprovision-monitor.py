#!/usr/bin/env python3
"""
Enhanced Reprovision Monitor
Monitors registered-nodes.json for reprovision completion and automatically triggers cluster formation
"""

import json
import time
import sys
import os
import subprocess
import logging
from datetime import datetime
from typing import Dict, List, Set

# Configure logging
log_handlers = [logging.StreamHandler()]
try:
    log_handlers.append(logging.FileHandler('/var/log/reprovision-monitor.log'))
except PermissionError:
    # Fall back to user home directory if no permission to /var/log
    log_file = os.path.expanduser('~/reprovision-monitor.log')
    log_handlers.append(logging.FileHandler(log_file))

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=log_handlers
)

class ReprovisionMonitor:
    def __init__(self, nodes_file='/var/www/html/data/registered-nodes.json'):
        self.nodes_file = nodes_file
        self.script_dir = os.path.dirname(os.path.abspath(__file__))
        self.cluster_script = os.path.join(self.script_dir, 'proxmox-form-cluster.py')
        self.check_interval = 30  # seconds
        self.timeout_minutes = 45  # timeout for individual node reprovision
        
    def load_nodes(self) -> Dict:
        """Load nodes data from JSON file"""
        try:
            with open(self.nodes_file, 'r') as f:
                return json.load(f)
        except Exception as e:
            logging.error(f"Failed to load nodes file: {e}")
            return {}
    
    def get_reprovisioning_nodes(self, data: Dict) -> Dict[str, Dict]:
        """Get nodes currently being reprovisioned"""
        reprovisioning = {}
        # Work with hostname-based structure (backward compatible)
        for hostname, node_info in data.items():
            if isinstance(node_info, dict) and node_info.get('reprovision_status') == 'in_progress':
                reprovisioning[hostname] = node_info
        return reprovisioning
    
    def get_completed_nodes(self, data: Dict) -> Dict[str, Dict]:
        """Get nodes that completed reprovision"""
        completed = {}
        # Work with hostname-based structure (backward compatible)  
        for hostname, node_info in data.items():
            if isinstance(node_info, dict) and node_info.get('reprovision_status') == 'completed':
                completed[hostname] = node_info
        return completed
    
    def check_node_accessibility(self, ip: str) -> bool:
        """Check if node is accessible via SSH"""
        try:
            result = subprocess.run([
                'ssh', '-o', 'ConnectTimeout=10', 
                '-o', 'StrictHostKeyChecking=no',
                f'root@{ip}', 'echo "test"'
            ], capture_output=True, timeout=15)
            return result.returncode == 0
        except Exception:
            return False
    
    def check_proxmox_ready(self, ip: str) -> bool:
        """Check if Proxmox services are ready"""
        try:
            result = subprocess.run([
                'ssh', '-o', 'ConnectTimeout=10',
                '-o', 'StrictHostKeyChecking=no', 
                f'root@{ip}',
                'systemctl is-active pve-cluster && systemctl is-active pvedaemon'
            ], capture_output=True, timeout=15)
            return result.returncode == 0
        except Exception:
            return False
    
    def update_node_status(self, identifier: str, status: str, additional_data: Dict = None):
        """Update node status in registered-nodes.json"""
        try:
            data = self.load_nodes()
            # Find by hostname or MAC in the existing structure
            target_key = None
            for key, node_info in data.items():
                if isinstance(node_info, dict):
                    if key == identifier or node_info.get('mac') == identifier or node_info.get('hostname') == identifier:
                        target_key = key
                        break
            
            if target_key:
                data[target_key]['reprovision_status'] = status
                data[target_key]['last_update'] = datetime.now().isoformat()
                
                if additional_data:
                    data[target_key].update(additional_data)
                
                with open(self.nodes_file, 'w') as f:
                    json.dump(data, f, indent=2)
                
                logging.info(f"Updated {identifier} status to {status}")
            else:
                logging.warning(f"Could not find node {identifier} to update")
        except Exception as e:
            logging.error(f"Failed to update status for {identifier}: {e}")
    
    def check_reprovision_timeout(self, node_info: Dict) -> bool:
        """Check if reprovision has timed out"""
        try:
            start_time = datetime.fromisoformat(node_info.get('reprovision_started', ''))
            elapsed = (datetime.now() - start_time).total_seconds() / 60
            return elapsed > self.timeout_minutes
        except:
            return False
    
    def trigger_cluster_formation(self, completed_nodes: Dict):
        """Trigger cluster formation for completed nodes"""
        try:
            node_ips = [info.get('ip') for info in completed_nodes.values() if info.get('ip')]
            if len(node_ips) < 1:
                logging.info(f"No nodes completed, cannot form cluster")
                return False
            
            logging.info(f"Triggering cluster formation for {len(node_ips)} nodes: {node_ips}")
            
            # Run cluster formation script
            result = subprocess.run([
                'python3', self.cluster_script
            ], capture_output=True, text=True)
            
            if result.returncode == 0:
                logging.info("Cluster formation triggered successfully")
                return True
            else:
                logging.error(f"Cluster formation failed: {result.stderr}")
                return False
                
        except Exception as e:
            logging.error(f"Failed to trigger cluster formation: {e}")
            return False
    
    def monitor_loop(self):
        """Main monitoring loop"""
        logging.info("Starting reprovision monitor...")
        
        while True:
            try:
                data = self.load_nodes()
                if not data:
                    time.sleep(self.check_interval)
                    continue
                
                reprovisioning_nodes = self.get_reprovisioning_nodes(data)
                completed_nodes = self.get_completed_nodes(data)
                
                # Check reprovisioning nodes for completion or timeout
                for hostname, node_info in reprovisioning_nodes.items():
                    ip = node_info.get('ip')
                    if not ip:
                        continue
                    
                    # Check for timeout
                    if self.check_reprovision_timeout(node_info):
                        logging.warning(f"Node {hostname} ({ip}) reprovision timed out")
                        self.update_node_status(hostname, 'timeout')
                        continue
                    
                    # Check if node is accessible and Proxmox is ready
                    if self.check_node_accessibility(ip):
                        logging.info(f"Node {hostname} ({ip}) is accessible, checking Proxmox status...")
                        if self.check_proxmox_ready(ip):
                            logging.info(f"Node {hostname} ({ip}) Proxmox is ready")
                            self.update_node_status(hostname, 'completed', {
                                'reprovision_completed': datetime.now().isoformat()
                            })
                        else:
                            logging.debug(f"Node {hostname} ({ip}) Proxmox services not ready yet")
                    else:
                        logging.debug(f"Node {hostname} ({ip}) still not accessible")
                
                # Check if we should trigger cluster formation
                completed_nodes = self.get_completed_nodes(data)  # Refresh after updates
                reprovisioning_nodes = self.get_reprovisioning_nodes(data)
                
                # Only trigger cluster formation when ALL nodes are complete (no nodes still in progress)
                if len(completed_nodes) > 0 and len(reprovisioning_nodes) == 0:
                    # Check if any completed nodes haven't been clustered yet
                    unclustered = {hostname: info for hostname, info in completed_nodes.items() 
                                 if info.get('reprovision_status') == 'completed'}
                    
                    if unclustered:
                        logging.info(f"All nodes completed reprovision. Found {len(unclustered)} nodes ready for clustering")
                        if self.trigger_cluster_formation(unclustered):
                            logging.info("Cluster formation completed successfully")
                            # Mark nodes as clustered
                            for hostname in unclustered.keys():
                                self.update_node_status(hostname, 'clustered', {
                                    'cluster_formed': datetime.now().isoformat()
                                })
                        else:
                            logging.error("Cluster formation failed, will retry next cycle")
                elif len(reprovisioning_nodes) > 0:
                    logging.debug(f"Waiting for {len(reprovisioning_nodes)} nodes to complete reprovision before clustering")
                
                time.sleep(self.check_interval)
                
            except KeyboardInterrupt:
                logging.info("Monitor stopped by user")
                break
            except Exception as e:
                logging.error(f"Error in monitor loop: {e}")
                time.sleep(self.check_interval)

def main():
    if len(sys.argv) > 1 and sys.argv[1] == '--help':
        print("Enhanced Reprovision Monitor")
        print("Usage: python3 enhanced-reprovision-monitor.py [nodes_file]")
        print("  nodes_file: Path to registered-nodes.json (default: /var/www/html/data/registered-nodes.json)")
        return
    
    nodes_file = sys.argv[1] if len(sys.argv) > 1 else '/var/www/html/data/registered-nodes.json'
    
    if not os.path.exists(nodes_file):
        logging.error(f"Nodes file not found: {nodes_file}")
        sys.exit(1)
    
    monitor = ReprovisionMonitor(nodes_file)
    monitor.monitor_loop()

if __name__ == '__main__':
    main()