"""SOL (Serial Over LAN) commands: connect, capture."""

import sys

import click

from smcbmc.cli import pass_context
from smcbmc.output import print_error


@click.group()
def sol():
    """Serial Over LAN (SOL) commands."""
    pass


@sol.command()
@pass_context
def connect(nctx):
    """Connect to SOL console (interactive).

    Replaces the current process with ipmitool sol activate.
    Only works with a single node.
    """
    from smcbmc.tools.ipmitool import sol_activate_exec

    if not nctx.nodes:
        print_error("No node specified. Use --node.", nctx.json_mode)
        sys.exit(1)
    if len(nctx.nodes) > 1:
        print_error("SOL connect only supports a single node.", nctx.json_mode)
        sys.exit(1)

    node = nctx.nodes[0]
    click.echo(f"Connecting to SOL on {node.hostname} ({node.console_ip})...")
    click.echo("Use ~. to disconnect.")
    # This does not return - replaces the process
    sol_activate_exec(node.console_ip, nctx.username, nctx.password)


@sol.command()
@click.option("--duration", type=int, default=None,
              help="Capture duration in seconds (default: until Ctrl+C).")
@click.option("--output", "output_file", default=None,
              help="Output file path (default: sol_<hostname>.log).")
@click.option("--stream", is_flag=True, default=False,
              help="Stream SOL output to terminal while capturing to file.")
@pass_context
def capture(nctx, duration, output_file, stream):
    """Capture SOL output to a file.

    Use --stream to also display output on the terminal in real-time.
    """
    from smcbmc.tools.ipmitool import sol_capture

    if not nctx.nodes:
        print_error("No node specified. Use --node.", nctx.json_mode)
        sys.exit(1)
    if len(nctx.nodes) > 1:
        print_error("SOL capture only supports a single node.", nctx.json_mode)
        sys.exit(1)

    node = nctx.nodes[0]
    if not output_file:
        output_file = f"sol_{node.hostname}.log"

    click.echo(f"Capturing SOL from {node.hostname} ({node.console_ip}) to {output_file}")
    if stream:
        click.echo("Streaming to terminal (output also saved to file)")
    if duration:
        click.echo(f"Duration: {duration}s")
    else:
        click.echo("Press Ctrl+C to stop capture.")

    success, message = sol_capture(
        node.console_ip, nctx.username, nctx.password,
        output_file, duration=duration, stream=stream,
    )
    click.echo(message)
    if not success:
        sys.exit(1)
