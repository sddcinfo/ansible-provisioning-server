#!/usr/bin/env python3
"""
Coordinated Proxmox Reprovision Workflow
Orchestrates the complete reprovision process including monitoring and automatic cluster formation

FLOW:
1. Start enhanced-reprovision-monitor.py in background
2. Trigger reboot-nodes-for-reprovision.py --all (auto-confirms with 'y')
3. reboot-nodes-for-reprovision.py calls web interface for each MAC -> index.php?action=reprovision&mac=...
4. index.php calls update_registered_node_status() -> sets registered-nodes.json[hostname]['reprovision_status'] = 'in_progress'
5. Nodes reboot and install Proxmox, then run proxmox-post-install.sh
6. proxmox-post-install.sh calls register-node.php -> updates registered-nodes.json[hostname]['status'] = 'post-install-complete'  
7. enhanced-reprovision-monitor.py detects completed nodes and updates status to 'completed'
8. When ALL nodes complete, monitor triggers proxmox-form-cluster.py
9. Monitor marks nodes as 'clustered' when cluster formation succeeds
10. Coordinated script detects 'clustered' status and declares success
"""

import sys
import os
import subprocess
import threading
import time
import logging
import signal
import json
from pathlib import Path

# Add script directory to path for importing
script_dir = Path(__file__).parent.absolute()
sys.path.insert(0, str(script_dir))

# Configure logging
log_handlers = [logging.StreamHandler()]
try:
    log_handlers.append(logging.FileHandler('/var/log/coordinated-reprovision.log'))
except PermissionError:
    # Fall back to user home directory if no permission to /var/log
    log_file = os.path.expanduser('~/coordinated-reprovision.log')
    log_handlers.append(logging.FileHandler(log_file))

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=log_handlers
)

