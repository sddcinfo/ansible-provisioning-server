"""Rescue environment commands: boot, status, ssh, install-os, wipe-disks, disk-info, bios-*, bmc-reset, tpm-*, efi-*."""

import os
import subprocess
import sys

import click

from smcbmc.cli import pass_context, run_on_nodes
from smcbmc.output import print_error

# SSH options for connecting to rescue environment
SSH_KEY = os.path.expanduser("~/.ssh/sysadmin_automation_key")
SSH_OPTS = [
    "-o", "StrictHostKeyChecking=no",
    "-o", "UserKnownHostsFile=/dev/null",
    "-o", "ConnectTimeout=5",
    "-o", "LogLevel=ERROR",
]
PROVISIONING_SERVER = "10.10.1.1"


def _ssh_cmd(node_ip, command):
    """Build SSH command list for a rescue node."""
    return [
        "ssh", *SSH_OPTS,
        "-i", SSH_KEY,
        f"root@{node_ip}",
        command,
    ]


def _run_ssh(node_ip, command):
    """Run an SSH command on a rescue node and return (success, stdout, stderr)."""
    cmd = _ssh_cmd(node_ip, command)
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,
        )
        return result.returncode == 0, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return False, "", "SSH command timed out"
    except Exception as e:
        return False, "", str(e)


@click.group()
def rescue():
    """Ubuntu Linux rescue environment commands."""
    pass


@rescue.command()
@pass_context
def boot(nctx):
    """Boot node(s) into rescue environment via PXE.

    Sets os_type=rescue and status=NEW in the provisioning server,
    configures IPMI for PXE boot (next boot, UEFI), and power cycles.
    """
    from smcbmc.tools.ipmitool import run_raw

    def _op(client, node):
        # Step 1: Set os_type=rescue and status=NEW via provisioning API
        import urllib.request

        mac = node.os_mac
        if not mac:
            raise Exception(f"No os_mac configured for {node.hostname}")

        # Set OS type to rescue
        url = f"http://{PROVISIONING_SERVER}/?action=set_os&mac={mac}&os_type=rescue"
        try:
            urllib.request.urlopen(url, timeout=10)
        except Exception as e:
            raise Exception(f"Failed to set os_type=rescue: {e}")

        # Set status to NEW for re-provisioning
        url = f"http://{PROVISIONING_SERVER}/?action=callback&mac={mac}&status=NEW"
        try:
            urllib.request.urlopen(url, timeout=10)
        except Exception as e:
            raise Exception(f"Failed to set status=NEW: {e}")

        # Step 2: Set IPMI PXE boot override (next-boot, UEFI)
        # byte1: 0x80 (valid) | 0x20 (EFI) = 0xA0 (next-boot only, UEFI)
        # byte2: 0x04 = PXE
        raw_cmd = "raw 0x00 0x08 0x05 0xa0 0x04 0x00 0x00 0x00"
        success, stdout, stderr = run_raw(
            node.console_ip, nctx.username, nctx.password, raw_cmd
        )
        if not success:
            raise Exception(f"IPMI PXE override failed: {(stdout + stderr).strip()}")

        # Step 3: Power cycle
        from smcbmc import REDFISH_RESET_ACTION
        try:
            client.post(REDFISH_RESET_ACTION, {"ResetType": "ForceRestart"})
        except Exception:
            # Try power on if system was off
            try:
                client.post(REDFISH_RESET_ACTION, {"ResetType": "On"})
            except Exception as e:
                raise Exception(f"Power cycle failed: {e}")

        return {
            "message": f"Rescue boot initiated for {node.hostname}",
            "os_mac": mac,
            "ipmi_override": "PXE (next-boot, UEFI)",
            "power": "ForceRestart",
        }

    run_on_nodes(nctx, _op, label="rescue boot")


@rescue.command()
@pass_context
def status(nctx):
    """Check if rescue environment is running on node(s).

    SSHs to the node OS IP and checks for Ubuntu (lsb-release) or Alpine.
    """
    if not nctx.nodes:
        print_error("No nodes specified. Use --node, --nodes, or --all.", nctx.json_mode)
        sys.exit(1)

    results = []
    for node in nctx.nodes:
        if not node.os_ip:
            results.append({
                "node": node.hostname,
                "success": False,
                "error": "No os_ip configured",
            })
            continue

        success, stdout, stderr = _run_ssh(
            node.os_ip,
            "(grep DISTRIB_DESCRIPTION /etc/lsb-release 2>/dev/null | cut -d= -f2 | tr -d '\"' || cat /etc/alpine-release 2>/dev/null || echo unknown) && hostname && uptime"
        )

        if success and stdout.strip():
            lines = stdout.strip().split("\n")
            results.append({
                "node": node.hostname,
                "success": True,
                "data": {
                    "status": "RESCUE_RUNNING",
                    "os_version": lines[0] if lines else "unknown",
                    "hostname": lines[1] if len(lines) > 1 else "unknown",
                    "uptime": lines[2].strip() if len(lines) > 2 else "unknown",
                    "os_ip": node.os_ip,
                },
            })
        else:
            results.append({
                "node": node.hostname,
                "success": False,
                "error": f"Rescue not reachable at {node.os_ip}: {stderr.strip()}",
            })

    from smcbmc.output import print_multi_node_results
    print_multi_node_results(results, json_mode=nctx.json_mode)
    if any(not r["success"] for r in results):
        sys.exit(1)


@rescue.command(name="ssh")
@click.argument("cmd", nargs=-1, required=True)
@pass_context
def ssh_cmd(nctx, cmd):
    """Run arbitrary command(s) on rescue node(s) via SSH.

    Example: smcbmc-cli -n console-node4 rescue ssh lsblk
    """
    command_str = " ".join(cmd)

    if not nctx.nodes:
        print_error("No nodes specified. Use --node, --nodes, or --all.", nctx.json_mode)
        sys.exit(1)

    results = []
    for node in nctx.nodes:
        if not node.os_ip:
            results.append({
                "node": node.hostname,
                "success": False,
                "error": "No os_ip configured",
            })
            continue

        success, stdout, stderr = _run_ssh(node.os_ip, command_str)
        if success:
            results.append({
                "node": node.hostname,
                "success": True,
                "data": {"output": stdout.strip()},
            })
        else:
            results.append({
                "node": node.hostname,
                "success": False,
                "error": f"Command failed: {stderr.strip()}",
            })

    from smcbmc.output import print_multi_node_results
    print_multi_node_results(results, json_mode=nctx.json_mode)
    if any(not r["success"] for r in results):
        sys.exit(1)


@rescue.command(name="install-os")
@click.option("--skip-rescue-boot", is_flag=True,
              help="Skip rescue boot (rescue already running).")
@click.option("--skip-tpm-clear", is_flag=True,
              help="Skip TPM clear (TPM already clean).")
@click.option("--skip-wait", is_flag=True,
              help="Skip waiting for Incus API after install.")
