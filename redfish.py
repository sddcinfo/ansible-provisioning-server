#!/usr/bin/env python3
import argparse
import json
import os
import ssl
import sys
from urllib import request, error
import base64

# --- Configuration ---
NODES_FILE = "/home/sysadmin/ansible-provisioning-server/nodes.json"
CREDENTIALS_FILE = os.path.expanduser("~/.redfish_credentials")

def get_node_ip(node_name):
    """Finds the IP address for a given node hostname."""
    try:
        with open(NODES_FILE) as f:
            nodes = json.load(f).get("console_nodes", [])
        for node in nodes:
            if node.get("hostname") == node_name:
                return node.get("ip")
    except FileNotFoundError:
        print(f"Error: Nodes file not found at {NODES_FILE}", file=sys.stderr)
        sys.exit(1)
    except json.JSONDecodeError:
        print(f"Error: Could not decode JSON from {NODES_FILE}", file=sys.stderr)
        sys.exit(1)
    return None

def get_redfish_credentials():
    """Reads and decodes the Basic Auth string from the credentials file."""
    try:
        with open(CREDENTIALS_FILE, 'r') as f:
            content = f.read().strip()
        if content.startswith('REDFISH_AUTH="') and content.endswith('"'):
            user_pass = content.split('"')[1]
            return base64.b64encode(user_pass.encode('utf-8')).decode('utf-8')
    except FileNotFoundError:
        print(f"Error: Credentials file not found at {CREDENTIALS_FILE}", file=sys.stderr)
        sys.exit(1)
    
    print(f"Error: Could not parse REDFISH_AUTH in {CREDENTIALS_FILE}", file=sys.stderr)
    sys.exit(1)

def format_output(data, as_json=False, name_filter=None):
    """Formats the output as either human-readable or JSON."""
    # If a filter is applied to sensor data, process it first.
    if name_filter and ('Temperatures' in data or 'Fans' in data):
        filtered_data = {}
        name_filter_lower = name_filter.lower()
        
        temps = [t for t in data.get('Temperatures', []) if name_filter_lower in t.get('Name', '').lower()]
        if temps:
            filtered_data['Temperatures'] = temps
            
        fans = [f for f in data.get('Fans', []) if name_filter_lower in f.get('FanName', '').lower()]
        if fans:
            filtered_data['Fans'] = fans
        
        data = filtered_data # Replace original data with filtered data

    if as_json:
        return json.dumps(data, indent=4)
    
    if not data:
        return "No matching sensors found."

    # Simple human-readable format for success messages
    if "Success" in data:
        return f"Success: {data['Success']['Message']}"
    
    # Simple human-readable format for sensor data
    if "Temperatures" in data or "Fans" in data:
        output = ["Sensor Status:"]
        for temp in data.get('Temperatures', []):
            output.append(f"  - Temp: {temp.get('Name', 'N/A')}: {temp.get('ReadingCelsius', 'N/A')} C (Status: {temp.get('Status', {}).get('Health', 'N/A')})")
        for fan in data.get('Fans', []):
            output.append(f"  - Fan: {fan.get('FanName', 'N/A')}: {fan.get('Reading', 'N/A')} {fan.get('ReadingUnits', '')} (Status: {fan.get('Status', {}).get('Health', 'N/A')})")
        return "\n".join(output)
        
    # Default to pretty-printed JSON for other cases
    return json.dumps(data, indent=4)

def main():
    """Main function to execute a simple Redfish command."""
    parser = argparse.ArgumentParser(description="A simplified script to send Redfish commands.")
    parser.add_argument("node", help="The hostname of the node (e.g., console-node2).")
    
    subparsers = parser.add_subparsers(dest="action", required=True, metavar='action')

    # Sensor parser
    parser_sensors = subparsers.add_parser("sensors", help="Get sensor data.")
    parser_sensors.add_argument("--filter", help="Filter sensors by name (case-insensitive substring).")
    parser_sensors.add_argument("--json", action="store_true", help="Output the result in JSON format.")
    
    # Other actions
    parser_boot = subparsers.add_parser("set-boot-to-bios", help="Set the server to boot into BIOS setup on next restart.")
    parser_boot.add_argument("--json", action="store_true", help="Output the result in JSON format.")
    
    parser_power_on = subparsers.add_parser("power-on", help="Power on the server.")
    parser_power_on.add_argument("--json", action="store_true", help="Output the result in JSON format.")

    parser_power_off = subparsers.add_parser("power-off", help="Gracefully shut down the server.")
    parser_power_off.add_argument("--json", action="store_true", help="Output the result in JSON format.")

    parser_power_reboot = subparsers.add_parser("power-reboot", help="Gracefully restart the server.")
    parser_power_reboot.add_argument("--json", action="store_true", help="Output the result in JSON format.")

    parser_power_cycle = subparsers.add_parser("power-cycle", help="Force restart the server.")
    parser_power_cycle.add_argument("--json", action="store_true", help="Output the result in JSON format.")

    args = parser.parse_args()

    node_ip = get_node_ip(args.node)
    if not node_ip:
        print(f"Error: IP address for node '{args.node}' not found.", file=sys.stderr)
        sys.exit(1)

    # Define API actions
    actions = {
        "sensors": {
            "path": "/redfish/v1/Chassis/1/Thermal",
            "method": "GET",
            "payload": None
        },
        "set-boot-to-bios": {
            "path": "/redfish/v1/Systems/1",
            "method": "PATCH",
            "payload": {"Boot": {"BootSourceOverrideTarget": "BiosSetup"}}
        },
        "power-on": {
            "path": "/redfish/v1/Systems/1/Actions/ComputerSystem.Reset",
            "method": "POST",
            "payload": {"ResetType": "On"}
        },
        "power-off": {
            "path": "/redfish/v1/Systems/1/Actions/ComputerSystem.Reset",
            "method": "POST",
            "payload": {"ResetType": "GracefulShutdown"}
        },
        "power-reboot": {
            "path": "/redfish/v1/Systems/1/Actions/ComputerSystem.Reset",
            "method": "POST",
            "payload": {"ResetType": "GracefulRestart"}
        },
        "power-cycle": {
            "path": "/redfish/v1/Systems/1/Actions/ComputerSystem.Reset",
            "method": "POST",
            "payload": {"ResetType": "ForceRestart"}
        }
    }
    
    action_details = actions[args.action]
    url = f"https://{node_ip}{action_details['path']}"
    auth_header = f"Basic {get_redfish_credentials()}"
    
    headers = {"Authorization": auth_header}
    payload = None
    if action_details['payload']:
        headers['Content-Type'] = 'application/json'
        payload = json.dumps(action_details['payload']).encode('utf-8')

    req = request.Request(url, headers=headers, method=action_details['method'], data=payload)
    
    # Ignore SSL certificate validation
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    try:
        with request.urlopen(req, context=ctx) as response:
            response_body = response.read().decode('utf-8')
            data = json.loads(response_body) if response_body else {"Success": {"Message": f"Action '{args.action}' completed with status {response.getcode()}."}}
            
            name_filter = getattr(args, 'filter', None)
            print(format_output(data, args.json, name_filter))

    except error.HTTPError as e:
        print(f"Error: HTTP request failed for node '{args.node}' with status {e.code}.", file=sys.stderr)
        try:
            body = e.read().decode('utf-8')
            print(f"Response body:\n{body}", file=sys.stderr)
        except Exception:
            pass # Ignore if reading body fails
        sys.exit(1)
    except Exception as e:
        print(f"An unexpected error occurred for node '{args.node}': {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
