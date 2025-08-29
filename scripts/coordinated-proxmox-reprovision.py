#!/usr/bin/env python3
"""
Coordinated Proxmox Reprovision Workflow
Orchestrates the complete reprovision process including monitoring and automatic cluster formation
"""

import sys
import os
import subprocess
import threading
import time
import logging
import signal
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
            cmd = ['python3', str(self.reboot_script), '--all']
            if node_filters:
                # If specific nodes are provided, use --nodes instead of --all
                cmd = ['python3', str(self.reboot_script), '--nodes', ','.join(node_filters)]
                
            logging.info(f"Starting reprovision: {' '.join(cmd)}")
            
            # Send "y" to confirm the action automatically
            result = subprocess.run(cmd, input='y\n', capture_output=True, text=True)
            
            if result.returncode == 0:
                logging.info("Reprovision triggered successfully")
                logging.info(f"Output: {result.stdout}")
                return True
            else:
                logging.error(f"Reprovision failed: {result.stderr}")
                return False
                
        except Exception as e:
            logging.error(f"Failed to trigger reprovision: {e}")
            return False
    
    def wait_for_completion(self, timeout_minutes=60):
        """Wait for reprovision to complete with timeout"""
        logging.info(f"Monitoring reprovision progress (timeout: {timeout_minutes} minutes)...")
        
        start_time = time.time()
        timeout_seconds = timeout_minutes * 60
        
        while not self.stop_monitoring:
            # Check if monitor process is still running
            if self.monitor_process and self.monitor_process.poll() is not None:
                # Monitor died, check why
                stdout, stderr = self.monitor_process.communicate()
                if "Cluster formation completed successfully" in stdout:
                    logging.info("Reprovision completed successfully!")
                    return True
                else:
                    logging.error(f"Monitor died unexpectedly: {stderr}")
                    return False
            
            # Check timeout
            elapsed = time.time() - start_time
            if elapsed > timeout_seconds:
                logging.warning(f"Reprovision timed out after {timeout_minutes} minutes")
                return False
            
            # Brief pause before next check
            time.sleep(30)
            
            # Log periodic status
            if int(elapsed) % 300 == 0:  # Every 5 minutes
                logging.info(f"Reprovision still in progress... ({elapsed/60:.1f} minutes elapsed)")
        
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