@pass_context
def install_os(nctx, skip_rescue_boot, skip_tpm_clear, skip_wait):
    """Install Incus OS on node(s) -- full orchestrated workflow.

    \b
    Steps:
      1. Boot into rescue (PXE) -- skipped with --skip-rescue-boot
      2. Clear TPM -- skipped with --skip-tpm-clear
      3. Run install-incusos.sh (DD image, seed, loader.conf)
      4. Set IPMI boot override to HDD
      5. Power cycle
      6. Wait for Incus API on port 8443 -- skipped with --skip-wait

    \b
    CRITICAL RULES:
      - NEVER clear TPM after first boot (destroys LUKS keys)
      - Disk ID must be scsi-3<wwn> format
      - Version 202602031842 was PULLED -- do not use
    """
    import time
    import urllib.request
    from smcbmc.tools.ipmitool import run_raw

    if not nctx.nodes:
        print_error("No nodes specified. Use --node, --nodes, or --all.", nctx.json_mode)
        sys.exit(1)

    results = []
    for node in nctx.nodes:
        if not node.os_ip:
            results.append({
                "node": node.hostname,
                "success": False,
                "error": "No os_ip configured",
            })
            continue

        hostname = node.os_hostname or node.hostname
        steps_completed = []

        try:
            # --- Step 1: Boot into rescue ---
            if not skip_rescue_boot:
                click.echo(f"[{node.hostname}] Step 1/6: Booting into rescue...")

                mac = node.os_mac
                if not mac:
                    raise Exception(f"No os_mac configured for {node.hostname}")

                # Set os_type=rescue and status=NEW
                url = f"http://{PROVISIONING_SERVER}/?action=set_os&mac={mac}&os_type=rescue"
                try:
                    urllib.request.urlopen(url, timeout=10)
                except Exception as e:
                    raise Exception(f"Failed to set os_type=rescue: {e}")

                url = f"http://{PROVISIONING_SERVER}/?action=callback&mac={mac}&status=NEW"
                try:
                    urllib.request.urlopen(url, timeout=10)
                except Exception as e:
                    raise Exception(f"Failed to set status=NEW: {e}")

                # Set IPMI PXE boot override (next-boot, UEFI)
                raw_cmd = "raw 0x00 0x08 0x05 0xa0 0x04 0x00 0x00 0x00"
                success, stdout, stderr = run_raw(
                    node.console_ip, nctx.username, nctx.password, raw_cmd
                )
                if not success:
                    raise Exception(f"IPMI PXE override failed: {(stdout + stderr).strip()}")

                # Power cycle
                client = nctx.get_client(node)
                from smcbmc import REDFISH_RESET_ACTION
                try:
                    client.post(REDFISH_RESET_ACTION, {"ResetType": "ForceRestart"})
                except Exception:
                    try:
                        client.post(REDFISH_RESET_ACTION, {"ResetType": "On"})
                    except Exception as e:
                        raise Exception(f"Power cycle failed: {e}")

                # Wait for rescue SSH
                click.echo(f"[{node.hostname}]   Waiting for rescue SSH on {node.os_ip}...")
                rescue_up = False
                for i in range(20):  # 20 * 15s = 5 min
                    time.sleep(15)
                    success, stdout, _ = _run_ssh(node.os_ip, "echo RESCUE_OK")
                    if success and "RESCUE_OK" in stdout:
                        rescue_up = True
                        break
                    click.echo(f"[{node.hostname}]   Attempt {i+1}/20...")

                if not rescue_up:
                    raise Exception("Rescue environment did not come up within 5 minutes")

                steps_completed.append("rescue_boot")
                click.echo(f"[{node.hostname}]   Rescue is running.")
            else:
                click.echo(f"[{node.hostname}] Step 1/6: Skipping rescue boot (--skip-rescue-boot)")
                steps_completed.append("rescue_boot (skipped)")

            # --- Step 2: Clear TPM ---
            if not skip_tpm_clear:
                click.echo(f"[{node.hostname}] Step 2/6: Clearing TPM...")
                tpm_cmd = (
                    "if command -v tpm2_clear >/dev/null 2>&1; then "
                    "  tpm2_clear 2>&1 && echo TPM_CLEAR_OK || "
                    "  (tpm2_clear -c platform 2>&1 && echo TPM_CLEAR_OK || echo TPM_CLEAR_FAIL); "
                    "else echo TPM_TOOLS_MISSING; fi"
                )
                success, stdout, stderr = _run_ssh(node.os_ip, tpm_cmd)
                if "TPM_CLEAR_OK" in stdout:
                    steps_completed.append("tpm_clear")
                    click.echo(f"[{node.hostname}]   TPM cleared.")
                elif "TPM_TOOLS_MISSING" in stdout:
                    click.echo(f"[{node.hostname}]   WARNING: tpm2-tools not found, skipping TPM clear")
                    steps_completed.append("tpm_clear (tools missing)")
                else:
                    raise Exception(f"TPM clear failed: {stdout.strip()} {stderr.strip()}")
            else:
                click.echo(f"[{node.hostname}] Step 2/6: Skipping TPM clear (--skip-tpm-clear)")
                steps_completed.append("tpm_clear (skipped)")

            # --- Step 3: Run install script ---
            click.echo(f"[{node.hostname}] Step 3/6: Running install-incusos.sh...")
            command = (
                f"curl -sfL http://{PROVISIONING_SERVER}/rescue-scripts/install-incusos.sh "
                f"| sh -s -- {PROVISIONING_SERVER}"
            )

            # Use extended timeout for large DD operation
            cmd = _ssh_cmd(node.os_ip, command)
            try:
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=600,
                )
                if result.returncode != 0:
                    raise Exception(
                        f"Install script failed:\n{result.stderr.strip()}\n"
                        f"{result.stdout.strip()[-500:]}"
                    )
                steps_completed.append("install_script")
                # Show last few lines of output
                output_lines = result.stdout.strip().split("\n")
                for line in output_lines[-5:]:
                    click.echo(f"[{node.hostname}]   {line}")
            except subprocess.TimeoutExpired:
                raise Exception("Install script timed out after 600s")

            # --- Step 4: Set IPMI boot to HDD ---
            click.echo(f"[{node.hostname}] Step 4/6: Setting IPMI boot to HDD...")
            # byte1: 0x80 (valid) | 0x20 (EFI) = 0xA0 (next-boot only, UEFI)
            # byte2: 0x08 = HDD
            raw_cmd = "raw 0x00 0x08 0x05 0xa0 0x08 0x00 0x00 0x00"
            success, stdout, stderr = run_raw(
                node.console_ip, nctx.username, nctx.password, raw_cmd
            )
            if not success:
                raise Exception(f"IPMI HDD override failed: {(stdout + stderr).strip()}")
            steps_completed.append("ipmi_hdd_boot")
            click.echo(f"[{node.hostname}]   IPMI boot set to HDD (next-boot, UEFI).")

            # --- Step 5: Power cycle ---
            click.echo(f"[{node.hostname}] Step 5/6: Power cycling...")
            client = nctx.get_client(node)
            from smcbmc import REDFISH_RESET_ACTION
            try:
                client.post(REDFISH_RESET_ACTION, {"ResetType": "ForceRestart"})
            except Exception:
                try:
                    client.post(REDFISH_RESET_ACTION, {"ResetType": "On"})
                except Exception as e:
                    raise Exception(f"Power cycle failed: {e}")
            steps_completed.append("power_cycle")
            click.echo(f"[{node.hostname}]   Power cycle initiated.")

            # --- Step 6: Wait for Incus API ---
            if not skip_wait:
                click.echo(f"[{node.hostname}] Step 6/6: Waiting for Incus API on port 8443...")
                click.echo(f"[{node.hostname}]   (installer boots, installs to target, reboots, first boot creates LUKS+ZFS)")
                api_up = False
                api_version = "unknown"

                for i in range(40):  # 40 * 15s = 10 min
                    time.sleep(15)
                    try:
                        api_result = subprocess.run(
                            ["curl", "-sk", "--connect-timeout", "3", "--max-time", "5",
                             f"https://{node.os_ip}:8443/1.0"],
                            capture_output=True, text=True, timeout=10,
                        )
                        if api_result.returncode == 0 and api_result.stdout.strip():
                            import json
                            data = json.loads(api_result.stdout)
                            api_version = data.get("metadata", {}).get("api_version", "unknown")
                            api_up = True
                            break
                    except Exception:
                        pass
                    click.echo(f"[{node.hostname}]   Attempt {i+1}/40...")

                if api_up:
                    steps_completed.append("api_ready")
                    click.echo(f"[{node.hostname}]   Incus API is up! Version: {api_version}")
                else:
                    raise Exception("Incus API did not come up within 10 minutes")
            else:
                click.echo(f"[{node.hostname}] Step 6/6: Skipping API wait (--skip-wait)")
                steps_completed.append("api_wait (skipped)")

            results.append({
                "node": node.hostname,
                "success": True,
                "data": {
                    "message": f"Incus OS installation complete on {hostname}",
                    "steps": steps_completed,
                    "os_ip": node.os_ip,
                    "api_port": 8443,
                },
            })

        except Exception as e:
            results.append({
                "node": node.hostname,
                "success": False,
                "error": str(e),
                "data": {"steps_completed": steps_completed},
            })

    from smcbmc.output import print_multi_node_results
    print_multi_node_results(results, json_mode=nctx.json_mode)
    if any(not r["success"] for r in results):
        sys.exit(1)


@rescue.command(name="wipe-disks")
@click.option("--confirm", is_flag=True, required=True,
              help="Required safety flag to confirm destructive disk wipe.")
