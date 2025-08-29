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

1. **Start Monitor** - The coordinated script starts the enhanced monitor
2. **Trigger Reprovision** - Calls `reboot-nodes-for-reprovision.py --all` (automatically confirms with 'y') which makes web requests to index.php
3. **Status Tracking** - index.php updates registered-nodes.json with 'in_progress' status
4. **Node Monitoring** - Monitor checks nodes for accessibility and Proxmox readiness
5. **Status Updates** - Nodes marked as 'completed' when Proxmox services are ready
6. **Cluster Formation** - When ALL nodes are ready (no nodes still in progress), automatically runs `proxmox-form-cluster.py`
7. **Completion** - All nodes marked as 'clustered' and process completes

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