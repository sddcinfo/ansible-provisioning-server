"""Power management commands: status, on, off, cycle, reset."""

import click

from smcbmc import REDFISH_SYSTEMS, REDFISH_RESET_ACTION
from smcbmc.cli import pass_context, run_on_nodes


@click.group()
def power():
    """Power management commands."""
    pass


@power.command()
@pass_context
def status(nctx):
    """Get power status."""
    def _op(client, node):
        data = client.get(REDFISH_SYSTEMS)
        return {
            "PowerState": data.get("PowerState", "Unknown"),
            "Name": data.get("Name", ""),
            "Id": data.get("Id", ""),
        }
    run_on_nodes(nctx, _op, label="power status")


@power.command()
@pass_context
def on(nctx):
    """Power on the server."""
    def _op(client, node):
        return client.post(REDFISH_RESET_ACTION, {"ResetType": "On"})
    run_on_nodes(nctx, _op, label="power on")


@power.command()
@pass_context
def off(nctx):
    """Gracefully shut down the server."""
    def _op(client, node):
        return client.post(REDFISH_RESET_ACTION, {"ResetType": "GracefulShutdown"})
    run_on_nodes(nctx, _op, label="power off")


@power.command()
@pass_context
def cycle(nctx):
    """Force restart the server."""
    def _op(client, node):
        return client.post(REDFISH_RESET_ACTION, {"ResetType": "ForceRestart"})
    run_on_nodes(nctx, _op, label="power cycle")


@power.command()
@pass_context
def reset(nctx):
    """Gracefully restart the server."""
    def _op(client, node):
        return client.post(REDFISH_RESET_ACTION, {"ResetType": "GracefulRestart"})
    run_on_nodes(nctx, _op, label="power reset")
