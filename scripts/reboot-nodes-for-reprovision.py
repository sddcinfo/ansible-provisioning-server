#!/usr/bin/env python3
"""
Enhanced Node Reprovision Script
This script will:
1. Set specified nodes to boot from PXE (EFI boot entry 000E)
2. Call the provisioning server API to reset node status
3. Reboot specified nodes to start fresh provisioning

Usage:
  ./reboot-nodes-for-reprovision.py --all                    # Reboot all nodes
  ./reboot-nodes-for-reprovision.py --nodes node1,node3      # Reboot specific nodes
  ./reboot-nodes-for-reprovision.py --list                   # List available nodes
  ./reboot-nodes-for-reprovision.py --help                   # Show help
"""

import json
import sys
import time
import argparse
import subprocess
import requests
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass
from enum import Enum


class Colors:
    """ANSI color codes for terminal output"""
    RED = '\033[0;31m'
    GREEN = '\033[0;32m'
    YELLOW = '\033[1;33m'
    BLUE = '\033[0;34m'
    CYAN = '\033[0;36m'
    NC = '\033[0m'  # No Color


class AccessibilityStatus(Enum):
    """Node accessibility status"""
    ACCESSIBLE = 0
    SSH_UNREACHABLE = 1
    UNREACHABLE = 2


@dataclass
class NodeInfo:
    """Node configuration data"""
    hostname: str
    os_ip: str
    os_mac: str
    ceph_ip: str
    console_ip: Optional[str] = None