@pass_context
def wipe_disks(nctx, confirm):
    """Wipe all NVMe and SATA disks on rescue node(s).

    Requires --confirm flag for safety. This is destructive and irreversible.
    """
    if not nctx.nodes:
        print_error("No nodes specified. Use --node, --nodes, or --all.", nctx.json_mode)
        sys.exit(1)

    results = []
    for node in nctx.nodes:
        if not node.os_ip:
            results.append({
                "node": node.hostname,
                "success": False,
                "error": "No os_ip configured",
            })
            continue

        command = (
            f"curl -sfL http://{PROVISIONING_SERVER}/rescue-scripts/wipe-disks.sh | sh"
        )

        click.echo(f"[{node.hostname}] Wiping disks on {node.os_ip}...")
        success, stdout, stderr = _run_ssh(node.os_ip, command)

        if success:
            results.append({
                "node": node.hostname,
                "success": True,
                "data": {
                    "message": f"Disk wipe completed on {node.hostname}",
                    "output": stdout.strip(),
                },
            })
        else:
            results.append({
                "node": node.hostname,
                "success": False,
                "error": f"Wipe failed: {stderr.strip()}\n{stdout.strip()[-200:]}",
            })

    from smcbmc.output import print_multi_node_results
    print_multi_node_results(results, json_mode=nctx.json_mode)
    if any(not r["success"] for r in results):
        sys.exit(1)


@rescue.command(name="disk-info")
@pass_context
def disk_info(nctx):
    """Run disk diagnostics on rescue node(s).

    Collects lsblk, nvme list, partition tables, SMART health,
    and PCI storage controller info.
    """
    if not nctx.nodes:
        print_error("No nodes specified. Use --node, --nodes, or --all.", nctx.json_mode)
        sys.exit(1)

    results = []
    for node in nctx.nodes:
        if not node.os_ip:
            results.append({
                "node": node.hostname,
                "success": False,
                "error": "No os_ip configured",
            })
            continue

        command = (
            f"curl -sfL http://{PROVISIONING_SERVER}/rescue-scripts/disk-info.sh | sh"
        )

        success, stdout, stderr = _run_ssh(node.os_ip, command)

        if success:
            results.append({
                "node": node.hostname,
                "success": True,
                "data": {"output": stdout.strip()},
            })
        else:
            results.append({
                "node": node.hostname,
                "success": False,
                "error": f"Disk info failed: {stderr.strip()}",
            })

    from smcbmc.output import print_multi_node_results
    print_multi_node_results(results, json_mode=nctx.json_mode)
    if any(not r["success"] for r in results):
        sys.exit(1)


def _ensure_sum(node_ip):
    """Ensure SUM is available on the rescue node. Downloads if missing."""
    check_cmd = "test -x /usr/local/bin/sum && echo OK"
    success, stdout, _ = _run_ssh(node_ip, check_cmd)
    if success and "OK" in stdout:
        return True

    # Download SUM from provisioning server
    dl_cmd = (
        "mkdir -p /opt/sum && "
        f"curl -sfL -o /opt/sum/sum http://{PROVISIONING_SERVER}/provisioning/ubuntu-rescue/tools/sum && "
        "chmod +x /opt/sum/sum && "
        "ln -sf /opt/sum/sum /usr/local/bin/sum && "
        "echo OK"
    )
    success, stdout, stderr = _run_ssh(node_ip, dl_cmd)
    if success and "OK" in stdout:
        return True
    return False


@rescue.command(name="bios-get")
@click.option("--output-dir", default=".", help="Local directory to save BIOS config XML files.")
@pass_context
def bios_get(nctx, output_dir):
    """Get BIOS configuration from rescue node(s) via in-band SUM.

    Runs SUM locally on each rescue node to extract the current BIOS
    configuration as XML, then copies it back to the management host.
    """
    if not nctx.nodes:
        print_error("No nodes specified. Use --node, --nodes, or --all.", nctx.json_mode)
        sys.exit(1)

    os.makedirs(output_dir, exist_ok=True)

    results = []
    for node in nctx.nodes:
        if not node.os_ip:
            results.append({
                "node": node.hostname,
                "success": False,
                "error": "No os_ip configured",
            })
            continue

        if not _ensure_sum(node.os_ip):
            results.append({
                "node": node.hostname,
                "success": False,
                "error": "Failed to install SUM on rescue node",
            })
            continue

        hostname = node.os_hostname or node.hostname
        remote_file = f"/tmp/{hostname}_bios_config.xml"

        # Run SUM in-band to get BIOS config
        cmd = f"sum -c GetCurrentBiosCfg --file {remote_file} 2>&1"
        click.echo(f"[{node.hostname}] Getting BIOS config via in-band SUM...")
        success, stdout, stderr = _run_ssh(node.os_ip, cmd)

        if not success:
            results.append({
                "node": node.hostname,
                "success": False,
                "error": f"SUM GetCurrentBiosCfg failed: {stdout.strip()} {stderr.strip()}",
            })
            continue

        # SCP the file back
        local_file = os.path.join(output_dir, f"{hostname}_bios_config.xml")
        scp_cmd = [
            "scp", *SSH_OPTS,
            "-i", SSH_KEY,
            f"root@{node.os_ip}:{remote_file}",
            local_file,
        ]
        try:
            scp_result = subprocess.run(scp_cmd, capture_output=True, text=True, timeout=60)
            if scp_result.returncode == 0:
                results.append({
                    "node": node.hostname,
                    "success": True,
                    "data": {
                        "message": f"BIOS config saved to {local_file}",
                        "file": local_file,
                    },
                })
            else:
                results.append({
                    "node": node.hostname,
                    "success": False,
                    "error": f"SCP failed: {scp_result.stderr.strip()}",
                })
        except Exception as e:
            results.append({
                "node": node.hostname,
                "success": False,
                "error": f"SCP failed: {e}",
            })

    from smcbmc.output import print_multi_node_results
    print_multi_node_results(results, json_mode=nctx.json_mode)
    if any(not r["success"] for r in results):
        sys.exit(1)


@rescue.command(name="bios-set")
@click.argument("config_file", type=click.Path(exists=True))
@click.option("--reboot", is_flag=True, help="Reboot after applying BIOS config.")
@pass_context
def bios_set(nctx, config_file, reboot):
    """Apply BIOS configuration to rescue node(s) via in-band SUM.

    Uploads a BIOS config XML file to each rescue node and applies it
    using SUM ChangeBiosCfg. Use --reboot to restart after applying.
    """
    if not nctx.nodes:
        print_error("No nodes specified. Use --node, --nodes, or --all.", nctx.json_mode)
        sys.exit(1)

    results = []
    for node in nctx.nodes:
        if not node.os_ip:
            results.append({
                "node": node.hostname,
                "success": False,
                "error": "No os_ip configured",
            })
            continue

        if not _ensure_sum(node.os_ip):
            results.append({
                "node": node.hostname,
                "success": False,
                "error": "Failed to install SUM on rescue node",
            })
            continue

        remote_file = "/tmp/bios_config_apply.xml"

        # SCP the config file to the rescue node
        scp_cmd = [
            "scp", *SSH_OPTS,
            "-i", SSH_KEY,
            config_file,
            f"root@{node.os_ip}:{remote_file}",
        ]
        try:
            scp_result = subprocess.run(scp_cmd, capture_output=True, text=True, timeout=60)
            if scp_result.returncode != 0:
                results.append({
                    "node": node.hostname,
                    "success": False,
                    "error": f"SCP upload failed: {scp_result.stderr.strip()}",
                })
                continue
        except Exception as e:
            results.append({
                "node": node.hostname,
                "success": False,
                "error": f"SCP upload failed: {e}",
            })
            continue

        # Apply BIOS config via in-band SUM
        cmd = f"sum -c ChangeBiosCfg --file {remote_file} --skip_unknown 2>&1"
        click.echo(f"[{node.hostname}] Applying BIOS config via in-band SUM...")
        success, stdout, stderr = _run_ssh(node.os_ip, cmd)

        if success:
            msg = f"BIOS config applied to {node.hostname}"
            if reboot:
                _run_ssh(node.os_ip, "reboot")
                msg += " (reboot initiated)"
            else:
                msg += " (reboot required for changes to take effect)"

            results.append({
                "node": node.hostname,
                "success": True,
                "data": {
                    "message": msg,
                    "sum_output": stdout.strip()[-500:],
                },
            })
        else:
            results.append({
                "node": node.hostname,
                "success": False,
                "error": f"SUM ChangeBiosCfg failed: {stdout.strip()} {stderr.strip()}",
            })

    from smcbmc.output import print_multi_node_results
    print_multi_node_results(results, json_mode=nctx.json_mode)
    if any(not r["success"] for r in results):
        sys.exit(1)


@rescue.command(name="bios-compare")
@click.option("--reference", type=click.Path(exists=True), default=None,
              help="Reference BIOS config XML to compare against. If not given, compares all nodes against the first.")
