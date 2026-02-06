"""Main Click CLI app for smcbmc."""

import sys

import click

from smcbmc import __version__
from smcbmc.config import load_nodes, load_credentials, resolve_nodes
from smcbmc.client import BMCClient, BMCError
from smcbmc.output import print_error, print_node_header, print_multi_node_results


class NodeContext:
    """Holds resolved nodes, credentials, and global flags."""

    def __init__(self):
        self.nodes = []
        self.username = ""
        self.password = ""
        self.json_mode = False
        self.verbose = False

    def get_client(self, node):
        """Create a BMCClient for a given node."""
        return BMCClient(
            node.console_ip,
            self.username,
            self.password,
        )


pass_context = click.make_pass_decorator(NodeContext, ensure=True)


@click.group()
@click.option("--node", "-n", "node_names", multiple=True, help="Node hostname(s) to target.")
@click.option("--nodes", "node_csv", default=None, help="Comma-separated list of node hostnames or IPs.")
@click.option("--all", "all_nodes", is_flag=True, help="Target all nodes from nodes.json.")
@click.option("--json", "json_mode", is_flag=True, help="Output in JSON format.")
@click.option("--verbose", "-v", is_flag=True, help="Verbose output.")
@click.version_option(version=__version__, prog_name="smcbmc")
@click.pass_context
def cli(ctx, node_names, node_csv, all_nodes, json_mode, verbose):
    """smcbmc - Supermicro BMC Management CLI"""
    nctx = NodeContext()
    nctx.json_mode = json_mode
    nctx.verbose = verbose

    # Load credentials
    nctx.username, nctx.password = load_credentials()

    # Resolve target nodes
    all_available = load_nodes()

    if all_nodes:
        nctx.nodes = all_available
    elif node_csv:
        identifiers = [n.strip() for n in node_csv.split(",")]
        nctx.nodes = resolve_nodes(identifiers, all_available)
    elif node_names:
        nctx.nodes = resolve_nodes(list(node_names), all_available)

    ctx.obj = nctx


def run_on_nodes(nctx, operation, label=None):
    """Run an operation on all targeted nodes, collecting results.

    Args:
        nctx: NodeContext with nodes, credentials, flags
        operation: callable(client, node) -> dict (the result data)
        label: optional label for the operation (used in verbose mode)

    Returns:
        list of result dicts, one per node
    """
    if not nctx.nodes:
        print_error("No nodes specified. Use --node, --nodes, or --all.", nctx.json_mode)
        sys.exit(1)

    results = []
    any_failed = False

    for node in nctx.nodes:
        if nctx.verbose and not nctx.json_mode:
            action_label = label or "operation"
            click.echo(f"[{node.hostname}] Running {action_label}...")

        try:
            client = nctx.get_client(node)
            data = operation(client, node)
            results.append({
                "node": node.hostname,
                "success": True,
                "data": data,
            })
        except BMCError as e:
            any_failed = True
            results.append({
                "node": node.hostname,
                "success": False,
                "error": str(e),
            })
        except Exception as e:
            any_failed = True
            results.append({
                "node": node.hostname,
                "success": False,
                "error": f"Unexpected error: {e}",
            })

    # Output
    if nctx.json_mode:
        # In JSON mode, always output the full results list
        if len(results) == 1:
            r = results[0]
            if r["success"]:
                print_multi_node_results([r], json_mode=True)
            else:
                print_multi_node_results([r], json_mode=True)
        else:
            print_multi_node_results(results, json_mode=True)
    else:
        print_multi_node_results(results, json_mode=False)

    if any_failed:
        sys.exit(1)

    return results


# Import and register command groups
from smcbmc.commands.power import power
from smcbmc.commands.boot import boot
from smcbmc.commands.sensors import sensors
from smcbmc.commands.sol import sol
from smcbmc.commands.console import console
from smcbmc.commands.virtual_media import virtual_media
from smcbmc.commands.inventory import inventory
from smcbmc.commands.firmware import firmware
from smcbmc.commands.raw import raw
from smcbmc.commands.rescue import rescue
from smcbmc.commands.incusos import incusos
from smcbmc.commands.bios import bios
from smcbmc.commands.bmc import bmc

cli.add_command(power)
cli.add_command(boot)
cli.add_command(sensors)
cli.add_command(sol)
cli.add_command(console)
cli.add_command(virtual_media, name="virtual-media")
cli.add_command(inventory)
cli.add_command(firmware)
cli.add_command(raw)
cli.add_command(rescue)
cli.add_command(incusos)
cli.add_command(bios)
cli.add_command(bmc)