class NodeReprovisioner:
    """Main class for handling node reprovisioning operations"""
    
    def __init__(self, config_path: Optional[Path] = None):
        self.script_dir = Path(__file__).parent.resolve()
        self.nodes_json_path = config_path or self.script_dir.parent / "nodes.json"
        self.server_ip = "10.10.1.1"
        self.provisioning_api_url = f"http://{self.server_ip}/index.php"
        self.ssh_opts = [
            "-o", "ConnectTimeout=8",
            "-o", "ServerAliveInterval=3",
            "-o", "ServerAliveCountMax=2",
            "-o", "StrictHostKeyChecking=no",
            "-o", "PasswordAuthentication=no",
            "-o", "BatchMode=yes",
            "-o", "LogLevel=ERROR"
        ]
        self.nodes: Dict[str, NodeInfo] = {}
        self.delay_between_nodes = 10
        
    def log(self, message: str, color: str = "") -> None:
        """Print timestamped log message with optional color"""
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        print(f"[{timestamp}] {color}{message}{Colors.NC}")
    
    def load_node_config(self) -> None:
        """Load node configuration from JSON file"""
        if not self.nodes_json_path.exists():
            self.log(f"[ERROR] Nodes configuration file not found: {self.nodes_json_path}", Colors.RED)
            sys.exit(1)
        
        self.log(f"Loading node configuration from: {self.nodes_json_path}", Colors.BLUE)
        
        try:
            with open(self.nodes_json_path, 'r') as f:
                config = json.load(f)
            
            for node_data in config.get('nodes', []):
                node = NodeInfo(
                    hostname=node_data['os_hostname'],
                    os_ip=node_data['os_ip'],
                    os_mac=node_data['os_mac'],
                    ceph_ip=node_data['ceph_ip'],
                    console_ip=node_data.get('console_ip')
                )
                self.nodes[node.hostname] = node
            
            self.log(f"[OK] Loaded {len(self.nodes)} nodes from configuration file", Colors.GREEN)
        except (json.JSONDecodeError, KeyError) as e:
            self.log(f"[ERROR] Failed to parse nodes configuration: {e}", Colors.RED)
            sys.exit(1)
    
    def get_all_nodes(self) -> List[str]:
        """Get sorted list of all available node names"""
        return sorted(self.nodes.keys())
    
    def list_nodes(self) -> None:
        """Display all available nodes with their configuration"""
        print()
        self.log("=== Available Nodes ===", Colors.CYAN)
        
        for hostname in self.get_all_nodes():
            node = self.nodes[hostname]
            console = node.console_ip if node.console_ip else "N/A"
            print(f"  {hostname:<8} IP: {node.os_ip:<15} MAC: {node.os_mac:<17} Ceph: {node.ceph_ip:<15} Console: {console}")
        
        print()
        self.log(f"Total nodes available: {len(self.nodes)}")
        print()
    
    def validate_node(self, node_name: str) -> bool:
        """Check if a node exists in configuration"""
        if node_name not in self.nodes:
            self.log(f"[ERROR] Node '{node_name}' not found in configuration", Colors.RED)
            return False
        return True
    
    def run_command(self, cmd: List[str], timeout: int = 10) -> Tuple[bool, str, str]:
        """Execute command with timeout and return success status, stdout, stderr"""
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False
            )
            return result.returncode == 0, result.stdout, result.stderr
        except subprocess.TimeoutExpired:
            return False, "", "Command timed out"
        except Exception as e:
            return False, "", str(e)
    
    def reset_node_status(self, node_name: str, node_mac: str, node_ip: str) -> bool:
        """Reset node status via provisioning API with retry logic"""
        self.log(f"Resetting provisioning status for {node_name} via API...")
        
        api_url = f"{self.provisioning_api_url}?action=reprovision&mac={node_mac}"
        
        for attempt in range(1, 4):
            try:
                response = requests.get(api_url, timeout=15)
                if response.status_code == 200:
                    self.log(f"[OK] API call successful for {node_name} - status reset to NEW", Colors.GREEN)
                    return True
            except requests.RequestException:
                if attempt < 3:
                    self.log(f"[WARNING] API call attempt {attempt} failed for {node_name}, retrying...", Colors.YELLOW)
                    time.sleep(2)
        
        self.log(f"[FAIL] All API call attempts failed for {node_name}", Colors.RED)
        return False
    
    def check_node_accessibility(self, node_name: str, node_ip: str) -> AccessibilityStatus:
        """Check if a node is accessible via ping and SSH"""
        self.log(f"Testing accessibility of {node_name} ({node_ip})...")
        
        # Quick ping test first
        success, _, _ = self.run_command(["ping", "-c", "1", "-W", "1", node_ip], timeout=3)
        
        if success:
            self.log(f"[OK] {node_name} responds to ping", Colors.GREEN)
            
            # Test SSH connectivity
            ssh_cmd = ["ssh"] + self.ssh_opts + [f"root@{node_ip}", "echo 'SSH connection test'"]
            ssh_success, _, _ = self.run_command(ssh_cmd, timeout=8)
            
            if ssh_success:
                self.log(f"[OK] SSH connectivity verified for {node_name}", Colors.GREEN)
                return AccessibilityStatus.ACCESSIBLE
            else:
                self.log(f"[INFO] {node_name} responds to ping but SSH is unreachable (possibly rebooting)", Colors.YELLOW)
                return AccessibilityStatus.SSH_UNREACHABLE
        else:
            self.log(f"[INFO] {node_name} is not responding to ping (unreachable or already rebooting)", Colors.YELLOW)
            return AccessibilityStatus.UNREACHABLE
    
    def set_pxe_boot(self, node_name: str, node_ip: str) -> bool:
        """Set EFI boot order to PXE with fallback options"""
        self.log(f"Setting {node_name} to boot from PXE...")
        
        # Try primary EFI boot entry
        ssh_cmd = ["ssh"] + self.ssh_opts + [f"root@{node_ip}", "efibootmgr -n 000E"]
        success, _, _ = self.run_command(ssh_cmd, timeout=12)
        
        if success:
            self.log(f"[OK] EFI boot order set to PXE (000E) for {node_name}", Colors.GREEN)
            return True
        
        self.log(f"[WARNING] Primary PXE boot entry (000E) failed for {node_name}, trying alternatives...", Colors.YELLOW)
        
        # Try alternative boot entries
        for boot_entry in ["14", "0014", "000F", "0E"]:
            ssh_cmd = ["ssh"] + self.ssh_opts + [f"root@{node_ip}", f"efibootmgr -n {boot_entry}"]
            success, _, _ = self.run_command(ssh_cmd, timeout=10)
            
            if success:
                self.log(f"[OK] Alternative EFI boot entry ({boot_entry}) set for {node_name}", Colors.GREEN)
                return True
        
        self.log(f"[WARNING] Could not set any PXE boot entry for {node_name}", Colors.YELLOW)
        self.log(f"[INFO] Node may still boot to PXE if configured as default boot option", Colors.YELLOW)
        return False
    
    def reboot_node(self, node_name: str, node_ip: str) -> bool:
        """Reboot a node with multiple fallback methods"""
        self.log(f"Initiating reboot for {node_name}...")
        
        # Try graceful reboot first
        ssh_cmd = ["ssh"] + self.ssh_opts + [f"root@{node_ip}", "systemctl reboot"]
        success, _, _ = self.run_command(ssh_cmd, timeout=8)
        
        if success:
            self.log(f"[OK] Graceful reboot command sent to {node_name}", Colors.GREEN)
            return True
        
        # Fall back to immediate reboot
        ssh_cmd = ["ssh"] + self.ssh_opts + [f"root@{node_ip}", "nohup reboot >/dev/null 2>&1 &"]
        success, _, _ = self.run_command(ssh_cmd, timeout=8)
        
        if success:
            self.log(f"[OK] Immediate reboot command sent to {node_name}", Colors.GREEN)
            return True
        
        # Try emergency reboot
        ssh_cmd = ["ssh"] + self.ssh_opts + [f"root@{node_ip}", "echo b > /proc/sysrq-trigger"]
        success, _, _ = self.run_command(ssh_cmd, timeout=8)
        
        if success:
            self.log(f"[WARNING] Emergency reboot triggered for {node_name}", Colors.YELLOW)
            return True
        
        self.log(f"[FAIL] All reboot methods failed for {node_name}", Colors.RED)
        return False
    
    def process_node(self, node_name: str) -> bool:
        """Process a single node: API call -> accessibility check -> EFI boot -> reboot"""
        node = self.nodes[node_name]
        start_time = time.time()
        
        self.log(f"=== Processing {node_name} ({node.os_ip}) ===", Colors.BLUE)
        
        # Step 1: Reset provisioning status via API
        self.log("Step 1: Resetting provisioning status via API...")
        if not self.reset_node_status(node_name, node.os_mac, node.os_ip):
            self.log(f"[FAIL] Could not reset API status for {node_name}", Colors.RED)
            return False
        
        # Step 2: Check node accessibility
        self.log(f"Step 2: Checking accessibility of {node_name}...")
        accessibility = self.check_node_accessibility(node_name, node.os_ip)
        
        if accessibility == AccessibilityStatus.UNREACHABLE:
            self.log(f"[INFO] {node_name} is unreachable - possibly already rebooting or powered off", Colors.YELLOW)
            self.log(f"[OK] {node_name} API status reset completed (node unreachable)", Colors.GREEN)
            return True
        elif accessibility == AccessibilityStatus.SSH_UNREACHABLE:
            self.log(f"[INFO] {node_name} SSH is unreachable but responds to ping - likely shutting down", Colors.YELLOW)
            self.log(f"[OK] {node_name} API status reset completed (SSH unreachable)", Colors.GREEN)
            return True
        
        # Step 3: Set EFI boot order (best effort)
        self.log(f"Step 3: Configuring PXE boot for {node_name}...")
        self.set_pxe_boot(node_name, node.os_ip)  # Continue even if this fails
        
        # Step 4: Reboot the node
        self.log(f"Step 4: Rebooting {node_name}...")
        if not self.reboot_node(node_name, node.os_ip):
            self.log(f"[FAIL] Failed to reboot {node_name}", Colors.RED)
            return False
        
        duration = int(time.time() - start_time)
        self.log(f"[SUCCESS] {node_name} processed successfully (took {duration}s)", Colors.GREEN)
        return True
    
    def confirm_action(self, message: str) -> bool:
        """Ask user for confirmation"""
        response = input(f"{message} (y/N): ").strip().lower()
        return response in ['y', 'yes']
    
    def run(self, all_nodes: bool = False, selected_nodes: List[str] = None) -> None:
        """Main execution method"""
        self.log("=== Enhanced Node Reprovision Script ===", Colors.CYAN)
        self.log(f"Script location: {__file__}")
        self.log(f"Nodes config: {self.nodes_json_path}")
        self.log(f"Provisioning API: {self.provisioning_api_url}")
        print()
        
        # Load configuration
        self.load_node_config()
        
        # Determine which nodes to process
        if all_nodes:
            nodes_to_process = self.get_all_nodes()
            self.log(f"Mode: Rebooting ALL nodes with {self.delay_between_nodes}s delay between nodes", Colors.YELLOW)
        else:
            nodes_to_process = selected_nodes or []
            self.log(f"Mode: Rebooting selected nodes: {', '.join(nodes_to_process)}", Colors.YELLOW)
        
        # Validate all specified nodes
        validation_failed = False
        for node in nodes_to_process:
            if not self.validate_node(node):
                validation_failed = True
        
        if validation_failed:
            print()
            self.log("Available nodes:", Colors.CYAN)
            self.list_nodes()
            sys.exit(1)
        
        # Show what will happen
        print()
        self.log("This will process the following nodes:", Colors.YELLOW)
        for node_name in nodes_to_process:
            node = self.nodes[node_name]
            print(f"  {node_name:<8} IP: {node.os_ip:<15} MAC: {node.os_mac}")
        
        print()
        self.log("Process for each node:", Colors.YELLOW)
        self.log("  1. Reset provisioning status via API call")
        self.log("  2. Check node accessibility (ping + SSH)")
        self.log("  3. Set node to boot from PXE (if accessible)")
        self.log("  4. Initiate node reboot")
        if all_nodes:
            self.log(f"  5. Wait {self.delay_between_nodes} seconds before processing next node")
        print()
        
        # Confirmation for all nodes mode
        if all_nodes:
            if not self.confirm_action(f"Are you sure you want to reboot ALL {len(nodes_to_process)} nodes?"):
                self.log("Operation cancelled by user", Colors.YELLOW)
                sys.exit(0)
        
        # Process nodes
        print()
        self.log("=== Starting Node Processing ===", Colors.BLUE)
        
        success_count = 0
        failed_nodes = []
        total_start_time = time.time()
        
        for i, node_name in enumerate(nodes_to_process):
            if self.process_node(node_name):
                success_count += 1
            else:
                failed_nodes.append(node_name)
            
            # Add delay between nodes if processing all nodes and not the last node
            if all_nodes and i < len(nodes_to_process) - 1:
                print()
                self.log(f"Waiting {self.delay_between_nodes} seconds before processing next node...", Colors.BLUE)
                time.sleep(self.delay_between_nodes)
                print()
            else:
                print()
        
        # Final summary
        total_duration = int(time.time() - total_start_time)
        
        self.log("=== Reprovision Process Summary ===", Colors.BLUE)
        self.log(f"Total processing time: {total_duration} seconds")
        self.log(f"Successfully processed: {success_count}/{len(nodes_to_process)} nodes")
        
        if failed_nodes:
            self.log(f"Failed nodes: {', '.join(failed_nodes)}", Colors.YELLOW)
            self.log("Failed nodes may need manual attention:", Colors.YELLOW)
            self.log("  - Check network connectivity")
            self.log("  - Verify SSH access")
            self.log("  - Check node power status")
            self.log("  - Manual reboot via console/IPMI")
        else:
            self.log("All nodes processed successfully!", Colors.GREEN)
        
        print()
        self.log("=== Next Steps ===", Colors.GREEN)
        self.log("1. Monitor nodes via console or management interface")
        self.log("2. Nodes should PXE boot and start OS installation")
        self.log("3. Wait for all nodes to complete installation (~15-20 minutes)")
        self.log(f"4. Check provisioning status: http://{self.server_ip}/index.php")
        self.log("5. Once all nodes are ready, run cluster formation script")
        print()
        
        # Exit with appropriate code
        if failed_nodes:
            self.log(f"Script completed with {len(failed_nodes)} failed node(s)", Colors.YELLOW)
            sys.exit(1)
        else:
            self.log("Script completed successfully!", Colors.GREEN)
            sys.exit(0)