@click.option("--output-dir", default=".", help="Directory to save fetched BIOS configs.")
@pass_context
def bios_compare(nctx, reference, output_dir):
    """Compare BIOS configurations across rescue node(s).

    Fetches BIOS config from each node via in-band SUM and compares them.
    With --reference, compares each node against a reference file.
    Without --reference, compares all nodes against the first node.
    """
    if not nctx.nodes:
        print_error("No nodes specified. Use --node, --nodes, or --all.", nctx.json_mode)
        sys.exit(1)

    os.makedirs(output_dir, exist_ok=True)

    # Fetch BIOS configs from all nodes
    configs = {}
    for node in nctx.nodes:
        if not node.os_ip:
            click.echo(f"[{node.hostname}] Skipping: no os_ip configured", err=True)
            continue

        if not _ensure_sum(node.os_ip):
            click.echo(f"[{node.hostname}] Skipping: SUM not available", err=True)
            continue

        hostname = node.os_hostname or node.hostname
        remote_file = f"/tmp/{hostname}_bios_config.xml"

        cmd = f"sum -c GetCurrentBiosCfg --file {remote_file} 2>&1"
        click.echo(f"[{node.hostname}] Fetching BIOS config...")
        success, stdout, stderr = _run_ssh(node.os_ip, cmd)

        if not success:
            click.echo(f"[{node.hostname}] SUM failed: {stdout.strip()}", err=True)
            continue

        # Read the config content directly
        success, content, stderr = _run_ssh(node.os_ip, f"cat {remote_file}")
        if success:
            local_file = os.path.join(output_dir, f"{hostname}_bios_config.xml")
            with open(local_file, "w") as f:
                f.write(content)
            configs[hostname] = content
            click.echo(f"[{node.hostname}] Config saved to {local_file}")
        else:
            click.echo(f"[{node.hostname}] Failed to read config: {stderr.strip()}", err=True)

    if not configs:
        click.echo("No BIOS configs retrieved. Cannot compare.", err=True)
        sys.exit(1)

    # Load reference
    if reference:
        with open(reference) as f:
            ref_content = f.read()
        ref_name = os.path.basename(reference)
    else:
        # Use first node as reference
        ref_name = list(configs.keys())[0]
        ref_content = configs[ref_name]

    # Parse and compare XML configs
    click.echo(f"\n=== BIOS Configuration Comparison (reference: {ref_name}) ===\n")

    import xml.etree.ElementTree as ET

    try:
        ref_tree = ET.fromstring(ref_content)
    except ET.ParseError as e:
        click.echo(f"Failed to parse reference XML: {e}", err=True)
        sys.exit(1)

    # Build reference dict: setting_name -> value
    ref_settings = {}
    for elem in ref_tree.iter():
        name = elem.get("name", "")
        value = elem.get("selectedOption", elem.get("numericValue", ""))
        if name and value:
            ref_settings[name] = value

    all_identical = True
    for hostname, content in configs.items():
        if hostname == ref_name and not reference:
            continue

        try:
            tree = ET.fromstring(content)
        except ET.ParseError:
            click.echo(f"[{hostname}] Failed to parse BIOS config XML")
            all_identical = False
            continue

        node_settings = {}
        for elem in tree.iter():
            name = elem.get("name", "")
            value = elem.get("selectedOption", elem.get("numericValue", ""))
            if name and value:
                node_settings[name] = value

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
        click.echo("\nDifferences found. Use 'rescue bios-set' to apply a uniform config.")
        sys.exit(1)


@rescue.command(name="bmc-reset")
@click.option("--cold", is_flag=True, help="Perform cold reset (full BMC restart) instead of warm reset.")
@pass_context
def bmc_reset(nctx, cold):
    """Reset BMC from rescue node(s) via in-band ipmitool.

    Performs a warm BMC reset by default. Use --cold for a full BMC restart.
    """
    if not nctx.nodes:
        print_error("No nodes specified. Use --node, --nodes, or --all.", nctx.json_mode)
        sys.exit(1)

    reset_type = "cold" if cold else "warm"

    results = []
    for node in nctx.nodes:
        if not node.os_ip:
            results.append({
                "node": node.hostname,
                "success": False,
                "error": "No os_ip configured",
            })
            continue

        cmd = f"ipmitool mc reset {reset_type} 2>&1"
        click.echo(f"[{node.hostname}] Performing BMC {reset_type} reset...")
        success, stdout, stderr = _run_ssh(node.os_ip, cmd)

        if success:
            results.append({
                "node": node.hostname,
                "success": True,
                "data": {
                    "message": f"BMC {reset_type} reset initiated on {node.hostname}",
                    "output": stdout.strip(),
                },
            })
        else:
            results.append({
                "node": node.hostname,
                "success": False,
                "error": f"BMC reset failed: {stdout.strip()} {stderr.strip()}",
            })

    from smcbmc.output import print_multi_node_results
    print_multi_node_results(results, json_mode=nctx.json_mode)
    if any(not r["success"] for r in results):
        sys.exit(1)


@rescue.command(name="ipmi-raw")
@click.argument("raw_cmd")
@pass_context
def ipmi_raw(nctx, raw_cmd):
    """Run raw ipmitool command on rescue node(s) in-band.

    \b
    Examples:
      rescue ipmi-raw 'mc info'
      rescue ipmi-raw 'chassis status'
      rescue ipmi-raw 'sensor list'
      rescue ipmi-raw 'raw 0x30 0x48 0x01 0x00'
    """
    if not nctx.nodes:
        print_error("No nodes specified. Use --node, --nodes, or --all.", nctx.json_mode)
        sys.exit(1)

    results = []
    for node in nctx.nodes:
        if not node.os_ip:
            results.append({
                "node": node.hostname,
                "success": False,
                "error": "No os_ip configured",
            })
            continue

        cmd = f"ipmitool {raw_cmd} 2>&1"
        success, stdout, stderr = _run_ssh(node.os_ip, cmd)

        if success:
            results.append({
                "node": node.hostname,
                "success": True,
                "data": {"output": stdout.strip()},
            })
        else:
            results.append({
                "node": node.hostname,
                "success": False,
                "error": f"ipmitool failed: {stdout.strip()} {stderr.strip()}",
            })

    from smcbmc.output import print_multi_node_results
    print_multi_node_results(results, json_mode=nctx.json_mode)
    if any(not r["success"] for r in results):
        sys.exit(1)


@rescue.command(name="tpm-clear")
@click.option("--confirm", is_flag=True, required=True,
              help="Required safety flag to confirm TPM clear.")
@pass_context
def tpm_clear(nctx, confirm):
    """Clear TPM 2.0 on rescue node(s) via in-band tpm2-tools.

    Attempts tpm2_clear via lockout hierarchy (most common).
    If that fails, falls back to PPI request (requires reboot).
    """
    if not nctx.nodes:
        print_error("No nodes specified. Use --node, --nodes, or --all.", nctx.json_mode)
        sys.exit(1)

    results = []
    for node in nctx.nodes:
        if not node.os_ip:
            results.append({
                "node": node.hostname,
                "success": False,
                "error": "No os_ip configured",
            })
            continue

        cmd = (
            "if ! command -v tpm2_clear >/dev/null 2>&1; then "
            "  echo 'ERROR: tpm2-tools not installed'; exit 1; "
            "fi; "
            "echo 'TPM info:'; tpm2_getcap properties-fixed 2>&1 | grep -E 'TPM2_PT_MANUFACTURER|TPM2_PT_FIRMWARE' || true; "
            "echo '---'; "
            "if tpm2_clear 2>&1; then "
            "  echo 'TPM_CLEAR_OK: TPM cleared via lockout hierarchy'; "
            "else "
            "  echo 'Lockout clear failed, trying PPI request...'; "
            "  if [ -f /sys/class/tpm/tpm0/ppi/request ]; then "
            "    echo 5 > /sys/class/tpm/tpm0/ppi/request && "
            "    echo 'TPM_PPI_OK: PPI clear request submitted (clear happens on next reboot)'; "
            "  else "
            "    echo 'ERROR: PPI interface not available'; exit 1; "
            "  fi; "
            "fi"
        )

        click.echo(f"[{node.hostname}] Clearing TPM on {node.os_ip}...")
        success, stdout, stderr = _run_ssh(node.os_ip, cmd)

        if success and ("TPM_CLEAR_OK" in stdout or "TPM_PPI_OK" in stdout):
            method = "lockout hierarchy" if "TPM_CLEAR_OK" in stdout else "PPI (reboot required)"
            results.append({
                "node": node.hostname,
                "success": True,
                "data": {
                    "message": f"TPM cleared on {node.hostname} via {method}",
                    "output": stdout.strip(),
                },
            })
        else:
            results.append({
                "node": node.hostname,
                "success": False,
                "error": f"TPM clear failed: {stdout.strip()} {stderr.strip()}",
            })

    from smcbmc.output import print_multi_node_results
    print_multi_node_results(results, json_mode=nctx.json_mode)
    if any(not r["success"] for r in results):
        sys.exit(1)


