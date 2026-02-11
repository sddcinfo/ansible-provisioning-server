#!/usr/bin/env python3
import argparse
import json
import ssl
import sys
import time
from urllib import request, error
import base64

def get_auth_header(username, password):
    user_pass = f"{username}:{password}"
    return "Basic " + base64.b64encode(user_pass.encode('utf-8')).decode('utf-8')

def make_request(url, method="GET", headers=None, data=None):
    if headers is None:
        headers = {}
    
    if data:
        headers['Content-Type'] = 'application/json'
        json_data = json.dumps(data).encode('utf-8')
    else:
        json_data = None

    req = request.Request(url, headers=headers, method=method, data=json_data)
    
    # Ignore SSL certificate validation
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    try:
        with request.urlopen(req, context=ctx) as response:
            response_body = response.read().decode('utf-8')
            if response_body:
                return json.loads(response_body)
            return {"status": response.getcode()}
    except error.HTTPError as e:
        print(f"Error: HTTP {e.code} for {url}", file=sys.stderr)
        try:
            print(e.read().decode('utf-8'), file=sys.stderr)
        except:
            pass
        raise
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        raise

def find_cd_media_url(base_url, headers):
    """Finds the Virtual Media resource URL for CD/DVD, supporting standard and proprietary paths."""
    
    # 1. Try Standard Collection
    url = f"{base_url}/redfish/v1/Managers/1/VirtualMedia"
    print(f"Listing Virtual Media from: {url}")
    
    try:
        data = make_request(url, headers=headers)
        members = data.get("Members", [])
        
        for member in members:
            member_url = member["@odata.id"]
            if not member_url.startswith("http"):
                 member_full_url = f"{base_url}{member_url}"
            else:
                 member_full_url = member_url
                 
            print(f"Checking media: {member_full_url}")
            media_data = make_request(member_full_url, headers=headers)
            
            # Check if it supports CD/DVD
            media_types = media_data.get("MediaTypes", [])
            if "CD" in media_types or "DVD" in media_types or "CD" in media_data.get("Id", "") or "VM1" in media_data.get("Id", ""):
                 print(f"Found Standard CD/DVD Media resource: {member_full_url}")
                 return member_full_url, "Standard"
    except Exception as e:
        print(f"Standard VirtualMedia lookup failed or empty: {e}")

    # 2. Try Supermicro Proprietary CfgCD
    print("Checking for Supermicro Proprietary VM1/CfgCD...")
    prop_url = f"{base_url}/redfish/v1/Managers/1/VM1/CfgCD"
    try:
        data = make_request(prop_url, headers=headers)
        print(f"Found CfgCD. Data: {json.dumps(data)}")
        if "Actions" in data and "#IsoConfig.Mount" in data["Actions"]:
             target = data["Actions"]["#IsoConfig.Mount"]["target"]
             # target might be relative
             if not target.startswith("http"):
                 # Handle cases where target is just the path
                 if target.startswith("/"):
                      target = f"{base_url}{target}"
                 else:
                      # If it's something weird, try to construct it.
                      # But usually it is /redfish/...
                      target = f"{base_url}/{target}"
             
             print(f"Found Proprietary Mount Action: {target}")
             return target, "Proprietary"
    except Exception as e:
        print(f"Proprietary CfgCD lookup failed: {e}")

    raise Exception("No Virtual Media resource found for CD/DVD (Standard or Proprietary)")