def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(
        description='Enhanced Node Reprovision Script',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --all                    # Reboot all nodes with default delay
  %(prog)s --all --delay 15         # Reboot all nodes with 15s delay
  %(prog)s --nodes node1,node3      # Reboot only node1 and node3
  %(prog)s --nodes node2            # Reboot only node2
  %(prog)s --list                   # Show available nodes

Notes:
  - Node configuration is read from nodes.json
  - Each node will be set to PXE boot before rebooting
  - Provisioning API status will be reset for each node
  - Script handles timeout scenarios gracefully
  - Failed nodes will be reported at the end
        """
    )
    
    parser.add_argument('--all', action='store_true',
                       help='Reboot all nodes sequentially')
    parser.add_argument('--nodes', type=str,
                       help='Reboot specific nodes (comma-separated list)')
    parser.add_argument('--list', action='store_true',
                       help='List all available nodes and exit')
    parser.add_argument('--delay', type=int, default=10,
                       help='Set delay between nodes when using --all (default: 10s)')
    
    args = parser.parse_args()
    
    # Create reprovisioner instance
    reprovisioner = NodeReprovisioner()
    reprovisioner.delay_between_nodes = args.delay
    
    # Handle list mode
    if args.list:
        reprovisioner.load_node_config()
        reprovisioner.list_nodes()
        sys.exit(0)
    
    # Validate arguments
    if not args.all and not args.nodes:
        parser.error("You must specify either --all or --nodes")
    
    if args.all and args.nodes:
        parser.error("Cannot use --all and --nodes together")
    
    # Parse selected nodes if provided
    selected_nodes = []
    if args.nodes:
        selected_nodes = [n.strip() for n in args.nodes.split(',')]
    
    # Run the reprovisioner
    try:
        reprovisioner.run(all_nodes=args.all, selected_nodes=selected_nodes)
    except KeyboardInterrupt:
        print()
        reprovisioner.log("Operation cancelled by user", Colors.YELLOW)
        sys.exit(130)
    except Exception as e:
        reprovisioner.log(f"[ERROR] Unexpected error: {e}", Colors.RED)
        sys.exit(1)


if __name__ == "__main__":
    main()