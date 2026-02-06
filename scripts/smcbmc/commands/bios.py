"""Out-of-band BIOS configuration commands via SUM."""

import os
import sys

import click

from smcbmc.cli import pass_context, run_on_nodes
from smcbmc.output import print_error


@click.group()
def bios():
    """BIOS configuration via out-of-band SUM."""
    pass


@bios.command(name="get")
@click.option("--output-dir", default=".", help="Directory to save BIOS config files.")
@pass_context
def bios_get(nctx, output_dir):
    """Download current BIOS configuration from each node."""
    from smcbmc.tools.sum import get_bios_config

    os.makedirs(output_dir, exist_ok=True)

    def _op(client, node):
        hostname = node.os_hostname or node.hostname
        output_file = os.path.join(output_dir, f"{hostname}_bios.xml")
        success, stdout, stderr = get_bios_config(
            node.console_ip, nctx.username, nctx.password, output_file
        )
        if success:
            return {"message": f"BIOS config saved to {output_file}", "file": output_file}
        raise Exception(f"Failed to get BIOS config: {(stdout + stderr).strip()}")

    run_on_nodes(nctx, _op, label="bios get")


@bios.command(name="set")
@click.argument("config_file", type=click.Path(exists=True))
@click.option("--reboot", is_flag=True, help="Reboot after applying config.")
@pass_context
def bios_set(nctx, config_file, reboot):
    """Apply a BIOS configuration file to each node."""
    from smcbmc.tools.sum import set_bios_config
    from smcbmc import REDFISH_RESET_ACTION

    def _op(client, node):
        success, stdout, stderr = set_bios_config(
            node.console_ip, nctx.username, nctx.password, config_file
        )
        if not success:
            raise Exception(f"Failed to set BIOS config: {(stdout + stderr).strip()}")

        result = {"message": "BIOS config applied successfully"}
        if reboot:
            client.post(REDFISH_RESET_ACTION, {"ResetType": "GracefulRestart"})
            result["message"] += " (reboot initiated)"
        else:
            result["message"] += " (reboot required for changes to take effect)"
        return result

    run_on_nodes(nctx, _op, label="bios set")


def _parse_bios_settings(content):
    """Parse SUM BIOS config text into a dict of {section|key: value}.

    Format: [Section|Subsection] headers, Key=Value // comments.
    """
    settings = {}
    section = ""
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
            continue
        if "=" in line:
            # Strip trailing comments: "Key=Value // comment"
            key_val = line.split("//")[0].strip()
            key, _, value = key_val.partition("=")
            key = key.strip()
            value = value.strip()
            if key:
                settings[f"{section}|{key}"] = value
    return settings


@bios.command(name="compare")
@click.option("--reference", type=click.Path(exists=True), default=None,
              help="Reference BIOS config file to compare against. If not given, compares all against the first node.")
@click.option("--output-dir", default=".", help="Directory to save fetched BIOS configs.")
@pass_context
def bios_compare(nctx, reference, output_dir):
    """Compare BIOS configurations across nodes.

    Fetches BIOS config from each node via out-of-band SUM and compares.
    With --reference, compares each node against the given file.
    Without --reference, compares all nodes against the first node.
    """
    from smcbmc.tools.sum import get_bios_config

    if not nctx.nodes:
        print_error("No nodes specified. Use --node, --nodes, or --all.", nctx.json_mode)
        sys.exit(1)

    os.makedirs(output_dir, exist_ok=True)

    # Fetch BIOS configs from all nodes
    configs = {}
    for node in nctx.nodes:
        hostname = node.os_hostname or node.hostname
        output_file = os.path.join(output_dir, f"{hostname}_bios.xml")

        click.echo(f"[{hostname}] Fetching BIOS config...")
        success, stdout, stderr = get_bios_config(
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
        click.echo("No BIOS configs retrieved. Cannot compare.", err=True)
        sys.exit(1)

    # Load reference
    if reference:
        with open(reference) as f:
            ref_content = f.read()
        ref_name = os.path.basename(reference)
    else:
        ref_name = list(configs.keys())[0]
        ref_content = configs[ref_name]

    click.echo(f"\n=== BIOS Configuration Comparison (reference: {ref_name}) ===\n")

    ref_settings = _parse_bios_settings(ref_content)
    if not ref_settings:
        click.echo(f"Failed to parse reference config (no settings found).", err=True)
        sys.exit(1)

    all_identical = True
    for hostname, content in configs.items():
        if hostname == ref_name and not reference:
            continue

        node_settings = _parse_bios_settings(content)
        if not node_settings:
            click.echo(f"[{hostname}] Failed to parse BIOS config (no settings found)")
            all_identical = False
            continue

        diffs = []
        for key in sorted(set(ref_settings.keys()) | set(node_settings.keys())):
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
        click.echo("\nAll nodes have identical BIOS configuration.")
    else:
        click.echo("\nDifferences found. Use 'bios set <config.xml>' to apply a uniform config.")
        sys.exit(1)
