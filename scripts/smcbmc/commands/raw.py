"""Raw command pass-through: redfish, ipmi, sum."""

import click

from smcbmc.cli import pass_context, run_on_nodes


@click.group()
def raw():
    """Raw command pass-through for Redfish, IPMI, and SUM."""
    pass


@raw.command()
@click.argument("path")
@click.option("--method", default="GET", type=click.Choice(["GET", "POST", "PATCH", "DELETE"]),
              help="HTTP method.")
@click.option("--data", "payload", default=None, help="JSON payload for POST/PATCH.")
@pass_context
def redfish(nctx, path, method, payload):
    """Execute a raw Redfish API request."""
    import json as _json

    parsed_data = None
    if payload:
        try:
            parsed_data = _json.loads(payload)
        except _json.JSONDecodeError as e:
            click.echo(f"Error: invalid JSON payload: {e}", err=True)
            raise SystemExit(1)

    def _op(client, node):
        if method == "GET":
            return client.get(path)
        elif method == "POST":
            return client.post(path, parsed_data)
        elif method == "PATCH":
            return client.patch(path, parsed_data)
        elif method == "DELETE":
            return client.delete(path)

    run_on_nodes(nctx, _op, label=f"redfish {method} {path}")


@raw.command()
@click.argument("cmd")
@pass_context
def ipmi(nctx, cmd):
    """Execute a raw ipmitool command."""
    from smcbmc.tools.ipmitool import run_raw

    def _op(client, node):
        success, stdout, stderr = run_raw(
            node.console_ip, nctx.username, nctx.password, cmd
        )
        if success:
            return {"output": stdout.strip()}
        raise Exception(f"IPMI command failed: {(stdout + stderr).strip()}")

    run_on_nodes(nctx, _op, label=f"ipmi {cmd}")


@raw.command()
@click.argument("cmd")
@click.option("--args", "extra_args", default=None, help="Additional SUM arguments.")
@pass_context
def sum(nctx, cmd, extra_args):
    """Execute a raw SUM command."""
    from smcbmc.tools.sum import run_arbitrary

    def _op(client, node):
        success, stdout, stderr = run_arbitrary(
            node.console_ip, nctx.username, nctx.password,
            cmd, extra_args,
        )
        if success:
            return {"output": stdout.strip()}
        raise Exception(f"SUM command failed: {(stdout + stderr).strip()}")

    run_on_nodes(nctx, _op, label=f"sum {cmd}")
