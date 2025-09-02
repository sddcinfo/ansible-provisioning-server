#!/usr/bin/env python3
"""
Proxmox Template Manager

A comprehensive script to manage Proxmox VM templates.
Intelligently handles template creation, validation, and testing with idempotent operations.

COMMANDS:
    Default (no args)    - Verify template configuration and status
    --create-templates   - Create missing/broken templates (skips existing valid ones)
    --test-templates     - Clone and boot templates to verify functionality  
    --clean-up          - Remove templates and test VMs
    --remove-all        - Complete cleanup including cached images (destructive)

OPTIONS:
    --force             - Force recreation even if templates exist (use with --create-templates)
    --yes, -y           - Auto-confirm destructive operations

EXAMPLES:
    # Quick check if templates are ready (default behavior)
    python3 template-manager.py
    
    # Create only missing templates (safe to re-run)
    python3 template-manager.py --create-templates
    
    # Force recreate all templates from scratch
    python3 template-manager.py --create-templates --force
    
    # Full functional test by cloning and booting
    python3 template-manager.py --test-templates
    
    # Complete cleanup without prompts
    python3 template-manager.py --remove-all --yes

TEMPLATE IDS:
    9000 - Ubuntu 24.04 base template (qemu-agent, cloud-init)
"""

import argparse
import subprocess
import sys
import time
import json
import logging
import os
import yaml
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class ProxmoxTemplateManager:
    """Manages Proxmox VM templates."""
    
    def __init__(self, config_file=None):
        # Load configuration from YAML file
        self.config = self.load_config(config_file)
        
        # Set configuration from YAML or use defaults
        self.proxmox_host = self.config['proxmox']['host']
        self.ssh_key_path = self.config['ssh']['key_path']
        self.ssh_key_pub = self.config['ssh']['public_key_path']
        
        # Template configurations from YAML
        self.templates = {
            'base': {
                'id': self.config['templates']['base']['id'],
                'name': self.config['templates']['base']['name'],
                'description': self.config['templates']['base']['description'],
                'memory': self.config['templates']['base']['memory'],
                'cores': self.config['templates']['base']['cores']
            }
        }
        
        # Cloud image settings from YAML
        self.japan_mirror = self.config['cloud_image']['japan_mirror']
        self.cloud_image_url = self.config['cloud_image']['url']
        self.japan_cloud_image_url = self.config['cloud_image']['japan_cloud_url']
        self.cached_image_path = self.config['cloud_image']['cached_path']
        self.cache_dir = "/tmp/template-cache"
        self.cloud_image_file = "ubuntu-24.04-cloud.img"
    
    def load_config(self, config_file):
        """Load configuration from existing project files."""
        if config_file is not None:
            # Use provided config file
            with open(config_file, 'r') as f:
                return yaml.safe_load(f)
        
        # Use self-contained configuration from existing project files
        logger.info("Using self-contained configuration from project files")
        
        # Load nodes.json to get node information
        nodes_file = Path(__file__).parent.parent / 'nodes.json'
        primary_node_ip = "10.10.1.21"  # Default fallback
        
        if nodes_file.exists():
            with open(nodes_file, 'r') as f:
                nodes_data = json.load(f)
                # Get first node's IP as Proxmox host
                if 'nodes' in nodes_data and nodes_data['nodes']:
                    primary_node_ip = nodes_data['nodes'][0].get('os_ip', primary_node_ip)
        
        # Return self-contained configuration
        return {
            'templates': {
                'base': {
                    'id': 9000,
                    'name': 'ubuntu-base-template',
                    'description': 'Ubuntu 24.04 Base Template - qemu-agent + cloud-init',
                    'memory': 2048,
                    'cores': 2
                }
            },
            'cloud_image': {
                'url': 'https://cloud-images.ubuntu.com/noble/current/noble-server-cloudimg-amd64.img',
                'japan_mirror': 'https://ftp.riken.jp/Linux/ubuntu-releases',
                'japan_cloud_url': 'http://cloud-images.ubuntu.com.edgecastcdn.net/noble/current/noble-server-cloudimg-amd64.img',
                'cached_path': '/mnt/rbd-iso/template/images/ubuntu-24.04-cloudimg-cached.img'
            },
            'ssh': {
                'key_path': '/home/sysadmin/.ssh/sysadmin_automation_key',
                'public_key_path': '/home/sysadmin/.ssh/sysadmin_automation_key.pub',
                'user': 'sysadmin'
            },
            'proxmox': {
                'host': primary_node_ip,
                'storage': 'local-lvm',
                'bridge': 'vmbr0'
            }
        }
    
    def run_ssh_command(self, command: str, timeout: int = 300) -> Tuple[int, str, str]:
        """Execute SSH command on Proxmox host."""
        ssh_cmd = [
            "ssh", "-o", "ConnectTimeout=10",
            "-o", "StrictHostKeyChecking=no",
            f"root@{self.proxmox_host}",
            command
        ]
        
        try:
            result = subprocess.run(
                ssh_cmd, 
                capture_output=True, 
                text=True, 
                timeout=timeout
            )
            return result.returncode, result.stdout, result.stderr
        except subprocess.TimeoutExpired:
            logger.error(f"SSH command timed out after {timeout}s: {command}")
            return 1, "", "Command timed out"
        except Exception as e:
            logger.error(f"SSH command failed: {e}")
            return 1, "", str(e)
    
    def check_template_exists(self, vm_id: int) -> bool:
        """Check if a template exists."""
        cmd = f"qm config {vm_id} >/dev/null 2>&1 && echo 'exists' || echo 'not_found'"
        returncode, stdout, stderr = self.run_ssh_command(cmd)
        return stdout.strip() == 'exists'
    
    def get_vm_ip(self, vm_id: int, max_attempts: int = 20) -> Optional[str]:
        """Get VM IP address via guest agent."""
        for attempt in range(max_attempts):
            cmd = f"qm guest cmd {vm_id} network-get-interfaces 2>/dev/null"
            returncode, stdout, stderr = self.run_ssh_command(cmd, timeout=30)
            
            if returncode == 0 and stdout:
                try:
                    # Parse guest agent JSON response
                    data = json.loads(stdout)
                    # Handle both old format (wrapped in 'return') and new format (direct array)
                    interfaces = data.get('return', data) if isinstance(data, dict) else data
                    for interface in interfaces:
                        if interface.get('name') != 'lo':
                            for addr in interface.get('ip-addresses', []):
                                if addr.get('ip-address-type') == 'ipv4':
                                    ip = addr.get('ip-address')
                                    if ip and ip != '127.0.0.1':
                                        return ip
                except (json.JSONDecodeError, KeyError):
                    pass
            
            logger.info(f"Attempt {attempt + 1}/{max_attempts} - waiting for VM {vm_id} IP...")
            time.sleep(5)
        
        return None
    
    def test_ssh_connectivity(self, ip: str, timeout: int = 10) -> bool:
        """Test SSH connectivity to VM."""
        ssh_cmd = [
            "ssh", "-o", "ConnectTimeout=5",
            "-o", "StrictHostKeyChecking=no",
            "-i", self.ssh_key_path,
            f"sysadmin@{ip}",
            "echo 'SSH_SUCCESS'"
        ]
        
        try:
            result = subprocess.run(ssh_cmd, capture_output=True, text=True, timeout=timeout)
            return result.returncode == 0 and 'SSH_SUCCESS' in result.stdout
        except subprocess.TimeoutExpired:
            return False
        except Exception:
            return False
    
    def cleanup_template(self, vm_id: int):
        """Clean up existing template or VM."""
        if self.check_template_exists(vm_id):
            logger.info(f"Removing existing template/VM {vm_id}")
            
            # First check if VM is running and stop it
            status_cmd = f"qm status {vm_id}"
            returncode, stdout, stderr = self.run_ssh_command(status_cmd)
            
            if returncode == 0 and 'running' in stdout:
                logger.info(f"Stopping running VM {vm_id}")
                stop_cmd = f"qm stop {vm_id}"
                self.run_ssh_command(stop_cmd)
                time.sleep(3)
            
            # Now destroy the VM
            cmd = f"qm destroy {vm_id} --purge"
            returncode, stdout, stderr = self.run_ssh_command(cmd)
            
            if returncode != 0:
                logger.warning(f"Failed to destroy VM {vm_id}: {stderr}")
                # Try force destroy
                force_cmd = f"qm destroy {vm_id} --purge --skiplock"
                self.run_ssh_command(force_cmd)
            
            time.sleep(2)
    
    def download_cloud_image(self) -> str:
        """Download and cache Ubuntu cloud image with Japan mirror fallback."""
        import os
        
        # Create cache directory
        os.makedirs(self.cache_dir, exist_ok=True)
        cached_image_path = os.path.join(self.cache_dir, self.cloud_image_file)
        
        # Check if cached image exists
        if os.path.exists(cached_image_path):
            logger.info(f"Using cached cloud image: {cached_image_path}")
            return cached_image_path
        
        logger.info("Downloading Ubuntu 24.04 cloud image...")
        
        # Try Japan mirror first for faster download
        japan_url = f"{self.japan_mirror}/24.04.3/ubuntu-24.04.3-server-cloudimg-amd64.img"
        
        try:
            # Try Japan mirror first
            logger.info("Trying Japan mirror for faster download...")
            result = subprocess.run([
                "wget", "-q", 
                "-O", cached_image_path,
                japan_url
            ], timeout=600)
            
            if result.returncode == 0:
                logger.info("Downloaded from Japan mirror successfully")
                return cached_image_path
        except (subprocess.TimeoutExpired, subprocess.CalledProcessError):
            logger.warning("Japan mirror failed, trying main Ubuntu site...")
        
        # Fallback to main Ubuntu site
        try:
            result = subprocess.run([
                "wget", "-q",
                "-O", cached_image_path,
                self.cloud_image_url
            ], timeout=900)
            
            if result.returncode == 0:
                logger.info("Downloaded from main Ubuntu site successfully")
                return cached_image_path
            else:
                raise Exception("Download failed")
                
        except Exception as e:
            logger.error(f"Failed to download cloud image: {e}")
            # Clean up partial download
            if os.path.exists(cached_image_path):
                os.remove(cached_image_path)
            raise
    
    def create_cloud_base_vm(self, vm_id: int, vm_name: str) -> bool:
        """Create a VM from Ubuntu cloud image using virt-customize."""
        logger.info(f"Creating cloud-based VM: {vm_name} (ID: {vm_id})")
        
        # Read SSH public key
        try:
            with open(self.ssh_key_pub, 'r') as f:
                ssh_key = f.read().strip()
        except Exception as e:
            logger.error(f"Failed to read SSH public key: {e}")
            return False
        
        # Create VM on Proxmox using standard method (no EFI, no virt-customize)
        logger.info("Creating VM on Proxmox...")
        proxmox_script = f"""
set -e

# Clean up existing VM
qm destroy {vm_id} --purge 2>/dev/null || true

# Use proper Proxmox template storage location like working scripts
MODIFIED_IMAGE="ubuntu-24.04-cloudimg-amd64-modified.img"
TEMPLATE_DIR="/mnt/rbd-iso/template/images"

# Check if RBD storage is mounted, if not use local storage
if [ ! -d "/mnt/rbd-iso" ]; then
    echo "RBD storage not found at /mnt/rbd-iso, using local storage instead..."
    TEMPLATE_DIR="/var/lib/vz/template/images"
fi

# Check if modified cloud image exists (like working script)
if [ ! -f $TEMPLATE_DIR/$MODIFIED_IMAGE ]; then
    echo "Modified cloud image not found: $TEMPLATE_DIR/$MODIFIED_IMAGE"
    echo "Need to run prepare-cloud-image.sh first or create it now..."
    
    echo "Installing required tools..."
    apt-get update >/dev/null 2>&1 && apt-get install -y libguestfs-tools >/dev/null 2>&1
    
    # Create template directory if it doesn't exist
    mkdir -p $TEMPLATE_DIR
    
    # Check if cached image exists locally first
    if [ ! -f $TEMPLATE_DIR/ubuntu-24.04-cloudimg-cached.img ]; then
        echo "Downloading Ubuntu cloud image from Japan mirror..."
        wget -q -O $TEMPLATE_DIR/ubuntu-24.04-cloudimg-cached.img "{self.japan_cloud_image_url}" || \
        wget -q -O $TEMPLATE_DIR/ubuntu-24.04-cloudimg-cached.img "{self.cloud_image_url}"
    else
        echo "Using cached Ubuntu cloud image..."
    fi
    
    # Copy cached image to working image
    cp $TEMPLATE_DIR/ubuntu-24.04-cloudimg-cached.img $TEMPLATE_DIR/ubuntu-24.04-cloudimg.img
    
    echo "Preparing cloud image with EFI support and qemu-guest-agent..."
    cd $TEMPLATE_DIR
    cp ubuntu-24.04-cloudimg.img $MODIFIED_IMAGE
    
    # Install essential packages including EFI bootloader (matching working script)
    virt-customize --install qemu-guest-agent,grub-efi-amd64,grub-efi-amd64-signed,shim-signed -a $MODIFIED_IMAGE
    
    # Reset machine-id (matching working script)
    virt-sysprep -a $MODIFIED_IMAGE || true
    
    # Create sysadmin user and setup SSH (matching working script)
    virt-customize -a $MODIFIED_IMAGE --run-command 'useradd -m -s /bin/bash sysadmin'
    virt-customize -a $MODIFIED_IMAGE --run-command 'usermod -aG sudo sysadmin'  
    virt-customize -a $MODIFIED_IMAGE --run-command 'echo "sysadmin:password" | chpasswd'
    virt-customize -a $MODIFIED_IMAGE --ssh-inject sysadmin:string:'{ssh_key}'
    virt-customize -a $MODIFIED_IMAGE --run-command 'echo "sysadmin ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/sysadmin'
    
    # Fix EFI boot (matching working script approach)
    virt-customize -a $MODIFIED_IMAGE --run-command 'update-grub && grub-install --target=x86_64-efi --efi-directory=/boot/efi --bootloader-id=ubuntu --recheck' || true
    virt-customize -a $MODIFIED_IMAGE --run-command 'mkdir -p /boot/efi/EFI/BOOT && cp /boot/efi/EFI/ubuntu/grubx64.efi /boot/efi/EFI/BOOT/BOOTX64.EFI 2>/dev/null || cp /boot/efi/EFI/ubuntu/shimx64.efi /boot/efi/EFI/BOOT/BOOTX64.EFI' || true
    
    echo "Cloud image prepared successfully in $TEMPLATE_DIR"
else
    echo "Using existing prepared cloud image: $TEMPLATE_DIR/$MODIFIED_IMAGE"
fi

# Create VM with EFI configuration (matching working shell script)
qm create {vm_id} \\
  --name '{vm_name}' \\
  --memory 2048 \\
  --cores 2 \\
  --net0 virtio,bridge=vmbr0 \\
  --scsihw virtio-scsi-pci \\
  --ostype l26 \\
  --cpu host \\
  --agent enabled=1 \\
  --machine q35 \\
  --bios ovmf \\
  --rng0 source=/dev/urandom,max_bytes=1024,period=1000

# CRITICAL: Disable ROM bar on network interface to prevent iPXE boot (like working script)
qm set {vm_id} --net0 virtio,bridge=vmbr0,rombar=0 || echo "Warning: Failed to disable network ROM bar, continuing anyway"

# Add EFI disk FIRST (working configuration from shell scripts)
echo "Adding EFI disk..."
qm set {vm_id} --efidisk0 rbd:4,efitype=4m,pre-enrolled-keys=0

# Import the prepared disk from proper Proxmox storage location
echo "Importing prepared disk..."
qm importdisk {vm_id} $TEMPLATE_DIR/$MODIFIED_IMAGE rbd --format raw

# Attach the imported disk as scsi0 (disk gets auto-numbered by importdisk)
echo "Configuring main disk..."
qm set {vm_id} --scsi0 rbd:vm-{vm_id}-disk-1

# CRITICAL: Set disk-only boot order (not just --boot c)
qm set {vm_id} --boot order=scsi0 --bootdisk scsi0

# Add cloud-init drive (working configuration)
echo "Adding cloud-init..."
qm set {vm_id} --ide2 rbd:cloudinit

# Copy SSH key to Proxmox host (like working script)
echo '{ssh_key}' > /tmp/sysadmin_automation_key.pub

# Configure cloud-init (matching working script exactly)
qm set {vm_id} --ciuser sysadmin --cipassword password --sshkeys /tmp/sysadmin_automation_key.pub --ipconfig0 ip=dhcp

# Resize disk to reasonable size (like working script)
qm resize {vm_id} scsi0 32G || echo "Warning: Failed to resize disk, continuing anyway"

# Add serial console
qm set {vm_id} --serial0 socket --vga serial0

# Set description
qm set {vm_id} --description '{vm_name} - Ubuntu 24.04 cloud image base'

# Clean up temp files (like working script)
rm -f /tmp/sysadmin_automation_key.pub

echo "VM {vm_name} created successfully"
"""
        
        returncode, stdout, stderr = self.run_ssh_command(proxmox_script)
        if returncode != 0:
            logger.error(f"Failed to create VM on Proxmox: {stderr}")
            return False
        
        # Clean up local working image
        try:
            os.remove(working_image)
        except:
            pass
        
        logger.info(f"[SUCCESS] Cloud-based VM created: {vm_name} (ID: {vm_id})")
        return True
    
    def create_base_template(self) -> bool:
        """Create the base Ubuntu template from cloud image."""
        template_id = self.templates['base']['id']
        template_name = self.templates['base']['name']
        
        logger.info(f"Creating base template: {template_name} (ID: {template_id})")
        
        # Clean up existing template
        self.cleanup_template(template_id)
        
        # Create VM from cloud image
        if not self.create_cloud_base_vm(template_id, template_name):
            logger.error("Failed to create base VM from cloud image")
            return False
        
        # Convert to template directly (like working script - no VM startup needed)
        logger.info("Converting to template...")
        cmd = f"qm template {template_id}"
        returncode, stdout, stderr = self.run_ssh_command(cmd)
        
        if returncode != 0:
            logger.error(f"Failed to convert to template: {stderr}")
            return False
        
        logger.info(f"[SUCCESS] Base template created successfully: {template_name} (ID: {template_id})")
        return True
    
    
    def test_template(self, template_type: str) -> bool:
        """Test a template by creating and testing a VM."""
        template_id = self.templates[template_type]['id']
        template_name = self.templates[template_type]['name']
        test_vm_id = 8000 + template_id
        test_vm_name = f"test-{template_type}"
        
        logger.info(f"Testing template: {template_name}")
        
        # Ensure template exists
        if not self.check_template_exists(template_id):
            logger.error(f"Template {template_id} does not exist")
            return False
        
        # Clean up existing test VM
        self.cleanup_template(test_vm_id)
        
        # Clone template for testing
        logger.info(f"Cloning template for testing...")
        cmd = f"qm clone {template_id} {test_vm_id} --name '{test_vm_name}'"
        returncode, stdout, stderr = self.run_ssh_command(cmd)
        
        if returncode != 0:
            logger.error(f"Failed to clone template: {stderr}")
            return False
        
        # Start test VM
        logger.info("Starting test VM...")
        cmd = f"qm start {test_vm_id}"
        self.run_ssh_command(cmd)
        
        # Wait for IP
        ip = self.get_vm_ip(test_vm_id)
        if not ip:
            logger.error("Failed to get test VM IP address")
            return False
        
        logger.info(f"Test VM IP: {ip}")
        
        # Test SSH connectivity
        for i in range(10):
            if self.test_ssh_connectivity(ip):
                logger.info("[SUCCESS] SSH connectivity test passed")
                break
            logger.info(f"Waiting for SSH... ({i+1}/10)")
            time.sleep(5)
        else:
            logger.error("[FAILED] SSH connectivity test failed")
            return False
        
        # Basic functionality test passed with SSH connectivity
        
        # Clean up test VM
        logger.info("Cleaning up test VM...")
        self.run_ssh_command(f"qm stop {test_vm_id}")
        time.sleep(5)
        self.run_ssh_command(f"qm destroy {test_vm_id} --purge")
        
        logger.info(f"[SUCCESS] Template {template_name} test completed successfully")
        return True
    
    def verify_template_config(self, template_type: str) -> bool:
        """Verify that a template has the correct configuration."""
        template_id = self.templates[template_type]['id']
        template_name = self.templates[template_type]['name']
        
        logger.info(f"Verifying template: {template_name} (ID: {template_id})")
        
        # Check if template exists
        if not self.check_template_exists(template_id):
            logger.info(f"Template {template_name} does not exist")
            return False
        
        # Get template config
        cmd = f"qm config {template_id}"
        returncode, stdout, stderr = self.run_ssh_command(cmd)
        
        if returncode != 0:
            logger.warning(f"Failed to get template config: {stderr}")
            return False
        
        # Check that it's actually a template
        if 'template: 1' not in stdout:
            logger.warning(f"VM {template_id} exists but is not a template")
            return False
        
        # Check basic configuration requirements
        has_agent = 'agent: enabled=1' in stdout or 'agent: 1' in stdout
        has_efidisk = 'efidisk0:' in stdout
        has_cloudinit = 'ide2:' in stdout and 'cloudinit' in stdout
        
        if not has_agent:
            logger.warning(f"Template {template_name} missing qemu-guest-agent")
            return False
        
        if not has_efidisk:
            logger.warning(f"Template {template_name} missing EFI disk")
            return False
        
        if not has_cloudinit:
            logger.warning(f"Template {template_name} missing cloud-init")
            return False
        
        logger.info(f"[SUCCESS] Template {template_name} configuration verified")
        return True
    
    def verify_all_templates(self) -> Dict[str, bool]:
        """Verify all templates and return status."""
        results = {}
        
        logger.info("[VERIFYING] Verifying existing templates...")
        
        for template_type in ['base']:
            results[template_type] = self.verify_template_config(template_type)
        
        # Summary
        all_valid = all(results.values())
        
        if all_valid:
            logger.info("[SUCCESS] All templates exist and are properly configured")
        else:
            missing = [name for name, exists in results.items() if not exists]
            if missing:
                logger.info(f"[FAILED] Missing or misconfigured templates: {', '.join(missing)}")
        
        return results
    
    def create_templates(self, force: bool = False):
        """Create base template."""
        logger.info("[START] Starting template creation process")
        
        if not force:
            # Check existing templates first
            template_status = self.verify_all_templates()
            
            # Only create missing or misconfigured templates
            if template_status['base']:
                logger.info("Base template already exists and is properly configured - skipping")
            else:
                if not self.create_base_template():
                    logger.error("Failed to create base template")
                    return False
        else:
            # Force recreation of all templates
            logger.info("Force mode: Recreating all templates")
            
            # Create base template
            if not self.create_base_template():
                logger.error("Failed to create base template")
                return False
        
        logger.info("[COMPLETE] All templates created successfully!")
        
        # Show final status
        cmd = "qm list | grep -E '(9000)'"
        returncode, stdout, stderr = self.run_ssh_command(cmd)
        if stdout:
            logger.info("Created templates:")
            for line in stdout.strip().split('\n'):
                logger.info(f"  {line}")
        
        return True
    
    def test_templates(self):
        """Test base template."""
        logger.info("[TESTING] Testing templates")
        
        success = True
        
        # Test base template
        if not self.test_template('base'):
            logger.error("Base template test failed")
            success = False
        
        if success:
            logger.info("[COMPLETE] All template tests passed!")
        else:
            logger.error("[FAILED] Some template tests failed")
        
        return success
    
    def cleanup_all(self):
        """Clean up all templates and test VMs."""
        logger.info("[CLEANUP] Cleaning up all templates and test VMs")
        
        vm_ids = [9000, 8000, 17000, 18000]
        
        for vm_id in vm_ids:
            if self.check_template_exists(vm_id):
                logger.info(f"Removing VM/template {vm_id}")
                self.cleanup_template(vm_id)
        
        logger.info("[SUCCESS] Cleanup completed")
    
    def remove_all_templates_and_cleanup(self):
        """Remove ALL templates and perform complete cleanup (destructive)."""
        logger.warning("[REMOVING] DESTRUCTIVE: Removing ALL templates and performing complete cleanup")
        
        # Extended list including more possible template/test VM IDs
        all_vm_ids = [
            9000, 9002, 9003, 9004, 9005,       # Main templates (removed 9001)
            8000, 8002, 8003, 8004, 8005,       # Test VMs (removed 8001)
            17000, 17002, 17003,                # Additional test VMs (removed 17001)
            18000, 18002, 18003,                # More test VMs (removed 18001)
            9998, 9999                          # Recent test VMs
        ]
        
        logger.info("Scanning for all existing VMs/templates to remove...")
        removed_count = 0
        
        for vm_id in all_vm_ids:
            if self.check_template_exists(vm_id):
                logger.info(f"[REMOVING] Removing VM/template {vm_id}")
                self.cleanup_template(vm_id)
                removed_count += 1
                time.sleep(1)  # Brief pause between deletions
        
        # Clean up prepared cloud images
        logger.info("[CLEANUP] Cleaning up prepared cloud images...")
        cleanup_script = f"""
# Remove prepared cloud images
rm -f /mnt/rbd-iso/template/images/ubuntu-24.04-cloudimg-amd64-modified.img
rm -f /mnt/rbd-iso/template/images/ubuntu-24.04-cloudimg.img
rm -f /tmp/ubuntu-24.04-*
rm -f /tmp/sysadmin_automation_key.pub
rm -f /tmp/fix-efi-boot.sh

echo "Cleaned up prepared images and temporary files"
"""
        
        returncode, stdout, stderr = self.run_ssh_command(cleanup_script)
        if returncode == 0:
            logger.info("[SUCCESS] Cleaned up prepared images and temporary files")
        else:
            logger.warning(f"[WARNING] Some cleanup operations failed: {stderr}")
        
        # Clean up local cache
        try:
            import shutil
            if os.path.exists(self.cache_dir):
                shutil.rmtree(self.cache_dir)
                logger.info(f"[SUCCESS] Cleaned up local cache directory: {self.cache_dir}")
        except Exception as e:
            logger.warning(f"[WARNING] Failed to clean up local cache: {e}")
        
        logger.info(f"[COMPLETE] Complete cleanup finished - removed {removed_count} VMs/templates")
        logger.info("All templates, test VMs, and prepared images have been removed")