def mount_iso(bmc_ip, username, password, iso_url):
    base_url = f"https://{bmc_ip}"
    headers = {"Authorization": get_auth_header(username, password)}

    print(f"--- Connecting to {bmc_ip} ---")
    
    # 1. Find Virtual Media
    try:
        media_url, mode = find_cd_media_url(base_url, headers)
    except Exception as e:
        print(f"Failed to find Virtual Media: {e}")
        sys.exit(1)

    # 2. Insert Media
    print(f"Mounting ISO from: {iso_url}")
    
    if mode == "Standard":
        insert_action_url = f"{media_url}/Actions/VirtualMedia.InsertMedia"
        payload = {
            "Image": iso_url,
            "Inserted": True,
            "WriteProtected": True
        }
    else:
        # Proprietary Mode (IsoConfig.Mount)
        insert_action_url = media_url # media_url IS the action target
        
        # FIRST: Try to unmount existing if any
        print("Attempting to unmount existing media first...")
        unmount_url = insert_action_url.replace(".Mount", ".UnMount")
        try:
            make_request(unmount_url, method="POST", headers=headers, data={})
            print("Unmount command sent.")
            time.sleep(2) # Give it a moment
        except:
            print("Unmount failed or not needed.")
        
        from urllib.parse import urlparse
        parsed = urlparse(iso_url)
        
        payload = {
            "Host": parsed.hostname,
            "Path": parsed.path,
            "Protocol": "HTTP",
            "Username": "",
            "Password": ""
        }
        print(f"Using Proprietary Payload: {payload}")

    print(f"Action URL: {insert_action_url}")
    
    try:
        make_request(insert_action_url, method="POST", headers=headers, data=payload)
        print("ISO Mount Request Sent Successfully.")
    except Exception as e:
        # ... retry logic ...
        print(f"Failed to mount ISO: {e}")
        if mode == "Proprietary":
             print("Retrying with 'Image' payload just in case...")
             payload = {"Image": iso_url}
             try:
                make_request(insert_action_url, method="POST", headers=headers, data=payload)
                print("ISO Mount Request Sent Successfully (Retry).")
             except:
                print("Retry failed.")
                sys.exit(1)
        else:
             sys.exit(1)
        
    # 3. Set Boot Order
    print("Setting Boot Order to CD/DVD (OneTime)...")
    system_url = f"{base_url}/redfish/v1/Systems/1"
    boot_payload = {
        "Boot": {
            "BootSourceOverrideEnabled": "Once",
            "BootSourceOverrideTarget": "CD/DVD"
        }
    }
    
    try:
        make_request(system_url, method="PATCH", headers=headers, data=boot_payload)
        print("Boot order set successfully.")
    except Exception as e:
        print(f"Failed to set boot order: {e}")
        sys.exit(1)

    # 4. Power Cycle
    print("Power Cycling server...")
    reset_url = f"{base_url}/redfish/v1/Systems/1/Actions/ComputerSystem.Reset"
    reset_payload = {
        "ResetType": "ForceRestart"  # or ForceOff then On if needed
    }
    
    try:
        make_request(reset_url, method="POST", headers=headers, data=reset_payload)
        print("Server reboot command sent.")
    except Exception as e:
        print(f"Failed to reboot server: {e}")
        sys.exit(1)

    print("\nOperation Complete. The server should boot from the ISO shortly.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Mount ISO via Redfish HTTP and reboot.")
    parser.add_argument("bmc_ip", help="IP address of the BMC")
    parser.add_argument("iso_url", help="HTTP URL of the ISO image (e.g., http://10.10.1.1/image.iso)")
    parser.add_argument("--user", default="admin", help="BMC Username (default: admin)")
    parser.add_argument("--password", default="blocked1", help="BMC Password (default: blocked1)")
    parser.add_argument("--check-only", action="store_true", help="Only check connection and list media resources")

    args = parser.parse_args()

    if args.check_only:
        base_url = f"https://{args.bmc_ip}"
        headers = {"Authorization": get_auth_header(args.user, args.password)}
        try:
            url, mode = find_cd_media_url(base_url, headers)
            print(f"Check successful: Virtual Media resource found. Mode: {mode}, URL: {url}")
        except Exception as e:
            print(f"Check failed: {e}")
    else:
        mount_iso(args.bmc_ip, args.user, args.password, args.iso_url)
