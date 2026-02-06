"""Firmware commands: version, product-key management."""

import click

from smcbmc import REDFISH_FIRMWARE_INVENTORY, REDFISH_MANAGER_ETHERNET, REDFISH_MANAGERS
from smcbmc.cli import pass_context, run_on_nodes


@click.group()
def firmware():
    """Firmware and product key commands."""
    pass


@firmware.command()
@pass_context
def version(nctx):
    """Get firmware version information."""
    SMC_FW_INVENTORY = "/redfish/v1/UpdateService/SmcFirmwareInventory"

    def _op(client, node):
        # Try Supermicro OEM endpoint first, fall back to standard
        fw_path = SMC_FW_INVENTORY
        try:
            data = client.get(fw_path)
        except Exception:
            data = client.get(REDFISH_FIRMWARE_INVENTORY)

        members = data.get("Members", [])
        versions = []
        for member in members:
            uri = member.get("@odata.id", "")
            if uri:
                try:
                    fw = client.get(uri)
                    versions.append({
                        "Id": fw.get("Id", ""),
                        "Name": fw.get("Name", ""),
                        "Version": fw.get("Version", ""),
                        "Updateable": fw.get("Updateable", False),
                    })
                except Exception:
                    pass

        # Also get BMC firmware version from manager endpoint
        try:
            mgr = client.get(REDFISH_MANAGERS)
            versions.append({
                "Id": "BMC-Manager",
                "Name": "BMC Manager",
                "Version": mgr.get("FirmwareVersion", "N/A"),
                "Updateable": False,
            })
        except Exception:
            pass

        return {"FirmwareInventory": versions}
    run_on_nodes(nctx, _op, label="firmware version")


@firmware.group(name="product-key")
def product_key():
    """Product key management commands."""
    pass


@product_key.command()
@pass_context
def query(nctx):
    """Query current product keys."""
    from smcbmc.tools.sum import query_product_key

    def _op(client, node):
        success, stdout, stderr = query_product_key(
            node.console_ip, nctx.username, nctx.password
        )
        output = (stdout + stderr).strip()
        if success:
            return {"product_keys": output}
        if "No product key" in output or "Number of product keys: 0" in output:
            return {"product_keys": "No product keys installed"}
        raise Exception(f"Query failed: {output}")

    run_on_nodes(nctx, _op, label="product-key query")


@product_key.command()
@click.option("--key-type", default="oob",
              help="Key type: 'oob' for SFT-OOB-LIC, or a SKU like 'SFT-DCMS-SINGLE'.")
@pass_context
def generate(nctx, key_type):
    """Generate a product key (display only, does not activate)."""
    from smcbmc.tools.sum import generate_product_key

    def _op(client, node):
        # Get BMC MAC address via Redfish
        mgr_data = client.get(REDFISH_MANAGER_ETHERNET)
        members = mgr_data.get("Members", [])
        if not members:
            raise Exception("No BMC ethernet interfaces found")

        iface_uri = members[0].get("@odata.id", "")
        iface = client.get(iface_uri)
        mac = iface.get("MACAddress", "")
        if not mac:
            raise Exception("Could not retrieve BMC MAC address")

        key = generate_product_key(mac, key_type)
        return {
            "mac": mac,
            "key_type": key_type,
            "key": key,
        }

    run_on_nodes(nctx, _op, label="product-key generate")


@product_key.command()
@click.option("--key-type", default="oob",
              help="Key type: 'oob' for SFT-OOB-LIC, or a SKU like 'SFT-DCMS-SINGLE'.")
@pass_context
def activate(nctx, key_type):
    """Generate and activate a product key."""
    from smcbmc.tools.sum import generate_product_key, activate_product_key

    def _op(client, node):
        # Get BMC MAC
        mgr_data = client.get(REDFISH_MANAGER_ETHERNET)
        members = mgr_data.get("Members", [])
        if not members:
            raise Exception("No BMC ethernet interfaces found")

        iface_uri = members[0].get("@odata.id", "")
        iface = client.get(iface_uri)
        mac = iface.get("MACAddress", "")
        if not mac:
            raise Exception("Could not retrieve BMC MAC address")

        key = generate_product_key(mac, key_type)

        success, stdout, stderr = activate_product_key(
            node.console_ip, nctx.username, nctx.password, key
        )
        output = (stdout + stderr).strip()
        if success:
            return {"mac": mac, "key": key, "status": "activated"}
        raise Exception(f"Activation failed: {output}")

    run_on_nodes(nctx, _op, label="product-key activate")


@product_key.command()
@pass_context
def clear(nctx):
    """Clear all product keys."""
    from smcbmc.tools.sum import query_product_key, clear_product_key

    def _op(client, node):
        ip = node.console_ip
        user = nctx.username
        pwd = nctx.password

        # Query to find how many keys exist
        success, stdout, stderr = query_product_key(ip, user, pwd)
        output = (stdout + stderr).strip()
        if not success:
            if "No product key" in output or "Number of product keys: 0" in output:
                return {"message": "No product keys to clear"}
            raise Exception(f"Could not query keys: {output}")

        # Parse key count
        key_count = 0
        for line in stdout.strip().split("\n"):
            if "Number of product keys:" in line:
                key_count = int(line.split(":")[1].strip())
                break

        if key_count == 0:
            return {"message": "No product keys to clear"}

        # Clear from highest index to lowest
        cleared = 0
        for idx in range(key_count, 0, -1):
            s, so, se = clear_product_key(ip, user, pwd, idx)
            if s:
                cleared += 1

        return {
            "message": f"Cleared {cleared}/{key_count} product key(s)",
            "cleared": cleared,
            "total": key_count,
        }

    run_on_nodes(nctx, _op, label="product-key clear")


@product_key.command()
@click.option("--key-type", default="oob",
              help="Key type: 'oob' for SFT-OOB-LIC, or a SKU like 'SFT-DCMS-SINGLE'.")
@pass_context
def full(nctx, key_type):
    """Clear existing keys, generate new ones, and activate them."""
    from smcbmc.tools.sum import (
        query_product_key, clear_product_key,
        generate_product_key, activate_product_key,
    )

    def _op(client, node):
        ip = node.console_ip
        user = nctx.username
        pwd = nctx.password

        # Step 1: Clear existing keys
        success, stdout, stderr = query_product_key(ip, user, pwd)
        output = (stdout + stderr).strip()
        key_count = 0
        if success:
            for line in stdout.strip().split("\n"):
                if "Number of product keys:" in line:
                    key_count = int(line.split(":")[1].strip())
                    break

        cleared = 0
        for idx in range(key_count, 0, -1):
            s, _, _ = clear_product_key(ip, user, pwd, idx)
            if s:
                cleared += 1

        # Step 2: Get BMC MAC and generate key
        mgr_data = client.get(REDFISH_MANAGER_ETHERNET)
        members = mgr_data.get("Members", [])
        if not members:
            raise Exception("No BMC ethernet interfaces found")
        iface_uri = members[0].get("@odata.id", "")
        iface = client.get(iface_uri)
        mac = iface.get("MACAddress", "")
        if not mac:
            raise Exception("Could not retrieve BMC MAC address")

        key = generate_product_key(mac, key_type)

        # Step 3: Activate
        success, stdout, stderr = activate_product_key(ip, user, pwd, key)
        output = (stdout + stderr).strip()
        if success:
            return {
                "mac": mac,
                "key": key,
                "keys_cleared": cleared,
                "status": "activated",
            }
        raise Exception(f"Activation failed: {output}")

    run_on_nodes(nctx, _op, label="product-key full")