@rescue.command(name="tpm-status")
@pass_context
def tpm_status(nctx):
    """Check TPM 2.0 status on rescue node(s)."""
    if not nctx.nodes:
        print_error("No nodes specified. Use --node, --nodes, or --all.", nctx.json_mode)
        sys.exit(1)

    results = []
    for node in nctx.nodes:
        if not node.os_ip:
            results.append({
                "node": node.hostname,
                "success": False,
                "error": "No os_ip configured",
            })
            continue

        cmd = (
            "echo '=== TPM Device ==='; "
            "ls -la /dev/tpm* 2>/dev/null || echo 'No TPM device found'; "
            "echo ''; "
            "echo '=== TPM Properties ==='; "
            "tpm2_getcap properties-fixed 2>&1 | grep -A2 -E 'TPM2_PT_MANUFACTURER|TPM2_PT_FIRMWARE|TPM2_PT_FAMILY|TPM2_PT_VENDOR_STRING|TPM2_PT_REVISION' || echo 'tpm2_getcap failed'; "
            "echo ''; "
            "echo '=== PCR Values (bank SHA256) ==='; "
            "tpm2_pcrread sha256 2>&1 | head -20 || echo 'tpm2_pcrread failed'; "
            "echo ''; "
            "echo '=== PPI Interface ==='; "
            "cat /sys/class/tpm/tpm0/ppi/request 2>/dev/null || echo 'PPI not available'"
        )

        success, stdout, stderr = _run_ssh(node.os_ip, cmd)
        if success:
            results.append({
                "node": node.hostname,
                "success": True,
                "data": {"output": stdout.strip()},
            })
        else:
            results.append({
                "node": node.hostname,
                "success": False,
                "error": f"TPM status check failed: {stderr.strip()}",
            })

    from smcbmc.output import print_multi_node_results
    print_multi_node_results(results, json_mode=nctx.json_mode)
    if any(not r["success"] for r in results):
        sys.exit(1)


@rescue.command(name="secureboot-status")
@pass_context
def secureboot_status(nctx):
    """Check Secure Boot status and keys on rescue node(s)."""
    if not nctx.nodes:
        print_error("No nodes specified. Use --node, --nodes, or --all.", nctx.json_mode)
        sys.exit(1)

    results = []
    for node in nctx.nodes:
        if not node.os_ip:
            results.append({
                "node": node.hostname,
                "success": False,
                "error": "No os_ip configured",
            })
            continue

        cmd = (
            "echo '=== EFI Mode ==='; "
            "if [ -d /sys/firmware/efi ]; then echo 'UEFI boot: yes'; else echo 'UEFI boot: no (legacy BIOS)'; fi; "
            "echo ''; "
            "echo '=== Secure Boot State ==='; "
            "if [ -f /sys/firmware/efi/efivars/SecureBoot-8be4df61-93ca-11d2-aa0d-00e098032b8c ]; then "
            "  SB=$(od -An -t u1 -j4 -N1 /sys/firmware/efi/efivars/SecureBoot-8be4df61-93ca-11d2-aa0d-00e098032b8c 2>/dev/null | tr -d ' '); "
            "  if [ \"$SB\" = '1' ]; then echo 'Secure Boot: ENABLED'; else echo 'Secure Boot: DISABLED'; fi; "
            "else "
            "  echo 'Secure Boot: status unavailable'; "
            "fi; "
            "echo ''; "
            "echo '=== Setup Mode ==='; "
            "if [ -f /sys/firmware/efi/efivars/SetupMode-8be4df61-93ca-11d2-aa0d-00e098032b8c ]; then "
            "  SM=$(od -An -t u1 -j4 -N1 /sys/firmware/efi/efivars/SetupMode-8be4df61-93ca-11d2-aa0d-00e098032b8c 2>/dev/null | tr -d ' '); "
            "  if [ \"$SM\" = '1' ]; then echo 'Setup Mode: YES (keys can be enrolled)'; else echo 'Setup Mode: NO (User Mode)'; fi; "
            "else "
            "  echo 'Setup Mode: status unavailable'; "
            "fi; "
            "echo ''; "
            "echo '=== Enrolled Keys ==='; "
            "efi-readvar 2>&1 || echo 'efi-readvar not available'; "
            "echo ''; "
            "echo '=== EFI Boot Entries ==='; "
            "efibootmgr -v 2>&1 || echo 'efibootmgr not available'"
        )

        success, stdout, stderr = _run_ssh(node.os_ip, cmd)
        if success:
            results.append({
                "node": node.hostname,
                "success": True,
                "data": {"output": stdout.strip()},
            })
        else:
            results.append({
                "node": node.hostname,
                "success": False,
                "error": f"Secure boot check failed: {stderr.strip()}",
            })

    from smcbmc.output import print_multi_node_results
    print_multi_node_results(results, json_mode=nctx.json_mode)
    if any(not r["success"] for r in results):
        sys.exit(1)


@rescue.command(name="bmc-get")
@click.option("--output-dir", default=".", help="Local directory to save BMC config files.")
@pass_context
def bmc_get(nctx, output_dir):
    """Get BMC configuration from rescue node(s) via in-band SUM.

    Runs SUM locally on each rescue node to extract the current BMC
    configuration, then copies it back to the management host.
    """
    if not nctx.nodes:
        print_error("No nodes specified. Use --node, --nodes, or --all.", nctx.json_mode)
        sys.exit(1)

    os.makedirs(output_dir, exist_ok=True)

    results = []
    for node in nctx.nodes:
        if not node.os_ip:
            results.append({
                "node": node.hostname,
                "success": False,
                "error": "No os_ip configured",
            })
            continue

        if not _ensure_sum(node.os_ip):
            results.append({
                "node": node.hostname,
                "success": False,
                "error": "Failed to install SUM on rescue node",
            })
            continue

        hostname = node.os_hostname or node.hostname
        remote_file = f"/tmp/{hostname}_bmc_config.xml"

        cmd = f"sum -c GetBmcCfg --file {remote_file} 2>&1"
        click.echo(f"[{node.hostname}] Getting BMC config via in-band SUM...")
        success, stdout, stderr = _run_ssh(node.os_ip, cmd)

        if not success:
            results.append({
                "node": node.hostname,
                "success": False,
                "error": f"SUM GetBmcCfg failed: {stdout.strip()} {stderr.strip()}",
            })
            continue

        # SCP the file back
        local_file = os.path.join(output_dir, f"{hostname}_bmc_config.xml")
        scp_cmd = [
            "scp", *SSH_OPTS,
            "-i", SSH_KEY,
            f"root@{node.os_ip}:{remote_file}",
            local_file,
        ]
        try:
            scp_result = subprocess.run(scp_cmd, capture_output=True, text=True, timeout=60)
            if scp_result.returncode == 0:
                results.append({
                    "node": node.hostname,
                    "success": True,
                    "data": {
                        "message": f"BMC config saved to {local_file}",
                        "file": local_file,
                    },
                })
            else:
                results.append({
                    "node": node.hostname,
                    "success": False,
                    "error": f"SCP failed: {scp_result.stderr.strip()}",
                })
        except Exception as e:
            results.append({
                "node": node.hostname,
                "success": False,
                "error": f"SCP failed: {e}",
            })

    from smcbmc.output import print_multi_node_results
    print_multi_node_results(results, json_mode=nctx.json_mode)
    if any(not r["success"] for r in results):
        sys.exit(1)


