"""Out-of-band BMC configuration commands via SUM."""

import os
import sys

import click

from smcbmc.cli import pass_context, run_on_nodes
from smcbmc.output import print_error


@click.group()
def bmc():
    """BMC configuration via out-of-band SUM."""
    pass


@bmc.command(name="get")
@click.option("--output-dir", default=".", help="Directory to save BMC config files.")
@pass_context
def bmc_get(nctx, output_dir):
    """Download current BMC configuration from each node."""
    from smcbmc.tools.sum import get_bmc_config

    os.makedirs(output_dir, exist_ok=True)

    def _op(client, node):
        hostname = node.os_hostname or node.hostname
        output_file = os.path.join(output_dir, f"{hostname}_bmc.xml")
        success, stdout, stderr = get_bmc_config(
            node.console_ip, nctx.username, nctx.password, output_file
        )
        if success:
            return {"message": f"BMC config saved to {output_file}", "file": output_file}
        raise Exception(f"Failed to get BMC config: {(stdout + stderr).strip()}")

    run_on_nodes(nctx, _op, label="bmc get")


@bmc.command(name="set")
@click.argument("config_file", type=click.Path(exists=True))
@pass_context
def bmc_set(nctx, config_file):
    """Apply a BMC configuration file to each node."""
    from smcbmc.tools.sum import set_bmc_config

    def _op(client, node):
        success, stdout, stderr = set_bmc_config(
            node.console_ip, nctx.username, nctx.password, config_file
        )
        if not success:
            raise Exception(f"Failed to set BMC config: {(stdout + stderr).strip()}")
        return {"message": "BMC config applied successfully"}

    run_on_nodes(nctx, _op, label="bmc set")


def _parse_config_settings(content):
    """Parse SUM config (BIOS or BMC) text into a dict of {path: value}.

    Handles:
      - INI-style format: [Section] headers, Key=Value // comments
      - BMC XML format: <BmcCfg>/<StdCfg>/<Section>/<Configuration>/<Key>value</Key>
      - BIOS XML format: elements with name/selectedOption attributes
    """
    # Try INI-style parsing first
    settings = {}
    section = ""
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
            continue
        if "=" in line and not line.startswith("<"):
            key_val = line.split("//")[0].strip()
            key, _, value = key_val.partition("=")
            key = key.strip()
            value = value.strip()
            if key:
                settings[f"{section}|{key}"] = value

    if settings:
        return settings

    # Try BMC/BIOS XML parsing
    try:
        import xml.etree.ElementTree as ET
        root = ET.fromstring(content)
        _parse_xml_recursive(root, "", settings)
    except Exception:
        pass

    return settings


def _parse_xml_recursive(elem, path, settings):
    """Recursively parse XML elements into path-based key-value pairs."""
    tag = elem.tag
    # Skip the root BmcCfg element in path
    if tag in ("BmcCfg",):
        for child in elem:
            _parse_xml_recursive(child, path, settings)
        return

    current_path = f"{path}/{tag}" if path else tag

    # Add attributes to path (e.g., User UserID="2")
    user_id = elem.get("UserID")
    if user_id:
        current_path = f"{current_path}[{user_id}]"

    # If element has text content and no child elements, it's a leaf value
    children = list(elem)
    has_value_children = any(
        c.tag not in ("Configuration", "Information") and list(c) == [] and c.text
        for c in children
    )

    if not children and elem.text and elem.text.strip():
        # Leaf element with text value
        settings[current_path] = elem.text.strip()
    elif has_value_children:
        # Container with value leaves -- recurse into children
        for child in children:
            _parse_xml_recursive(child, current_path, settings)
    else:
        # Intermediate container (Configuration, Information, etc.) -- recurse
        for child in children:
            _parse_xml_recursive(child, current_path, settings)


# Sections containing per-node data (FRU serial numbers, etc.)
BMC_SKIP_SECTIONS = [
    "FRU",
]
# Additional per-node keys that vary by definition
BMC_SKIP_SUBSTRINGS = [
    "SerialNum", "Serial", "UUID", "Password", "MacAddr", "DUID",
    "IPAddr", "SubnetMask", "DefaultGateway", "DateTimeValue",
    "CertStartDate", "CertEndDate", "PathToImage", "HostName",
]


@bmc.command(name="compare")
@click.option("--reference", type=click.Path(exists=True), default=None,
              help="Reference BMC config file. If not given, compares all against the first node.")
