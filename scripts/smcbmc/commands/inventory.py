"""Inventory commands: system, network, storage, memory, all."""

import click

from smcbmc import (
    REDFISH_SYSTEMS,
    REDFISH_ETHERNET_INTERFACES,
    REDFISH_MANAGER_ETHERNET,
    REDFISH_STORAGE,
    REDFISH_MEMORY,
)
from smcbmc.cli import pass_context, run_on_nodes


@click.group()
def inventory():
    """Hardware inventory commands."""
    pass


@inventory.command()
@pass_context
def system(nctx):
    """Get system information."""
    def _op(client, node):
        return client.get(REDFISH_SYSTEMS)
    run_on_nodes(nctx, _op, label="inventory system")


@inventory.command()
@pass_context
def network(nctx):
    """Get network interface information."""
    def _op(client, node):
        result = {}

        # System ethernet interfaces
        try:
            data = client.get(REDFISH_ETHERNET_INTERFACES)
            members = data.get("Members", [])
            ifaces = []
            for member in members:
                uri = member.get("@odata.id", "")
                if uri:
                    iface = client.get(uri)
                    ifaces.append({
                        "Id": iface.get("Id", ""),
                        "Name": iface.get("Name", ""),
                        "MACAddress": iface.get("MACAddress", ""),
                        "SpeedMbps": iface.get("SpeedMbps", ""),
                        "Status": iface.get("Status", {}),
                    })
            result["SystemInterfaces"] = ifaces
        except Exception:
            result["SystemInterfaces"] = []

        # Manager (BMC) ethernet interfaces
        try:
            data = client.get(REDFISH_MANAGER_ETHERNET)
            members = data.get("Members", [])
            mgmt_ifaces = []
            for member in members:
                uri = member.get("@odata.id", "")
                if uri:
                    iface = client.get(uri)
                    mgmt_ifaces.append({
                        "Id": iface.get("Id", ""),
                        "Name": iface.get("Name", ""),
                        "MACAddress": iface.get("MACAddress", ""),
                        "IPv4Addresses": iface.get("IPv4Addresses", []),
                        "HostName": iface.get("HostName", ""),
                    })
            result["ManagerInterfaces"] = mgmt_ifaces
        except Exception:
            result["ManagerInterfaces"] = []

        return result

    run_on_nodes(nctx, _op, label="inventory network")


@inventory.command()
@pass_context
def storage(nctx):
    """Get storage information."""
    def _op(client, node):
        data = client.get(REDFISH_STORAGE)
        members = data.get("Members", [])
        controllers = []
        for member in members:
            uri = member.get("@odata.id", "")
            if uri:
                try:
                    ctrl = client.get(uri)
                    drives = []
                    for drive_ref in ctrl.get("Drives", []):
                        drive_uri = drive_ref.get("@odata.id", "")
                        if drive_uri:
                            try:
                                drive = client.get(drive_uri)
                                drives.append({
                                    "Name": drive.get("Name", ""),
                                    "CapacityBytes": drive.get("CapacityBytes", 0),
                                    "MediaType": drive.get("MediaType", ""),
                                    "Model": drive.get("Model", ""),
                                    "SerialNumber": drive.get("SerialNumber", ""),
                                    "Protocol": drive.get("Protocol", ""),
                                    "Status": drive.get("Status", {}),
                                })
                            except Exception:
                                pass
                    controllers.append({
                        "Id": ctrl.get("Id", ""),
                        "Name": ctrl.get("Name", ""),
                        "Drives": drives,
                    })
                except Exception:
                    pass
        return {"StorageControllers": controllers}

    run_on_nodes(nctx, _op, label="inventory storage")


@inventory.command()
@pass_context
def memory(nctx):
    """Get memory information."""
    def _op(client, node):
        data = client.get(REDFISH_MEMORY)
        members = data.get("Members", [])
        dimms = []
        for member in members:
            uri = member.get("@odata.id", "")
            if uri:
                try:
                    dimm = client.get(uri)
                    dimms.append({
                        "Id": dimm.get("Id", ""),
                        "Name": dimm.get("Name", ""),
                        "CapacityMiB": dimm.get("CapacityMiB", 0),
                        "MemoryDeviceType": dimm.get("MemoryDeviceType", ""),
                        "OperatingSpeedMhz": dimm.get("OperatingSpeedMhz", ""),
                        "Manufacturer": dimm.get("Manufacturer", ""),
                        "SerialNumber": dimm.get("SerialNumber", ""),
                        "Status": dimm.get("Status", {}),
                    })
                except Exception:
                    pass
        return {"MemoryDIMMs": dimms}

    run_on_nodes(nctx, _op, label="inventory memory")


@inventory.command(name="all")
@pass_context
def all_inventory(nctx):
    """Get all inventory information."""
    def _op(client, node):
        result = {}

        # System info
        result["System"] = client.get(REDFISH_SYSTEMS)

        # Network - just the manager interfaces for summary
        try:
            mgr_data = client.get(REDFISH_MANAGER_ETHERNET)
            members = mgr_data.get("Members", [])
            mgmt_ifaces = []
            for member in members:
                uri = member.get("@odata.id", "")
                if uri:
                    iface = client.get(uri)
                    mgmt_ifaces.append({
                        "Id": iface.get("Id", ""),
                        "MACAddress": iface.get("MACAddress", ""),
                        "HostName": iface.get("HostName", ""),
                    })
            result["ManagerInterfaces"] = mgmt_ifaces
        except Exception:
            result["ManagerInterfaces"] = []

        # Storage summary
        try:
            storage_data = client.get(REDFISH_STORAGE)
            result["StorageCount"] = len(storage_data.get("Members", []))
        except Exception:
            result["StorageCount"] = "N/A"

        # Memory summary (from system info)
        mem_summary = result["System"].get("MemorySummary", {})
        result["TotalMemoryGiB"] = mem_summary.get("TotalSystemMemoryGiB", "N/A")

        return result

    run_on_nodes(nctx, _op, label="inventory all")