@rescue.command(name="bmc-set")
@click.argument("config_file", type=click.Path(exists=True))
@pass_context
def bmc_set(nctx, config_file):
    """Apply BMC configuration to rescue node(s) via in-band SUM.

    Uploads a BMC config file to each rescue node and applies it
    using SUM ChangeBmcCfg.
    """
    if not nctx.nodes:
        print_error("No nodes specified. Use --node, --nodes, or --all.", nctx.json_mode)
        sys.exit(1)

    results = []
    for node in nctx.nodes:
        if not node.os_ip:
            results.append({
                "node": node.hostname,
                "success": False,
                "error": "No os_ip configured",
            })
            continue

        if not _ensure_sum(node.os_ip):
            results.append({
                "node": node.hostname,
                "success": False,
                "error": "Failed to install SUM on rescue node",
            })
            continue

        remote_file = "/tmp/bmc_config_apply.xml"

        scp_cmd = [
            "scp", *SSH_OPTS,
            "-i", SSH_KEY,
            config_file,
            f"root@{node.os_ip}:{remote_file}",
        ]
        try:
            scp_result = subprocess.run(scp_cmd, capture_output=True, text=True, timeout=60)
            if scp_result.returncode != 0:
                results.append({
                    "node": node.hostname,
                    "success": False,
                    "error": f"SCP upload failed: {scp_result.stderr.strip()}",
                })
                continue
        except Exception as e:
            results.append({
                "node": node.hostname,
                "success": False,
                "error": f"SCP upload failed: {e}",
            })
            continue

        cmd = f"sum -c ChangeBmcCfg --file {remote_file} 2>&1"
        click.echo(f"[{node.hostname}] Applying BMC config via in-band SUM...")
        success, stdout, stderr = _run_ssh(node.os_ip, cmd)

        if success:
            results.append({
                "node": node.hostname,
                "success": True,
                "data": {
                    "message": f"BMC config applied to {node.hostname}",
                    "sum_output": stdout.strip()[-500:],
                },
            })
        else:
            results.append({
                "node": node.hostname,
                "success": False,
                "error": f"SUM ChangeBmcCfg failed: {stdout.strip()} {stderr.strip()}",
            })

    from smcbmc.output import print_multi_node_results
    print_multi_node_results(results, json_mode=nctx.json_mode)
    if any(not r["success"] for r in results):
        sys.exit(1)


@rescue.command(name="bmc-compare")
@click.option("--reference", type=click.Path(exists=True), default=None,
              help="Reference BMC config file. If not given, compares all against the first node.")
@click.option("--output-dir", default=".", help="Directory to save fetched BMC configs.")
@click.option("--include-network", is_flag=True,
              help="Include network/IP settings in comparison (excluded by default).")
@pass_context
def bmc_compare(nctx, reference, output_dir, include_network):
    """Compare BMC configurations across rescue node(s) via in-band SUM.

    By default, skips node-specific settings (hostname, MAC, IP).
    Use --include-network to include them.
    """
    if not nctx.nodes:
        print_error("No nodes specified. Use --node, --nodes, or --all.", nctx.json_mode)
        sys.exit(1)

    os.makedirs(output_dir, exist_ok=True)

    configs = {}
    for node in nctx.nodes:
        if not node.os_ip:
            click.echo(f"[{node.hostname}] Skipping: no os_ip configured", err=True)
            continue

        if not _ensure_sum(node.os_ip):
            click.echo(f"[{node.hostname}] Skipping: SUM not available", err=True)
            continue

        hostname = node.os_hostname or node.hostname
        remote_file = f"/tmp/{hostname}_bmc_config.xml"

        cmd = f"sum -c GetBmcCfg --file {remote_file} 2>&1"
        click.echo(f"[{node.hostname}] Fetching BMC config...")
        success, stdout, stderr = _run_ssh(node.os_ip, cmd)

        if not success:
            click.echo(f"[{node.hostname}] SUM failed: {stdout.strip()}", err=True)
            continue

        success, content, stderr = _run_ssh(node.os_ip, f"cat {remote_file}")
        if success:
            local_file = os.path.join(output_dir, f"{hostname}_bmc_config.xml")
            with open(local_file, "w") as f:
                f.write(content)
            configs[hostname] = content
            click.echo(f"[{node.hostname}] Config saved to {local_file}")
        else:
            click.echo(f"[{node.hostname}] Failed to read config: {stderr.strip()}", err=True)

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

    from smcbmc.commands.bmc import _parse_config_settings, BMC_SKIP_SECTIONS, BMC_SKIP_SUBSTRINGS

    ref_settings = _parse_config_settings(ref_content)
    if not ref_settings:
        click.echo("Failed to parse reference config (no settings found).", err=True)
        sys.exit(1)

    def _should_skip_bmc(key, include_net):
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
            if _should_skip_bmc(key, include_network):
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
        click.echo("\nDifferences found. Use 'rescue bmc-set' to apply a uniform config.")
        sys.exit(1)