@click.option("--output-dir", default=".", help="Directory to save fetched BMC configs.")
@click.option("--include-network", is_flag=True,
              help="Include network/IP settings in comparison (excluded by default).")
@pass_context
def bmc_compare(nctx, reference, output_dir, include_network):
    """Compare BMC configurations across nodes.

    By default, skips node-specific settings (hostname, MAC, IP).
    Use --include-network to include them.
    """
    from smcbmc.tools.sum import get_bmc_config

    if not nctx.nodes:
        print_error("No nodes specified. Use --node, --nodes, or --all.", nctx.json_mode)
        sys.exit(1)

    os.makedirs(output_dir, exist_ok=True)

    configs = {}
    for node in nctx.nodes:
        hostname = node.os_hostname or node.hostname
        output_file = os.path.join(output_dir, f"{hostname}_bmc.xml")

        click.echo(f"[{hostname}] Fetching BMC config...")
        success, stdout, stderr = get_bmc_config(
            node.console_ip, nctx.username, nctx.password, output_file
        )

        if not success:
            click.echo(f"[{hostname}] SUM failed: {(stdout + stderr).strip()}", err=True)
            continue

        try:
            with open(output_file) as f:
                configs[hostname] = f.read()
            click.echo(f"[{hostname}] Config saved to {output_file}")
        except OSError as e:
            click.echo(f"[{hostname}] Failed to read config: {e}", err=True)

    if not configs:
        click.echo("No BMC configs retrieved. Cannot compare.", err=True)
        sys.exit(1)

    if reference:
        with open(reference) as f:
            ref_content = f.read()
        ref_name = os.path.basename(reference)
    else:
        ref_name = list(configs.keys())[0]
        ref_content = configs[ref_name]

    click.echo(f"\n=== BMC Configuration Comparison (reference: {ref_name}) ===\n")

    ref_settings = _parse_config_settings(ref_content)
    if not ref_settings:
        click.echo("Failed to parse reference config (no settings found).", err=True)
        sys.exit(1)

    def _should_skip(key, include_net):
        """Check if a key should be skipped in comparison."""
        # Always skip per-node values
        if any(sub in key for sub in BMC_SKIP_SUBSTRINGS):
            return True
        if not include_net:
            for section in BMC_SKIP_SECTIONS:
                if f"/{section}/" in key or key.startswith(f"{section}/"):
                    return True
        return False

    all_identical = True
    for hostname, content in configs.items():
        if hostname == ref_name and not reference:
            continue

        node_settings = _parse_config_settings(content)
        if not node_settings:
            click.echo(f"[{hostname}] Failed to parse BMC config (no settings found)")
            all_identical = False
            continue

        diffs = []
        all_keys = sorted(set(ref_settings.keys()) | set(node_settings.keys()))
        for key in all_keys:
            if _should_skip(key, include_network):
                continue
            ref_val = ref_settings.get(key, "<missing>")
            node_val = node_settings.get(key, "<missing>")
            if ref_val != node_val:
                diffs.append(f"  {key}: {ref_val} -> {node_val}")

        if diffs:
            all_identical = False
            click.echo(f"[{hostname}] {len(diffs)} difference(s) vs {ref_name}:")
            for d in diffs:
                click.echo(d)
            click.echo("")
        else:
            click.echo(f"[{hostname}] IDENTICAL to {ref_name}")

    if all_identical:
        click.echo("\nAll nodes have identical BMC configuration.")
    else:
        click.echo("\nDifferences found. Use 'bmc set <config.xml>' to apply a uniform config.")
        sys.exit(1)


@bmc.command(name="info")
@pass_context
def bmc_info(nctx):
    """Get BMC and BIOS firmware versions from each node."""
    from smcbmc.tools.sum import get_bmc_info, get_bios_info

    def _op(client, node):
        bmc_ok, bmc_out, bmc_err = get_bmc_info(
            node.console_ip, nctx.username, nctx.password
        )
        bios_ok, bios_out, bios_err = get_bios_info(
            node.console_ip, nctx.username, nctx.password
        )

        result = {}
        if bmc_ok:
            result["bmc_info"] = bmc_out.strip()
        else:
            result["bmc_error"] = (bmc_out + bmc_err).strip()

        if bios_ok:
            result["bios_info"] = bios_out.strip()
        else:
            result["bios_error"] = (bios_out + bios_err).strip()

        return result

    run_on_nodes(nctx, _op, label="bmc info")
