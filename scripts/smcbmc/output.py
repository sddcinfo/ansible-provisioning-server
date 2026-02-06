"""Output formatting for smcbmc - human-readable and JSON modes."""

import json
import sys

import click


def format_json(data):
    """Format data as pretty-printed JSON."""
    return json.dumps(data, indent=2)


def print_result(data, json_mode=False, file=sys.stdout):
    """Print a result dict in human-readable or JSON format."""
    if json_mode:
        click.echo(format_json(data), file=file)
    else:
        click.echo(_format_human(data), file=file)


def print_error(message, json_mode=False):
    """Print an error message, respecting --json flag."""
    if json_mode:
        click.echo(format_json({"error": message}), err=True)
    else:
        click.echo(f"Error: {message}", err=True)


def print_node_header(node_name, json_mode=False):
    """Print a header line for multi-node output."""
    if not json_mode:
        click.echo(f"\n--- {node_name} ---")


def print_multi_node_results(results, json_mode=False):
    """Print results from a multi-node operation.

    Args:
        results: list of dicts with keys: node, success, data/error
        json_mode: output as JSON if True
    """
    if json_mode:
        click.echo(format_json(results))
        return

    for result in results:
        node = result.get("node", "unknown")
        click.echo(f"\n--- {node} ---")
        if result.get("success"):
            click.echo(_format_human(result.get("data", {})))
        else:
            click.echo(f"Error: {result.get('error', 'unknown error')}", err=True)


def _format_human(data):
    """Convert a data dict to human-readable text."""
    if not data:
        return "No data."

    if "Success" in data:
        return data["Success"].get("Message", "Success")

    # Sensor data (Thermal endpoint)
    if "Temperatures" in data or "Fans" in data:
        return _format_sensors(data)

    # System info (check before boot - system endpoint has both Boot and Model)
    if "Model" in data and "Manufacturer" in data:
        return _format_system_info(data)

    # Boot options (only when Boot is the primary data, not part of system info)
    if "Boot" in data and isinstance(data["Boot"], dict):
        return _format_boot(data)

    # Firmware inventory
    if "FirmwareInventory" in data:
        return _format_firmware(data)

    # Generic: pretty print key-value pairs
    return _format_generic(data)


def _format_sensors(data):
    lines = []
    for temp in data.get("Temperatures", []):
        name = temp.get("Name", "N/A")
        reading = temp.get("ReadingCelsius", "N/A")
        health = temp.get("Status", {}).get("Health", "N/A")
        lines.append(f"  Temp: {name}: {reading} C (Status: {health})")
    for fan in data.get("Fans", []):
        name = fan.get("FanName", fan.get("Name", "N/A"))
        reading = fan.get("Reading", "N/A")
        units = fan.get("ReadingUnits", "")
        health = fan.get("Status", {}).get("Health", "N/A")
        lines.append(f"  Fan: {name}: {reading} {units} (Status: {health})")
    return "\n".join(lines) if lines else "No sensor data."


def _format_boot(data):
    boot = data["Boot"]
    lines = ["Boot Options:"]
    lines.append(f"  Boot Source Override Enabled: {boot.get('BootSourceOverrideEnabled', 'N/A')}")
    lines.append(f"  Boot Source Override Target: {boot.get('BootSourceOverrideTarget', 'N/A')}")
    boot_order = boot.get("BootOrder", [])
    if boot_order:
        lines.append("  Boot Order:")
        for i, device in enumerate(boot_order):
            lines.append(f"    {i + 1}. {device}")
    return "\n".join(lines)


def _format_system_info(data):
    lines = [
        f"  Manufacturer: {data.get('Manufacturer', 'N/A')}",
        f"  Model: {data.get('Model', 'N/A')}",
        f"  Serial: {data.get('SerialNumber', 'N/A')}",
        f"  SKU: {data.get('SKU', 'N/A')}",
        f"  BIOS Version: {data.get('BiosVersion', 'N/A')}",
        f"  Power State: {data.get('PowerState', 'N/A')}",
        f"  UUID: {data.get('UUID', 'N/A')}",
    ]
    proc = data.get("ProcessorSummary", {})
    if proc:
        lines.append(f"  Processors: {proc.get('Count', 'N/A')} x {proc.get('Model', 'N/A')}")
    mem = data.get("MemorySummary", {})
    if mem:
        lines.append(f"  Total Memory: {mem.get('TotalSystemMemoryGiB', 'N/A')} GiB")
    return "\n".join(lines)


def _format_firmware(data):
    lines = []
    for fw in data.get("FirmwareInventory", []):
        name = fw.get("Name", fw.get("Id", "N/A"))
        version = fw.get("Version", "N/A")
        lines.append(f"  {name}: {version}")
    return "\n".join(lines) if lines else "No firmware information."


def _format_generic(data):
    """Format arbitrary dict as indented key-value lines."""
    lines = []
    for key, value in data.items():
        if key.startswith("@") or key.startswith("odata"):
            continue
        if isinstance(value, dict):
            lines.append(f"  {key}:")
            for k2, v2 in value.items():
                if not str(k2).startswith("@"):
                    lines.append(f"    {k2}: {v2}")
        elif isinstance(value, list):
            lines.append(f"  {key}: [{len(value)} items]")
        else:
            lines.append(f"  {key}: {value}")
    return "\n".join(lines) if lines else json.dumps(data, indent=2)
