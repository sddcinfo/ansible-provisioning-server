"""Sensor commands: list with optional type and name filtering."""

import click

from smcbmc import REDFISH_THERMAL, REDFISH_POWER
from smcbmc.cli import pass_context, run_on_nodes


@click.group()
def sensors():
    """Sensor data commands."""
    pass


@sensors.command(name="list")
@click.option("--type", "sensor_type", type=click.Choice(["temp", "fan", "voltage"]),
              help="Filter by sensor type.")
@click.option("--filter", "name_filter", default=None,
              help="Filter sensors by name (case-insensitive substring).")
@pass_context
def list_sensors(nctx, sensor_type, name_filter):
    """List sensor readings."""
    def _op(client, node):
        data = client.get(REDFISH_THERMAL)
        result = {}

        # Include temperatures
        if sensor_type is None or sensor_type == "temp":
            temps = data.get("Temperatures", [])
            if name_filter:
                nf = name_filter.lower()
                temps = [t for t in temps if nf in t.get("Name", "").lower()]
            if temps:
                result["Temperatures"] = temps

        # Include fans
        if sensor_type is None or sensor_type == "fan":
            fans = data.get("Fans", [])
            if name_filter:
                nf = name_filter.lower()
                fans = [f for f in fans if nf in f.get("FanName", f.get("Name", "")).lower()]
            if fans:
                result["Fans"] = fans

        # Include voltages (from Power endpoint)
        if sensor_type is None or sensor_type == "voltage":
            try:
                power_data = client.get(REDFISH_POWER)
                voltages = power_data.get("Voltages", [])
                if name_filter:
                    nf = name_filter.lower()
                    voltages = [v for v in voltages if nf in v.get("Name", "").lower()]
                if voltages:
                    result["Voltages"] = voltages
            except Exception:
                pass  # Voltage endpoint may not exist

        if not result:
            result = {"message": "No matching sensors found."}

        return result

    run_on_nodes(nctx, _op, label="sensors list")
