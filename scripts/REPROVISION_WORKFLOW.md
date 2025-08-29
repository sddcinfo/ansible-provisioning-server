# Enhanced Proxmox Reprovision Workflow

This document describes the improved end-to-end reprovision workflow for Proxmox nodes.

## Overview

The enhanced workflow provides:
1. **Coordinated reprovision triggering** - Starts monitoring before triggering reprovision
2. **Real-time status tracking** - Updates registered-nodes.json with reprovision progress  
3. **Automatic cluster formation** - Triggers cluster setup when all nodes are ready
4. **Comprehensive logging** - Detailed logs of the entire process

## Components

### 1. Enhanced Reprovision Monitor (`enhanced-reprovision-monitor.py`)
- Monitors `/var/www/html/data/registered-nodes.json` for reprovision status changes
- Checks node accessibility and Proxmox service readiness
- Automatically triggers cluster formation when nodes are ready
- Handles timeouts and error conditions

### 2. Updated Web Interface (`index.php`)
- New `update_registered_node_status()` function
- Updated `handle_reprovision_request()` to track reprovision status
- Sets reprovision status to 'in_progress' when reprovision is triggered

### 3. Coordinated Workflow Script (`coordinated-proxmox-reprovision.py`)
- Orchestrates the complete reprovision process
- Starts monitoring before triggering reprovision
- Waits for completion with configurable timeout
- Handles interruption signals gracefully

## Usage

### Basic Usage
```bash
# Reprovision all configured nodes (uses --all flag automatically)
python3 coordinated-proxmox-reprovision.py

# Reprovision with custom timeout (90 minutes)
python3 coordinated-proxmox-reprovision.py --timeout 90

# Reprovision specific nodes (uses --nodes flag with comma-separated list)
python3 coordinated-proxmox-reprovision.py node1 node2
```

### Manual Monitor Usage
```bash
# Run monitor independently  
python3 enhanced-reprovision-monitor.py

# Use custom registered-nodes.json path
python3 enhanced-reprovision-monitor.py /path/to/nodes.json
```

## Workflow Process

1. **Start Monitor** - The coordinated script starts the enhanced monitor in background
2. **Pre-Status Check** - Logs current status of all nodes before triggering reprovision
3. **Trigger Reprovision** - Calls `reboot-nodes-for-reprovision.py --all` (automatically confirms with 'y')
4. **Web API Calls** - Reprovision script makes web requests to `index.php?action=reprovision&mac=...` for each node
5. **Status Update** - `index.php` calls `update_registered_node_status()` to mark nodes as 'in_progress' in registered-nodes.json
6. **Post-Status Check** - Coordinated script verifies status was updated and shows which nodes are now 'in_progress'
7. **Active Monitoring** - Script watches registered-nodes.json every 30 seconds and logs detailed status changes
8. **Node Provisioning** - Nodes reboot, install Proxmox, and run `proxmox-post-install.sh`
9. **Registration Update** - `proxmox-post-install.sh` calls `register-node.php` to update node status to 'post-install-complete'
10. **Completion Detection** - Monitor detects completed installs and updates status to 'completed'  
11. **Cluster Formation** - When ALL nodes complete, monitor automatically triggers `proxmox-form-cluster.py`
12. **Final Status** - Monitor marks nodes as 'clustered' when cluster formation succeeds
13. **Workflow Complete** - Coordinated script detects all nodes 'clustered' and declares success

## Data Structure

The `registered-nodes.json` file uses this enhanced structure:

```json
{
  "nodes": {
    "aa:bb:cc:dd:ee:ff": {
      "hostname": "node1", 
      "ip": "10.0.0.101",
      "mac": "aa:bb:cc:dd:ee:ff",
      "type": "proxmox",
      "status": "registered",
      "registered_at": "2024-01-01 12:00:00",
      "reprovision_status": "in_progress",
      "reprovision_started": "2024-01-01T13:00:00+00:00",
      "last_update": "2024-01-01 13:15:00"
    }
  }
}
```

## Status Values

- **reprovision_status**: 
  - `in_progress` - Reprovision triggered, node rebooting/installing
  - `completed` - Node accessible and Proxmox services ready
  - `clustered` - Node added to cluster
  - `timeout` - Reprovision took too long
  - `error` - Reprovision failed

## Configuration

### Timeouts
- **Node reprovision timeout**: 45 minutes (configurable in monitor script)
- **Overall workflow timeout**: 60 minutes (configurable via --timeout)
- **SSH connectivity checks**: 10 second timeout

### Monitoring
- **Check interval**: 30 seconds
- **Status reporting**: Every 5 minutes during long operations
- **Log locations**: `/var/log/` or `~/` if no permission

## Troubleshooting

### Monitor Issues
- Check log files for detailed error messages
- Ensure registered-nodes.json is accessible and writable
- Verify SSH keys are properly configured for node access

### Workflow Issues  
- Use `--timeout` to extend wait time for slow provisioning
- Check individual script permissions and dependencies
- Monitor web server logs for PHP errors during reprovision

### Cluster Formation Issues
- Ensure proxmox-form-cluster.py works independently
- Check network connectivity between nodes
- Verify Proxmox services are fully started

## Integration

This enhanced workflow is backward compatible with existing scripts:
- `reboot-nodes-for-reprovision.py` continues to work unchanged
- Web interface maintains existing functionality
- Added status tracking doesn't break existing workflows

The coordinated script can be integrated into larger automation systems or called manually as needed.