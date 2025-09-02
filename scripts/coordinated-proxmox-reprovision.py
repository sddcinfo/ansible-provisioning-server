#!/usr/bin/env python3
"""
Coordinated Proxmox Reprovision Workflow
Orchestrates the complete reprovision process including monitoring and automatic cluster formation

COMPLETE DEPLOYMENT FLOW:
1. Start enhanced-reprovision-monitor.py in background
2. Trigger reboot-nodes-for-reprovision.py --all (auto-confirms with 'y') 
3. reboot-nodes-for-reprovision.py calls web interface for each MAC -> index.php?action=reprovision&mac=...
4. index.php calls update_registered_node_status() -> sets registered-nodes.json[hostname]['reprovision_status'] = 'in_progress'
5. Nodes reboot and install Proxmox, then run proxmox-post-install.sh
6. proxmox-post-install.sh calls register-node.php -> updates registered-nodes.json[hostname]['status'] = 'post-install-complete'  
7. enhanced-reprovision-monitor.py detects completed nodes and updates status to 'completed'
8. When ALL nodes complete, monitor triggers proxmox-form-cluster.py
9. Monitor marks nodes as 'clustered' when cluster formation succeeds
10. Coordinated script detects 'clustered' status and runs proxmox-ceph-setup.py
11. After Ceph setup completes, runs template-manager.py --create-templates
12. Complete timing summary shows duration of each step and total time
13. Full Proxmox cluster with Ceph storage and VM templates ready for use!
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
    def __init__(self, verbose=False):
        self.script_dir = Path(__file__).parent.absolute()
        self.reboot_script = self.script_dir / "reboot-nodes-for-reprovision.py"
        self.monitor_script = self.script_dir / "enhanced-reprovision-monitor.py"
        self.cluster_script = self.script_dir / "proxmox-form-cluster.py"
        self.ceph_script = self.script_dir / "proxmox-ceph-setup.py"
        self.template_script = self.script_dir / "template-manager.py"
        self.nodes_file = '/var/www/html/data/registered-nodes.json'
        self.monitor_process = None
        self.stop_monitoring = False
        self.timing = {}  # Track timing for each step
        self.verbose = verbose
        
    def validate_scripts(self):
        """Validate required scripts exist"""
        required_scripts = [
            self.reboot_script,
            self.monitor_script,
            self.cluster_script,
            self.ceph_script,
            self.template_script
        ]
        
        missing_scripts = []
        for script in required_scripts:
            if not script.exists():
                missing_scripts.append(str(script))
        
        if missing_scripts:
            logging.error(f"Missing required scripts: {missing_scripts}")
            return False
        
        return True
    
    def initialize_nodes_data_from_config(self):
        """Initialize registered-nodes.json from nodes.json if it doesn't exist"""
        nodes_config_file = '/var/www/html/nodes.json'
        
        if os.path.exists(self.nodes_file):
            return  # File already exists, no need to initialize
            
        if not os.path.exists(nodes_config_file):
            logging.warning(f"Neither {self.nodes_file} nor {nodes_config_file} exists")
            return
            
        try:
            # Load nodes.json configuration
            with open(nodes_config_file, 'r') as f:
                config = json.load(f)
            
            # Create registered-nodes structure
            registered_nodes = {}
            
            if 'nodes' in config and isinstance(config['nodes'], list):
                for node in config['nodes']:
                    if isinstance(node, dict) and 'os_hostname' in node:
                        hostname = node['os_hostname']
                        registered_nodes[hostname] = {
                            'hostname': hostname,
                            'ip': node.get('os_ip', ''),
                            'mac': node.get('os_mac', ''),
                            'console_ip': node.get('console_ip', ''),
                            'console_mac': node.get('console_mac', ''),
                            'ceph_ip': node.get('ceph_ip', ''),
                            'status': 'pending_reprovision',
                            'registered_at': '',
                            'reprovision_status': None,
                            'reprovision_started': None,
                            'reprovision_completed': None
                        }
            
            if registered_nodes:
                try:
                    # Ensure directory exists
                    os.makedirs(os.path.dirname(self.nodes_file), exist_ok=True)
                    
                    # Write the initial registered-nodes.json
                    with open(self.nodes_file, 'w') as f:
                        json.dump(registered_nodes, f, indent=2)
                    
                    logging.info(f"Created initial {self.nodes_file} with {len(registered_nodes)} nodes from {nodes_config_file}")
                except PermissionError:
                    # Try with sudo if permission denied
                    import tempfile
                    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as tmp_f:
                        json.dump(registered_nodes, tmp_f, indent=2)
                        tmp_path = tmp_f.name
                    
                    try:
                        # Ensure directory exists with sudo
                        subprocess.run(['sudo', 'mkdir', '-p', os.path.dirname(self.nodes_file)], check=True)
                        # Copy temp file to destination with sudo
                        subprocess.run(['sudo', 'cp', tmp_path, self.nodes_file], check=True)
                        # Set proper permissions
                        subprocess.run(['sudo', 'chown', 'www-data:www-data', self.nodes_file], check=True)
                        subprocess.run(['sudo', 'chmod', '664', self.nodes_file], check=True)
                        
                        logging.info(f"Created initial {self.nodes_file} with {len(registered_nodes)} nodes from {nodes_config_file} (using sudo)")
                    except subprocess.CalledProcessError as e:
                        logging.error(f"Failed to create {self.nodes_file} even with sudo: {e}")
                    finally:
                        # Clean up temp file
                        try:
                            os.unlink(tmp_path)
                        except:
                            pass
            else:
                logging.warning(f"No valid nodes found in {nodes_config_file}")
                
        except Exception as e:
            logging.error(f"Failed to initialize nodes data from config: {e}")

    def load_nodes_data(self):
        """Load current nodes data from registered-nodes.json"""
        try:
            # Initialize from nodes.json if registered-nodes.json doesn't exist
            self.initialize_nodes_data_from_config()
            
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
    
    def start_timer(self, step_name):
        """Start timing a step"""
        self.timing[step_name] = {
            'start': time.time(),
            'end': None,
            'duration': None,
            'success': None
        }
        if self.verbose:
            logging.info(f"Started: {step_name}")
    
    def end_timer(self, step_name, success=True):
        """End timing a step"""
        if step_name in self.timing:
            self.timing[step_name]['end'] = time.time()
            self.timing[step_name]['success'] = success
            duration = self.timing[step_name]['end'] - self.timing[step_name]['start']
            self.timing[step_name]['duration'] = duration
            
            status = "SUCCESS" if success else "FAILED"
            logging.info(f"Completed: {step_name} - {self.format_duration(duration)} - {status}")
    
    def format_duration(self, seconds):
        """Format duration in human readable format"""
        if seconds < 60:
            return f"{seconds:.1f}s"
        elif seconds < 3600:
            return f"{seconds/60:.1f}m"
        else:
            return f"{seconds/3600:.1f}h {(seconds%3600)/60:.0f}m"
    
    def print_timing_summary(self):
        """Print comprehensive timing summary"""
        logging.info("=" * 60)
        logging.info("WORKFLOW TIMING SUMMARY")
        logging.info("=" * 60)
        
        total_duration = 0
        for step_name, timing_info in self.timing.items():
            if timing_info['duration']:
                duration = timing_info['duration']
                total_duration += duration
                status = "SUCCESS" if timing_info['success'] else "FAILED"
                logging.info(f"{status:<7} {step_name:<30} {self.format_duration(duration):>10}")
        
        logging.info("-" * 60)
        logging.info(f"TOTAL WORKFLOW TIME:             {self.format_duration(total_duration):>10}")
        logging.info("=" * 60)
    
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
            if self.verbose:
                logging.info("=== PRE-REPROVISION STATUS ===")
                self.log_status_summary(initial_data)
            
            cmd = ['python3', str(self.reboot_script), '--all']
            if node_filters:
                # If specific nodes are provided, use --nodes instead of --all
                cmd = ['python3', str(self.reboot_script), '--nodes', ','.join(node_filters)]
                
            logging.info(f"STARTING Triggering reprovision: {' '.join(cmd)}")
            
            # Send "y" to confirm the action automatically with timeout
            result = subprocess.run(
                cmd, 
                input='y\n', 
                capture_output=True, 
                text=True, 
                timeout=60  # 1 minute timeout for reprovision trigger
            )
            
            if result.returncode == 0:
                logging.info("SUCCESS: Reprovision triggered successfully!")
                if self.verbose:
                    logging.info(f"Reprovision script output: {result.stdout}")
                
                # Wait a moment for status to update, then check
                time.sleep(3)
                
                updated_data = self.load_nodes_data()
                if self.verbose:
                    logging.info("=== POST-REPROVISION STATUS ===")
                    self.log_status_summary(updated_data)
                
                # Show what changed
                reprovision_nodes = self.get_nodes_by_status(updated_data, 'in_progress')
                if reprovision_nodes:
                    logging.info(f"SUCCESS: Status updated: {len(reprovision_nodes)} nodes now marked 'in_progress'")
                    if self.verbose:
                        for hostname, node_info in reprovision_nodes.items():
                            ip = node_info.get('ip', 'unknown')
                            started = node_info.get('reprovision_started', 'unknown')
                            logging.info(f"  - {hostname} ({ip}) - started: {started}")
                else:
                    logging.warning("WARNING: No nodes found with 'in_progress' status after triggering reprovision")
                
                return True
            else:
                logging.error(f"ERROR: Reprovision failed (exit code: {result.returncode})")
                logging.error(f"Stderr: {result.stderr}")
                if self.verbose:
                    logging.error(f"Stdout: {result.stdout}")
                return False
                
        except subprocess.TimeoutExpired:
            logging.error("ERROR: Reprovision trigger script timed out after 1 minute")
            logging.warning("This indicates SSH connectivity issues or hung operations")
            logging.info("Continuing to monitor phase - nodes may still be reprovisioning")
            return True  # Continue monitoring even if trigger timed out
                
        except Exception as e:
            logging.error(f"ERROR: Failed to trigger reprovision: {e}")
            return False
    
    def run_cluster_formation(self):
        """Run cluster formation directly"""
        try:
            self.start_timer("Cluster Formation")
            logging.info("SETUP: Starting cluster formation...")
            
            result = subprocess.run([
                'python3', str(self.cluster_script)
            ], capture_output=True, text=True, timeout=1200)  # 20 min timeout
            
            if result.returncode == 0:
                logging.info("SUCCESS: Cluster formation completed successfully!")
                if self.verbose:
                    logging.info(f"Cluster formation output: {result.stdout}")
                self.end_timer("Cluster Formation", True)
                return True
            else:
                logging.error(f"ERROR: Cluster formation failed: {result.stderr}")
                self.end_timer("Cluster Formation", False)
                return False
                
        except subprocess.TimeoutExpired:
            logging.error("ERROR: Cluster formation timed out after 20 minutes")
            self.end_timer("Cluster Formation", False)
            return False
        except Exception as e:
            logging.error(f"ERROR: Failed to run cluster formation: {e}")
            self.end_timer("Cluster Formation", False)
            return False
    
    def run_ceph_setup(self):
        """Run Ceph setup after cluster formation"""
        try:
            self.start_timer("Ceph Setup")
            logging.info("SETUP: Starting Ceph setup...")
            
            result = subprocess.run([
                'python3', str(self.ceph_script)
            ], capture_output=True, text=True, timeout=1800)  # 30 min timeout
            
            if result.returncode == 0:
                logging.info("SUCCESS: Ceph setup completed successfully!")
                if self.verbose:
                    logging.info(f"Ceph setup output: {result.stdout}")
                self.end_timer("Ceph Setup", True)
                return True
            else:
                logging.error(f"ERROR: Ceph setup failed: {result.stderr}")
                self.end_timer("Ceph Setup", False)
                return False
                
        except subprocess.TimeoutExpired:
            logging.error("ERROR: Ceph setup timed out after 30 minutes")
            self.end_timer("Ceph Setup", False)
            return False
        except Exception as e:
            logging.error(f"ERROR: Failed to run Ceph setup: {e}")
            self.end_timer("Ceph Setup", False)
            return False
    
    def run_template_creation(self):
        """Run template creation after Ceph setup"""
        try:
            self.start_timer("Template Creation")
            logging.info("CREATING: Starting template creation...")
            
            result = subprocess.run([
                'python3', str(self.template_script), '--create-templates'
            ], capture_output=True, text=True, timeout=1800)  # 30 min timeout
            
            if result.returncode == 0:
                logging.info("SUCCESS: Template creation completed successfully!")
                if self.verbose:
                    logging.info(f"Template creation output: {result.stdout}")
                self.end_timer("Template Creation", True)
                return True
            else:
                logging.error(f"ERROR: Template creation failed: {result.stderr}")
                self.end_timer("Template Creation", False)
                return False
                
        except subprocess.TimeoutExpired:
            logging.error("ERROR: Template creation timed out after 30 minutes")
            self.end_timer("Template Creation", False)
            return False
        except Exception as e:
            logging.error(f"ERROR: Failed to run template creation: {e}")
            self.end_timer("Template Creation", False)
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
                    if self.verbose:
                        logging.info(f"=== STATUS CHECK ({elapsed/60:.1f} min elapsed) ===")
                        self.log_status_summary(data)
                    
                    # Detail specific states
                    if in_progress:
                        if self.verbose:
                            logging.info(f"IN PROGRESS ({len(in_progress)}): {list(in_progress.keys())}")
                        elif len(in_progress) > 0:
                            logging.info(f"Provisioning in progress: {len(in_progress)} nodes")
                    
                    if provisioning_complete:
                        if self.verbose:
                            logging.info(f"PROVISIONING COMPLETE ({len(provisioning_complete)}): {list(provisioning_complete.keys())}")
                            for hostname, node_info in provisioning_complete.items():
                                registered_time = node_info.get('registered_at', 'unknown')
                                ip = node_info.get('ip', 'unknown')
                                logging.info(f"  - {hostname} ({ip}) finished provisioning at {registered_time}")
                        elif len(provisioning_complete) > 0:
                            logging.info(f"Provisioning complete: {len(provisioning_complete)} nodes ready")
                    
                    if completed and self.verbose:
                        logging.info(f"MONITOR MARKED COMPLETED ({len(completed)}): {list(completed.keys())}")
                        for hostname, node_info in completed.items():
                            completed_time = node_info.get('reprovision_completed', 'unknown')
                            logging.info(f"  - {hostname} completed at {completed_time}")
                    
                    if clustered and self.verbose:
                        logging.info(f"CLUSTERED ({len(clustered)}): {list(clustered.keys())}")
                        logging.info("INFO: Monitor already clustered nodes (direct mode active)")
                    
                    if timeout_nodes:
                        logging.warning(f"TIMED OUT ({len(timeout_nodes)}): {list(timeout_nodes.keys())}")
                    
                    # Check for completed reprovision - trigger our own cluster formation
                    if (completed or provisioning_complete) and not in_progress:
                        total_ready = len(completed) + len(provisioning_complete)
                        logging.info(f"SUCCESS: All reprovisioning complete ({total_ready} nodes ready)!")
                        logging.info("STARTING Starting direct cluster formation workflow...")
                        
                        # End the provisioning timer
                        self.end_timer("Node Provisioning")
                        
                        # Stop the monitor since we're taking control
                        self.stop_monitor()
                        
                        # Run cluster formation directly
                        if self.run_cluster_formation():
                            # Run Ceph setup
                            if self.run_ceph_setup():
                                # Run template creation
                                if self.run_template_creation():
                                    logging.info("COMPLETE: COMPLETE WORKFLOW FINISHED SUCCESSFULLY! COMPLETE: ")
                                    return True
                                else:
                                    logging.error("ERROR: Template creation failed, but cluster is operational")
                                    return False
                            else:
                                logging.error("ERROR: Ceph setup failed, but cluster is operational")  
                                return False
                        else:
                            logging.error("ERROR: Cluster formation failed")
                            return False
                            
                except Exception as e:
                    logging.error(f"Error checking status: {e}")
            
            # Brief pause
            time.sleep(5)
            
            # Periodic summary (every 5 minutes)
            if int(elapsed) % 300 == 0 and elapsed > 0:
                logging.info(f"TIMING: Workflow still running... ({elapsed/60:.1f} minutes elapsed)")
        
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
            
            # Start provisioning timing (cluster formation will be separate)
            self.start_timer("Node Provisioning")
            
            # Trigger the reprovision
            if not self.trigger_reprovision(node_filters):
                logging.error("Failed to trigger reprovision")
                self.end_timer("Node Provisioning", False)
                self.cleanup()
                return False
            
            # Wait for completion
            success = self.wait_for_completion(timeout_minutes)
            
            # Always print timing summary
            self.print_timing_summary()
            
            if success:
                logging.info("SUCCESS: === COMPLETE PROXMOX DEPLOYMENT SUCCESSFUL! ===")
            else:
                logging.error("FAILED: === PROXMOX DEPLOYMENT FAILED OR INCOMPLETE ===")
            
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
    print("  --verbose           Show detailed progress information")
    print("  --help              Show this help")
    print()
    print("Node filters are passed directly to reboot-nodes-for-reprovision.py")
    print("Examples:")
    print("  python3 coordinated-proxmox-reprovision.py")
    print("  python3 coordinated-proxmox-reprovision.py --timeout 90 --verbose")
    print("  python3 coordinated-proxmox-reprovision.py node1 node2")

def main():
    if '--help' in sys.argv:
        print_usage()
        return
    
    # Parse arguments
    timeout_minutes = 60
    node_filters = []
    verbose = False
    
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
        elif arg == '--verbose':
            verbose = True
            i += 1
        else:
            node_filters.append(arg)
            i += 1
    
    # Run the coordinated workflow
    coordinator = CoordinatedReprovision(verbose=verbose)
    success = coordinator.run_coordinated_workflow(
        node_filters=node_filters if node_filters else None,
        timeout_minutes=timeout_minutes
    )
    
    sys.exit(0 if success else 1)

if __name__ == '__main__':
    main()