@rescue.command(name="audit")
@click.option("--output-dir", default=".", help="Directory to save audit data.")
@pass_context
def audit(nctx, output_dir):
    """Comprehensive configuration audit across rescue node(s).

    Collects and compares across all targeted nodes:
      - BIOS firmware version (via SUM GetBIOSInfo)
      - BMC firmware version (via SUM GetBmcInfo)
      - BIOS configuration (via SUM GetCurrentBiosCfg)
      - BMC configuration (via SUM GetBmcCfg)
      - DMI/SMBIOS info (board model, BIOS version)

    Reports any differences found.
    """
    if not nctx.nodes:
        print_error("No nodes specified. Use --node, --nodes, or --all.", nctx.json_mode)
        sys.exit(1)

    os.makedirs(output_dir, exist_ok=True)

    from smcbmc.commands.bmc import _parse_config_settings, BMC_SKIP_SECTIONS, BMC_SKIP_SUBSTRINGS
    from smcbmc.commands.bios import _parse_bios_settings

    # Collect data from all nodes
    node_data = {}
    for node in nctx.nodes:
        if not node.os_ip:
            click.echo(f"[{node.hostname}] Skipping: no os_ip configured", err=True)
            continue

        if not _ensure_sum(node.os_ip):
            click.echo(f"[{node.hostname}] Skipping: SUM not available", err=True)
            continue

        hostname = node.os_hostname or node.hostname
        data = {"hostname": hostname}
        click.echo(f"\n[{hostname}] Collecting audit data...")

        # 1. BIOS firmware info
        click.echo(f"[{hostname}]   BIOS firmware info...")
        success, stdout, stderr = _run_ssh(node.os_ip, "sum -c GetBIOSInfo 2>&1")
        if success:
            data["bios_info"] = stdout.strip()
        else:
            data["bios_info"] = f"FAILED: {stdout.strip()}"

        # 2. BMC firmware info
        click.echo(f"[{hostname}]   BMC firmware info...")
        success, stdout, stderr = _run_ssh(node.os_ip, "sum -c GetBmcInfo 2>&1")
        if success:
            data["bmc_info"] = stdout.strip()
        else:
            data["bmc_info"] = f"FAILED: {stdout.strip()}"

        # 3. BIOS config
        click.echo(f"[{hostname}]   BIOS configuration...")
        remote_bios = f"/tmp/{hostname}_audit_bios.xml"
        success, stdout, stderr = _run_ssh(
            node.os_ip, f"sum -c GetCurrentBiosCfg --file {remote_bios} 2>&1"
        )
        if success:
            ok, content, _ = _run_ssh(node.os_ip, f"cat {remote_bios}")
            if ok:
                local_bios = os.path.join(output_dir, f"{hostname}_audit_bios.xml")
                with open(local_bios, "w") as f:
                    f.write(content)
                data["bios_config"] = _parse_bios_settings(content)
        if "bios_config" not in data:
            data["bios_config"] = {}

        # 4. BMC config
        click.echo(f"[{hostname}]   BMC configuration...")
        remote_bmc = f"/tmp/{hostname}_audit_bmc.xml"
        success, stdout, stderr = _run_ssh(
            node.os_ip, f"sum -c GetBmcCfg --file {remote_bmc} 2>&1"
        )
        if success:
            ok, content, _ = _run_ssh(node.os_ip, f"cat {remote_bmc}")
            if ok:
                local_bmc = os.path.join(output_dir, f"{hostname}_audit_bmc.xml")
                with open(local_bmc, "w") as f:
                    f.write(content)
                data["bmc_config"] = _parse_config_settings(content)
        if "bmc_config" not in data:
            data["bmc_config"] = {}

        # 5. DMI info
        click.echo(f"[{hostname}]   DMI/SMBIOS info...")
        success, stdout, stderr = _run_ssh(
            node.os_ip,
            "dmidecode -t bios -t system -t baseboard 2>/dev/null | "
            "grep -E 'Manufacturer|Product|Version|Serial|Release|UUID' || "
            "echo 'dmidecode not available'"
        )
        if success:
            data["dmi_info"] = stdout.strip()
        else:
            data["dmi_info"] = f"FAILED: {stderr.strip()}"

        node_data[hostname] = data

    if not node_data:
        click.echo("\nNo nodes audited.", err=True)
        sys.exit(1)

    # Compare results
    click.echo("\n" + "=" * 70)
    click.echo("  CONFIGURATION AUDIT REPORT")
    click.echo("=" * 70)

    ref_name = list(node_data.keys())[0]
    ref = node_data[ref_name]
    has_diffs = False

    # --- Firmware versions ---
    click.echo(f"\n--- Firmware Versions ---\n")
    for hostname, data in node_data.items():
        click.echo(f"[{hostname}]")
        # Extract version lines from info output
        for line in data.get("bios_info", "").splitlines():
            line = line.strip()
            if line and not line.startswith("--") and not line.startswith("Supermicro"):
                if any(kw in line.lower() for kw in ["version", "date", "bios", "build"]):
                    click.echo(f"  BIOS: {line}")
        for line in data.get("bmc_info", "").splitlines():
            line = line.strip()
            if line and not line.startswith("--") and not line.startswith("Supermicro"):
                if any(kw in line.lower() for kw in ["version", "date", "bmc", "build", "firmware"]):
                    click.echo(f"  BMC:  {line}")

    # --- BIOS config comparison ---
    click.echo(f"\n--- BIOS Configuration (reference: {ref_name}) ---\n")
    ref_bios = ref.get("bios_config", {})
    if ref_bios:
        bios_identical = True
        for hostname, data in node_data.items():
            if hostname == ref_name:
                continue
            node_bios = data.get("bios_config", {})
            diffs = []
            for key in sorted(set(ref_bios.keys()) | set(node_bios.keys())):
                ref_val = ref_bios.get(key, "<missing>")
                node_val = node_bios.get(key, "<missing>")
                if ref_val != node_val:
                    diffs.append(f"    {key}: {ref_val} -> {node_val}")
            if diffs:
                bios_identical = False
                has_diffs = True
                click.echo(f"  [{hostname}] {len(diffs)} difference(s):")
                for d in diffs:
                    click.echo(d)
            else:
                click.echo(f"  [{hostname}] IDENTICAL")
        if bios_identical:
            click.echo("  All nodes have identical BIOS configuration.")
    else:
        click.echo("  Could not retrieve reference BIOS config.")

    # --- BMC config comparison ---
    click.echo(f"\n--- BMC Configuration (reference: {ref_name}) ---\n")
    ref_bmc = ref.get("bmc_config", {})
    if ref_bmc:
        def _should_skip_bmc_audit(key):
            if any(sub in key for sub in BMC_SKIP_SUBSTRINGS):
                return True
            for section in BMC_SKIP_SECTIONS:
                if f"/{section}/" in key or key.startswith(f"{section}/"):
                    return True
            return False

        bmc_identical = True
        for hostname, data in node_data.items():
            if hostname == ref_name:
                continue
            node_bmc = data.get("bmc_config", {})

            diffs = []
            for key in sorted(set(ref_bmc.keys()) | set(node_bmc.keys())):
                if _should_skip_bmc_audit(key):
                    continue
                ref_val = ref_bmc.get(key, "<missing>")
                node_val = node_bmc.get(key, "<missing>")
                if ref_val != node_val:
                    diffs.append(f"    {key}: {ref_val} -> {node_val}")
            if diffs:
                bmc_identical = False
                has_diffs = True
                click.echo(f"  [{hostname}] {len(diffs)} difference(s):")
                for d in diffs:
                    click.echo(d)
            else:
                click.echo(f"  [{hostname}] IDENTICAL")
        if bmc_identical:
            click.echo("  All nodes have identical BMC configuration.")
    else:
        click.echo("  Could not retrieve reference BMC config.")

    # --- DMI summary ---
    click.echo(f"\n--- DMI/Hardware Info ---\n")
    for hostname, data in node_data.items():
        click.echo(f"  [{hostname}]")
        for line in data.get("dmi_info", "").splitlines():
            line = line.strip()
            if line and "Serial" not in line and "UUID" not in line:
                click.echo(f"    {line}")

    click.echo("\n" + "=" * 70)
    if has_diffs:
        click.echo("  RESULT: Differences found -- see above for details")
        click.echo("=" * 70)
        sys.exit(1)
    else:
        click.echo("  RESULT: All nodes have identical configuration")
        click.echo("=" * 70)


@rescue.command(name="hw-check")
@pass_context
def hw_check(nctx):
    """Run comprehensive hardware diagnostics on rescue node(s).

    Checks memory (DIMMs, ECC/EDAC), CPU, temperatures, voltages,
    fans, IPMI event log, storage SMART, PCI devices, and lshw summary.
    """
    if not nctx.nodes:
        print_error("No nodes specified. Use --node, --nodes, or --all.", nctx.json_mode)
        sys.exit(1)

    results = []
    for node in nctx.nodes:
        if not node.os_ip:
            results.append({
                "node": node.hostname,
                "success": False,
                "error": "No os_ip configured",
            })
            continue

        command = (
            f"curl -sfL http://{PROVISIONING_SERVER}/rescue-scripts/hw-check.sh | sh"
        )

        click.echo(f"[{node.hostname}] Running hardware diagnostics on {node.os_ip}...")
        success, stdout, stderr = _run_ssh(node.os_ip, command)

        if success:
            results.append({
                "node": node.hostname,
                "success": True,
                "data": {"output": stdout.strip()},
            })
        else:
            results.append({
                "node": node.hostname,
                "success": False,
                "error": f"Hardware check failed: {stderr.strip()}",
            })

    from smcbmc.output import print_multi_node_results
    print_multi_node_results(results, json_mode=nctx.json_mode)
    if any(not r["success"] for r in results):
        sys.exit(1)


@rescue.command(name="hw-stress")
@click.option("--duration", default=60, type=int,
              help="Duration of each stress test in seconds (default: 60).")
@click.option("--memory-mb", default=256, type=int,
              help="Memory to test with memtester in MB (default: 256).")
