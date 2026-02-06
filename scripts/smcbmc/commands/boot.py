"""Boot management commands: get, set-next, set-persistent, bios-config, ipmi-override."""

import os
import sys

import click

from smcbmc import REDFISH_SYSTEMS, BOOT_DEVICES
from smcbmc.cli import pass_context, run_on_nodes
from smcbmc.output import print_error


# IPMI boot device selector byte values (bits [5:2] of byte 2)
IPMI_BOOT_DEVICES = {
    "none": 0x00,
    "pxe": 0x04,
    "hdd": 0x08,
    "cd": 0x14,
    "bios": 0x18,
    "usb": 0x3C,
}


@click.group()
def boot():
    """Boot configuration commands."""
    pass


@boot.command()
@pass_context
def get(nctx):
    """Get current boot configuration (Redfish + IPMI boot params)."""
    from smcbmc.tools.ipmitool import run_raw

    def _op(client, node):
        # Redfish boot info
        data = client.get(REDFISH_SYSTEMS)
        result = {"Boot": data.get("Boot", {})}

        # IPMI boot parameter 5 for the real override state
        success, stdout, stderr = run_raw(
            node.console_ip, nctx.username, nctx.password,
            "chassis bootparam get 5"
        )
        if success:
            result["IPMI_BootParams"] = stdout.strip()

        return result

    run_on_nodes(nctx, _op, label="boot get")


@boot.command(name="set-next")
@click.argument("device", type=click.Choice(BOOT_DEVICES, case_sensitive=False))
@pass_context
def set_next(nctx, device):
    """Set the boot device for the next boot only."""
    def _op(client, node):
        payload = {
            "Boot": {
                "BootSourceOverrideEnabled": "Once",
                "BootSourceOverrideTarget": device,
            }
        }
        return client.patch(REDFISH_SYSTEMS, payload)
    run_on_nodes(nctx, _op, label=f"boot set-next {device}")


@boot.command(name="set-persistent")
@click.argument("device", type=click.Choice(BOOT_DEVICES, case_sensitive=False))
@pass_context
def set_persistent(nctx, device):
    """Set the persistent boot device."""
    def _op(client, node):
        payload = {
            "Boot": {
                "BootSourceOverrideEnabled": "Continuous",
                "BootSourceOverrideTarget": device,
            }
        }
        return client.patch(REDFISH_SYSTEMS, payload)
    run_on_nodes(nctx, _op, label=f"boot set-persistent {device}")


@boot.command(name="ipmi-override")
@click.argument("device", type=click.Choice(list(IPMI_BOOT_DEVICES.keys()), case_sensitive=False))
@click.option("--persistent/--next-only", default=True,
              help="Apply to all future boots (default) or next boot only.")
@click.option("--uefi/--legacy", default=True,
              help="UEFI boot (default) or legacy BIOS boot.")
@pass_context
def ipmi_override(nctx, device, persistent, uefi):
    """Set IPMI boot device override using raw commands.

    Uses raw IPMI commands to bypass ipmitool 1.8.19 efiboot+persistent bug.
    This is the most reliable way to force boot device on Supermicro X10/X11.

    \b
    Devices: none, pxe, hdd, cd, bios, usb
    """
    from smcbmc.tools.ipmitool import run_raw

    # Build byte 1: bit7=valid, bit6=persistent, bit5=EFI
    byte1 = 0x80  # valid
    if persistent:
        byte1 |= 0x40
    if uefi:
        byte1 |= 0x20

    byte2 = IPMI_BOOT_DEVICES[device.lower()]

    def _op(client, node):
        raw_cmd = f"raw 0x00 0x08 0x05 0x{byte1:02x} 0x{byte2:02x} 0x00 0x00 0x00"
        success, stdout, stderr = run_raw(
            node.console_ip, nctx.username, nctx.password, raw_cmd
        )
        if not success:
            raise Exception(f"IPMI override failed: {(stdout + stderr).strip()}")

        # Verify
        success2, stdout2, stderr2 = run_raw(
            node.console_ip, nctx.username, nctx.password,
            "chassis bootparam get 5"
        )
        verify = stdout2.strip() if success2 else "verification failed"

        mode = "UEFI" if uefi else "Legacy"
        scope = "persistent" if persistent else "next-boot-only"
        return {
            "message": f"IPMI override set: {device} ({mode}, {scope})",
            "raw_bytes": f"0x{byte1:02x} 0x{byte2:02x} 0x00 0x00 0x00",
            "verify": verify,
        }

    run_on_nodes(nctx, _op, label=f"ipmi-override {device}")


@boot.command(name="clear-override")
@pass_context
def clear_override(nctx):
    """Clear IPMI boot device override (return to normal BIOS boot order)."""
    from smcbmc.tools.ipmitool import run_raw

    def _op(client, node):
        raw_cmd = "raw 0x00 0x08 0x05 0x00 0x00 0x00 0x00 0x00"
        success, stdout, stderr = run_raw(
            node.console_ip, nctx.username, nctx.password, raw_cmd
        )
        if not success:
            raise Exception(f"Clear override failed: {(stdout + stderr).strip()}")
        return {"message": "IPMI boot override cleared"}

    run_on_nodes(nctx, _op, label="boot clear-override")


@boot.group(name="bios-config")
def bios_config():
    """BIOS configuration via SUM tool."""
    pass


@bios_config.command(name="get")
@click.option("--output-dir", default=".", help="Directory to save BIOS config files.")
@pass_context
def bios_config_get(nctx, output_dir):
    """Download current BIOS configuration."""
    from smcbmc.tools.sum import get_bios_config

    os.makedirs(output_dir, exist_ok=True)

    def _op(client, node):
        output_file = os.path.join(output_dir, f"{node.console_ip}_bios_config.xml")
        success, stdout, stderr = get_bios_config(
            node.console_ip, nctx.username, nctx.password, output_file
        )
        if success:
            return {"message": f"BIOS config saved to {output_file}", "file": output_file}
        raise Exception(f"Failed to get BIOS config: {(stdout + stderr).strip()}")

    run_on_nodes(nctx, _op, label="bios-config get")


@bios_config.command(name="set")
@click.argument("config_file", type=click.Path(exists=True))
@click.option("--reboot", is_flag=True, help="Reboot after applying config.")
@pass_context
def bios_config_set(nctx, config_file, reboot):
    """Apply a BIOS configuration file."""
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

    run_on_nodes(nctx, _op, label="bios-config set")