class CoordinatedReprovision:
    def __init__(self):
        self.script_dir = Path(__file__).parent.absolute()
        self.reboot_script = self.script_dir / "reboot-nodes-for-reprovision.py"
        self.monitor_script = self.script_dir / "enhanced-reprovision-monitor.py"
        self.nodes_file = '/var/www/html/data/registered-nodes.json'
        self.monitor_process = None
        self.stop_monitoring = False
        
    def validate_scripts(self):
        """Validate required scripts exist"""
        required_scripts = [
            self.reboot_script,
            self.monitor_script,
            self.script_dir / "proxmox-form-cluster.py"
        ]
        
        missing_scripts = []
        for script in required_scripts:
            if not script.exists():
                missing_scripts.append(str(script))
        
        if missing_scripts:
            logging.error(f"Missing required scripts: {missing_scripts}")
            return False
        
        return True
    
    def load_nodes_data(self):
        """Load current nodes data from registered-nodes.json"""
        try:
            if not os.path.exists(self.nodes_file):
                return {}
            with open(self.nodes_file, 'r') as f:
                return json.load(f)
        except Exception as e:
            logging.error(f"Failed to load nodes data: {e}")
            return {}
    
    def get_nodes_by_status(self, data, status):
        """Get nodes with specific reprovision_status"""
        nodes = {}
        for hostname, node_info in data.items():
            if isinstance(node_info, dict) and node_info.get('reprovision_status') == status:
                nodes[hostname] = node_info
        return nodes
    
    def log_status_summary(self, data):
        """Log current status summary of all nodes"""
        status_counts = {}
        total_nodes = 0
        
        for hostname, node_info in data.items():
            if isinstance(node_info, dict):
                total_nodes += 1
                # Check reprovision_status first, then fall back to regular status
                reprov_status = node_info.get('reprovision_status')
                if reprov_status:
                    status = reprov_status
                else:
                    # Node completed provisioning, check regular status  
                    reg_status = node_info.get('status', 'unknown')
                    if reg_status == 'post-install-complete':
                        status = 'provisioning-complete'
                    else:
                        status = reg_status
                        
                status_counts[status] = status_counts.get(status, 0) + 1
        
        if total_nodes > 0:
            status_str = ", ".join([f"{status}: {count}" for status, count in status_counts.items()])
            logging.info(f"Status Summary ({total_nodes} nodes): {status_str}")
        else:
            logging.info("No nodes found in registered-nodes.json")
    
    def start_monitor(self):
        """Start the reprovision monitor in background"""
        try:
            logging.info("Starting reprovision monitor...")
            self.monitor_process = subprocess.Popen([
                'python3', str(self.monitor_script)
            ], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            
            # Give monitor time to start
            time.sleep(2)
            
            if self.monitor_process.poll() is not None:
                # Process died immediately
                stdout, stderr = self.monitor_process.communicate()
                logging.error(f"Monitor failed to start: {stderr.decode()}")
                return False
            
            logging.info(f"Monitor started with PID {self.monitor_process.pid}")
            return True
            
        except Exception as e:
            logging.error(f"Failed to start monitor: {e}")
            return False
    
    def stop_monitor(self):
        """Stop the reprovision monitor"""
        if self.monitor_process and self.monitor_process.poll() is None:
            logging.info("Stopping reprovision monitor...")
            self.monitor_process.terminate()
            try:
                self.monitor_process.wait(timeout=10)
                logging.info("Monitor stopped gracefully")
            except subprocess.TimeoutExpired:
                logging.warning("Monitor didn't stop gracefully, killing...")
                self.monitor_process.kill()
                self.monitor_process.wait()
    
    def trigger_reprovision(self, node_filters=None):
        """Trigger reprovision using existing script"""
        try:
            # Check initial status before triggering
            initial_data = self.load_nodes_data()
            logging.info("=== PRE-REPROVISION STATUS ===")
            self.log_status_summary(initial_data)
            
            cmd = ['python3', str(self.reboot_script), '--all']
            if node_filters:
                # If specific nodes are provided, use --nodes instead of --all
                cmd = ['python3', str(self.reboot_script), '--nodes', ','.join(node_filters)]
                
            logging.info(f"🚀 Triggering reprovision: {' '.join(cmd)}")
            
            # Send "y" to confirm the action automatically
            result = subprocess.run(cmd, input='y\n', capture_output=True, text=True)
            
            if result.returncode == 0:
                logging.info("✅ Reprovision triggered successfully!")
                logging.info(f"Reprovision script output: {result.stdout}")
                
                # Wait a moment for status to update, then check
                time.sleep(3)
                
                updated_data = self.load_nodes_data()
                logging.info("=== POST-REPROVISION STATUS ===")
                self.log_status_summary(updated_data)
                
                # Show what changed
                reprovision_nodes = self.get_nodes_by_status(updated_data, 'in_progress')
                if reprovision_nodes:
                    logging.info(f"✅ Status updated: {len(reprovision_nodes)} nodes now marked 'in_progress':")
                    for hostname, node_info in reprovision_nodes.items():
                        ip = node_info.get('ip', 'unknown')
                        started = node_info.get('reprovision_started', 'unknown')
                        logging.info(f"  - {hostname} ({ip}) - started: {started}")
                else:
                    logging.warning("⚠️  No nodes found with 'in_progress' status after triggering reprovision")
                
                return True
            else:
                logging.error(f"❌ Reprovision failed: {result.stderr}")
                return False
                
        except Exception as e:
            logging.error(f"❌ Failed to trigger reprovision: {e}")
            return False
    
    def wait_for_completion(self, timeout_minutes=60):
        """Wait for reprovision to complete with timeout - actively monitor registered-nodes.json"""
        logging.info(f"Monitoring reprovision progress (timeout: {timeout_minutes} minutes)...")
        logging.info("Watching registered-nodes.json for status changes...")
        
        start_time = time.time()
        timeout_seconds = timeout_minutes * 60
        last_status_check = 0
        
        # Initial status check
        initial_data = self.load_nodes_data()
        logging.info("=== INITIAL STATUS ===")
        self.log_status_summary(initial_data)
        
        reprovision_nodes = self.get_nodes_by_status(initial_data, 'in_progress')
        if reprovision_nodes:
            logging.info(f"Found {len(reprovision_nodes)} nodes marked for reprovision:")
            for hostname, node_info in reprovision_nodes.items():
                logging.info(f"  - {hostname} ({node_info.get('ip')}) - started: {node_info.get('reprovision_started')}")
        else:
            logging.warning("No nodes found with 'in_progress' status. Checking for any reprovision activity...")
        
        while not self.stop_monitoring:
            current_time = time.time()
            elapsed = current_time - start_time
            
            # Check timeout
            if elapsed > timeout_seconds:
                logging.warning(f"Reprovision timed out after {timeout_minutes} minutes")
                return False
            
            # Check registered-nodes.json every 30 seconds
            if current_time - last_status_check >= 30:
                last_status_check = current_time
                
                try:
                    data = self.load_nodes_data()
                    
                    # Get nodes in different states
                    in_progress = self.get_nodes_by_status(data, 'in_progress')
                    completed = self.get_nodes_by_status(data, 'completed')
                    clustered = self.get_nodes_by_status(data, 'clustered')
                    timeout_nodes = self.get_nodes_by_status(data, 'timeout')
                    
                    # Also check for nodes that finished provisioning (post-install-complete)
                    provisioning_complete = {}
                    for hostname, node_info in data.items():
                        if isinstance(node_info, dict) and not node_info.get('reprovision_status'):
                            if node_info.get('status') == 'post-install-complete':
                                provisioning_complete[hostname] = node_info
                    
                    # Log status changes
                    logging.info(f"=== STATUS CHECK ({elapsed/60:.1f} min elapsed) ===")
                    self.log_status_summary(data)
                    
                    # Detail specific states
                    if in_progress:
                        logging.info(f"IN PROGRESS ({len(in_progress)}): {list(in_progress.keys())}")
                    
                    if provisioning_complete:
                        logging.info(f"PROVISIONING COMPLETE ({len(provisioning_complete)}): {list(provisioning_complete.keys())}")
                        for hostname, node_info in provisioning_complete.items():
                            registered_time = node_info.get('registered_at', 'unknown')
                            ip = node_info.get('ip', 'unknown')
                            logging.info(f"  - {hostname} ({ip}) finished provisioning at {registered_time}")
                    
                    if completed:
                        logging.info(f"MONITOR MARKED COMPLETED ({len(completed)}): {list(completed.keys())}")
                        for hostname, node_info in completed.items():
                            completed_time = node_info.get('reprovision_completed', 'unknown')
                            logging.info(f"  - {hostname} completed at {completed_time}")
                    
                    if clustered:
                        logging.info(f"CLUSTERED ({len(clustered)}): {list(clustered.keys())}")
                        # All nodes are clustered - success!
                        if not in_progress and not provisioning_complete:
                            logging.info("🎉 ALL NODES CLUSTERED - WORKFLOW COMPLETE! 🎉")
                            return True
                    
                    if timeout_nodes:
                        logging.warning(f"TIMED OUT ({len(timeout_nodes)}): {list(timeout_nodes.keys())}")
                    
                    # Check for completed reprovision but waiting for cluster formation
                    if (completed or provisioning_complete) and not in_progress:
                        total_ready = len(completed) + len(provisioning_complete)
                        logging.info(f"✅ All reprovisioning complete ({total_ready} nodes ready), waiting for cluster formation...")
                    
                    # Check if monitor process died
                    if self.monitor_process and self.monitor_process.poll() is not None:
                        stdout, stderr = self.monitor_process.communicate()
                        if "Cluster formation completed successfully" in stdout:
                            logging.info("✅ Monitor reports cluster formation completed!")
                            return True
                        else:
                            logging.error(f"❌ Monitor process died: {stderr}")
                            return False
                            
                except Exception as e:
                    logging.error(f"Error checking status: {e}")
            
            # Brief pause
            time.sleep(5)
            
            # Periodic summary (every 5 minutes)
            if int(elapsed) % 300 == 0 and elapsed > 0:
                logging.info(f"⏱️  Workflow still running... ({elapsed/60:.1f} minutes elapsed)")
        
        return False
    
    def cleanup(self):
        """Cleanup resources"""
        self.stop_monitoring = True
        self.stop_monitor()
    
    def signal_handler(self, signum, frame):
        """Handle interrupt signals"""
        logging.info(f"Received signal {signum}, cleaning up...")
        self.cleanup()
        sys.exit(0)
    
    def run_coordinated_workflow(self, node_filters=None, timeout_minutes=60):
        """Run the complete coordinated workflow"""
        # Setup signal handlers
        signal.signal(signal.SIGINT, self.signal_handler)
        signal.signal(signal.SIGTERM, self.signal_handler)
        
        try:
            logging.info("=== Starting Coordinated Proxmox Reprovision ===")
            
            # Validate all required scripts
            if not self.validate_scripts():
                logging.error("Script validation failed")
                return False
            
            # Start the monitor first
            if not self.start_monitor():
                logging.error("Failed to start monitor")
                return False
            
            # Wait a moment for monitor to initialize
            time.sleep(3)
            
            # Trigger the reprovision
            if not self.trigger_reprovision(node_filters):
                logging.error("Failed to trigger reprovision")
                self.cleanup()
                return False
            
            # Wait for completion
            success = self.wait_for_completion(timeout_minutes)
            
            if success:
                logging.info("=== Coordinated Reprovision Completed Successfully ===")
            else:
                logging.error("=== Coordinated Reprovision Failed or Timed Out ===")
            
            return success
            
        except Exception as e:
            logging.error(f"Unexpected error in coordinated workflow: {e}")
            return False
        finally:
            self.cleanup()

def print_usage():
    print("Coordinated Proxmox Reprovision Workflow")
    print("Usage: python3 coordinated-proxmox-reprovision.py [options] [node_filters...]")
    print()
    print("Options:")
    print("  --timeout MINUTES    Timeout in minutes (default: 60)")
    print("  --help              Show this help")
    print()
    print("Node filters are passed directly to reboot-nodes-for-reprovision.py")
    print("Examples:")
    print("  python3 coordinated-proxmox-reprovision.py")
    print("  python3 coordinated-proxmox-reprovision.py --timeout 90")
    print("  python3 coordinated-proxmox-reprovision.py node1 node2")

def main():
    if '--help' in sys.argv:
        print_usage()
        return
    
    # Parse arguments
    timeout_minutes = 60
    node_filters = []
    
    i = 1
    while i < len(sys.argv):
        arg = sys.argv[i]
        if arg == '--timeout':
            if i + 1 < len(sys.argv):
                try:
                    timeout_minutes = int(sys.argv[i + 1])
                    i += 2
                except ValueError:
                    print(f"Error: Invalid timeout value: {sys.argv[i + 1]}")
                    sys.exit(1)
            else:
                print("Error: --timeout requires a value")
                sys.exit(1)
        else:
            node_filters.append(arg)
            i += 1
    
    # Run the coordinated workflow
    coordinator = CoordinatedReprovision()
    success = coordinator.run_coordinated_workflow(
        node_filters=node_filters if node_filters else None,
        timeout_minutes=timeout_minutes
    )
    
    sys.exit(0 if success else 1)

if __name__ == '__main__':
    main()