@click.option("--skip-memory", is_flag=True, help="Skip memtester.")
@click.option("--skip-cpu", is_flag=True, help="Skip CPU stress test.")
@click.option("--skip-io", is_flag=True, help="Skip fio I/O stress test.")
@pass_context
def hw_stress(nctx, duration, memory_mb, skip_memory, skip_cpu, skip_io):
    """Run hardware stress tests on rescue node(s).

    \b
    Runs the following tests (all skippable):
      1. memtester - Tests RAM integrity (default 256MB, 1 iteration)
      2. stress-ng - CPU + memory stress (default 60s)
      3. fio - Storage I/O stress (default 60s, 4k random read/write)

    Results include pass/fail status and any errors detected.
    """
    if not nctx.nodes:
        print_error("No nodes specified. Use --node, --nodes, or --all.", nctx.json_mode)
        sys.exit(1)

    results = []
    for node in nctx.nodes:
        if not node.os_ip:
            results.append({
                "node": node.hostname,
                "success": False,
                "error": "No os_ip configured",
            })
            continue

        tests_run = []
        tests_passed = []
        tests_failed = []
        full_output = []

        # 1. memtester
        if not skip_memory:
            click.echo(f"[{node.hostname}] Running memtester ({memory_mb}MB, 1 iteration)...")
            cmd = f"memtester {memory_mb}M 1 2>&1"
            success, stdout, stderr = _run_ssh(node.os_ip, cmd)
            tests_run.append("memtester")
            if success and "Done" in stdout:
                tests_passed.append("memtester")
                full_output.append(f"=== memtester: PASS ({memory_mb}MB) ===")
            else:
                tests_failed.append("memtester")
                full_output.append(f"=== memtester: FAIL ===\n{stdout.strip()}")

        # 2. stress-ng (CPU + memory)
        if not skip_cpu:
            click.echo(f"[{node.hostname}] Running stress-ng CPU test ({duration}s)...")
            nproc_ok, nproc_out, _ = _run_ssh(node.os_ip, "nproc")
            cores = nproc_out.strip() if nproc_ok else "2"
            cmd = f"stress-ng --cpu {cores} --cpu-method all --metrics-brief --timeout {duration}s 2>&1"
            success, stdout, stderr = _run_ssh(node.os_ip, cmd)
            tests_run.append("stress-ng-cpu")
            if success:
                tests_passed.append("stress-ng-cpu")
                full_output.append(f"=== stress-ng CPU: PASS ({duration}s, {cores} workers) ===")
                # Show metrics
                for line in stdout.strip().splitlines():
                    if "bogo" in line.lower() or "metric" in line.lower() or "stress-ng" in line:
                        full_output.append(f"  {line.strip()}")
            else:
                tests_failed.append("stress-ng-cpu")
                full_output.append(f"=== stress-ng CPU: FAIL ===\n{stdout.strip()[-500:]}")

        # 3. fio (I/O stress)
        if not skip_io:
            click.echo(f"[{node.hostname}] Running fio I/O stress test ({duration}s)...")
            cmd = (
                f"fio --name=hw-stress-test --rw=randrw --bs=4k --size=256M "
                f"--numjobs=4 --time_based --runtime={duration} --group_reporting "
                f"--directory=/tmp --output-format=normal 2>&1"
            )
            success, stdout, stderr = _run_ssh(node.os_ip, cmd)
            tests_run.append("fio-io")
            if success:
                tests_passed.append("fio-io")
                full_output.append(f"=== fio I/O: PASS ({duration}s) ===")
                for line in stdout.strip().splitlines():
                    if any(kw in line.lower() for kw in ["read:", "write:", "iops", "bw=", "err="]):
                        full_output.append(f"  {line.strip()}")
            else:
                tests_failed.append("fio-io")
                full_output.append(f"=== fio I/O: FAIL ===\n{stdout.strip()[-500:]}")

        # Check for new EDAC/ECC errors after stress
        if not skip_memory or not skip_cpu:
            click.echo(f"[{node.hostname}] Checking for post-stress ECC errors...")
            cmd = (
                "echo '=== Post-stress ECC check ==='; "
                "if [ -d /sys/devices/system/edac/mc ]; then "
                "  for mc in /sys/devices/system/edac/mc/mc*; do "
                "    ce=$(cat $mc/ce_count 2>/dev/null); "
                "    ue=$(cat $mc/ue_count 2>/dev/null); "
                "    echo \"  $(basename $mc): CE=$ce UE=$ue\"; "
                "  done; "
                "else echo '  EDAC not available'; fi; "
                "dmesg | grep -i 'ecc\\|mce\\|hardware error' | tail -5 || true"
            )
            success, stdout, _ = _run_ssh(node.os_ip, cmd)
            if success:
                full_output.append(stdout.strip())

        overall_pass = len(tests_failed) == 0
        summary = (
            f"Tests run: {len(tests_run)}, "
            f"Passed: {len(tests_passed)}, "
            f"Failed: {len(tests_failed)}"
        )

        results.append({
            "node": node.hostname,
            "success": overall_pass,
            "data": {
                "summary": summary,
                "tests_passed": tests_passed,
                "tests_failed": tests_failed,
                "output": "\n".join(full_output),
            },
        })

    from smcbmc.output import print_multi_node_results
    print_multi_node_results(results, json_mode=nctx.json_mode)
    if any(not r["success"] for r in results):
        sys.exit(1)


@rescue.command(name="efi-boot-pxe")
@pass_context
def efi_boot_pxe(nctx):
    """Set next boot to PXE via in-band efibootmgr on rescue node(s).

    Uses efibootmgr to find the PXE/network boot entry and set it
    as the next boot option, without changing the persistent boot order.
    """
    if not nctx.nodes:
        print_error("No nodes specified. Use --node, --nodes, or --all.", nctx.json_mode)
        sys.exit(1)

    results = []
    for node in nctx.nodes:
        if not node.os_ip:
            results.append({
                "node": node.hostname,
                "success": False,
                "error": "No os_ip configured",
            })
            continue

        cmd = (
            "if ! command -v efibootmgr >/dev/null 2>&1; then "
            "  echo 'ERROR: efibootmgr not installed'; exit 1; "
            "fi; "
            "PXE_ENTRY=$(efibootmgr -v 2>/dev/null | grep -iE 'PXE|Network|IPv4|UEFI.*LAN|EFI Network' | head -1 | grep -o 'Boot[0-9A-Fa-f]*' | sed 's/Boot//'); "
            "if [ -z \"$PXE_ENTRY\" ]; then "
            "  echo 'Current boot entries:'; efibootmgr -v 2>&1; "
            "  echo ''; echo 'ERROR: No PXE/Network boot entry found'; exit 1; "
            "fi; "
            "echo \"Found PXE entry: Boot${PXE_ENTRY}\"; "
            "efibootmgr -n \"$PXE_ENTRY\" 2>&1 && "
            "echo \"EFI_BOOT_PXE_OK: BootNext set to ${PXE_ENTRY}\" && "
            "echo ''; echo 'Current state:'; efibootmgr 2>&1"
        )

        click.echo(f"[{node.hostname}] Setting EFI BootNext to PXE...")
        success, stdout, stderr = _run_ssh(node.os_ip, cmd)

        if success and "EFI_BOOT_PXE_OK" in stdout:
            results.append({
                "node": node.hostname,
                "success": True,
                "data": {
                    "message": f"EFI BootNext set to PXE on {node.hostname}",
                    "output": stdout.strip(),
                },
            })
        else:
            results.append({
                "node": node.hostname,
                "success": False,
                "error": f"efibootmgr failed: {stdout.strip()} {stderr.strip()}",
            })

    from smcbmc.output import print_multi_node_results
    print_multi_node_results(results, json_mode=nctx.json_mode)
    if any(not r["success"] for r in results):
        sys.exit(1)


@rescue.command(name="efi-boot-disk")
@pass_context
def efi_boot_disk(nctx):
    """Set next boot to first disk via in-band efibootmgr on rescue node(s).

    Uses efibootmgr to find the first disk boot entry and set it
    as the next boot option.
    """
    if not nctx.nodes:
        print_error("No nodes specified. Use --node, --nodes, or --all.", nctx.json_mode)
        sys.exit(1)

    results = []
    for node in nctx.nodes:
        if not node.os_ip:
            results.append({
                "node": node.hostname,
                "success": False,
                "error": "No os_ip configured",
            })
            continue

        cmd = (
            "if ! command -v efibootmgr >/dev/null 2>&1; then "
            "  echo 'ERROR: efibootmgr not installed'; exit 1; "
            "fi; "
            "DISK_ENTRY=$(efibootmgr -v 2>/dev/null | grep -iE 'ubuntu|proxmox|incus|nvme|ssd|HD|Hard|SATA|GRUB|systemd-boot|shim' | head -1 | grep -o 'Boot[0-9A-Fa-f]*' | sed 's/Boot//'); "
            "if [ -z \"$DISK_ENTRY\" ]; then "
            "  DISK_ENTRY=$(efibootmgr 2>/dev/null | grep 'BootOrder' | tr ',' '\\n' | sed 's/BootOrder: //' | while read e; do "
            "    efibootmgr -v 2>/dev/null | grep \"Boot${e}\" | grep -ivE 'PXE|Network|IPv4' | head -1 | grep -o 'Boot[0-9A-Fa-f]*' | sed 's/Boot//' && break; "
            "  done); "
            "fi; "
            "if [ -z \"$DISK_ENTRY\" ]; then "
            "  echo 'Current boot entries:'; efibootmgr -v 2>&1; "
            "  echo ''; echo 'ERROR: No disk boot entry found'; exit 1; "
            "fi; "
            "echo \"Found disk entry: Boot${DISK_ENTRY}\"; "
            "efibootmgr -n \"$DISK_ENTRY\" 2>&1 && "
            "echo \"EFI_BOOT_DISK_OK: BootNext set to ${DISK_ENTRY}\" && "
            "echo ''; echo 'Current state:'; efibootmgr 2>&1"
        )

        click.echo(f"[{node.hostname}] Setting EFI BootNext to disk...")
        success, stdout, stderr = _run_ssh(node.os_ip, cmd)

        if success and "EFI_BOOT_DISK_OK" in stdout:
            results.append({
                "node": node.hostname,
                "success": True,
                "data": {
                    "message": f"EFI BootNext set to disk on {node.hostname}",
                    "output": stdout.strip(),
                },
            })
        else:
            results.append({
                "node": node.hostname,
                "success": False,
                "error": f"efibootmgr failed: {stdout.strip()} {stderr.strip()}",
            })

    from smcbmc.output import print_multi_node_results
    print_multi_node_results(results, json_mode=nctx.json_mode)
    if any(not r["success"] for r in results):
        sys.exit(1)