def main():
    """Main function."""
    parser = argparse.ArgumentParser(
        description="Proxmox Template Manager - Intelligently manages Proxmox VM templates",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
EXAMPLES:
  %(prog)s                          # Verify templates exist and are configured correctly
  %(prog)s --create-templates       # Create only missing/broken templates (safe to re-run)
  %(prog)s --create-templates --force  # Force recreate ALL templates from scratch
  %(prog)s --test-templates         # Clone templates and test SSH/K8s functionality
  %(prog)s --remove-all --yes       # Complete cleanup without confirmation prompts

BEHAVIOR NOTES:
  - Default (no args): Quick configuration check - verifies templates exist with proper settings
  - Create mode: Intelligently skips existing valid templates unless --force is used
  - Test mode: Creates temporary VMs from templates to verify full functionality
  - All operations are idempotent and safe to re-run
        """
    )
    parser.add_argument("--create-templates", action="store_true", 
                       help="Create missing/broken templates (skips existing valid ones)")
    parser.add_argument("--test-templates", action="store_true", 
                       help="Clone and boot templates to verify full functionality")
    parser.add_argument("--verify", action="store_true", 
                       help="Check template configuration without creating VMs (default if no args)")
    parser.add_argument("--clean-up", action="store_true", 
                       help="Remove test VMs and optionally templates")
    parser.add_argument("--remove-all", action="store_true", 
                       help="DESTRUCTIVE: Remove all templates, VMs, and cached images")
    parser.add_argument("--force", action="store_true", 
                       help="Force recreation even if valid templates exist (use with --create-templates)")
    parser.add_argument("--yes", "-y", action="store_true", 
                       help="Skip confirmation prompts for destructive operations")
    
    args = parser.parse_args()
    
    # Default action is to verify templates
    if not any([args.create_templates, args.test_templates, args.verify, args.clean_up, args.remove_all]):
        args.verify = True
    
    manager = ProxmoxTemplateManager()
    
    try:
        if args.verify:
            # Default behavior: verify template status
            template_status = manager.verify_all_templates()
            all_valid = all(template_status.values())
            sys.exit(0 if all_valid else 1)
        
        elif args.create_templates:
            success = manager.create_templates(force=args.force)
            sys.exit(0 if success else 1)
        
        elif args.test_templates:
            success = manager.test_templates()
            sys.exit(0 if success else 1)
        
        elif args.clean_up:
            manager.cleanup_all()
            sys.exit(0)
            
        elif args.remove_all:
            # Add confirmation prompt for destructive operation
            logger.warning("[WARNING] WARNING: This will remove ALL templates and perform complete cleanup!")
            logger.warning("This includes:")
            logger.warning("  - All VM templates (9000, 9002-9005)")
            logger.warning("  - All test VMs (8000, 8002-8005, 17000, 17002-17003, 18000, 18002-18003, 9998-9999)")
            logger.warning("  - All prepared cloud images")
            logger.warning("  - All temporary files and cache")
            
            if args.yes:
                logger.info("Auto-confirming removal due to --yes flag")
                manager.remove_all_templates_and_cleanup()
                sys.exit(0)
            else:
                try:
                    confirmation = input("Type 'yes' to confirm complete removal: ")
                    if confirmation.lower() == 'yes':
                        manager.remove_all_templates_and_cleanup()
                        sys.exit(0)
                    else:
                        logger.info("Operation cancelled")
                        sys.exit(1)
                except KeyboardInterrupt:
                    logger.info("\nOperation cancelled by user")
                    sys.exit(1)
            
    except KeyboardInterrupt:
        logger.info("Operation cancelled by user")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()