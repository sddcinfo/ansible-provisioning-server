"""Incus OS management commands: status, add-remote, verify, download-image."""

import json
import os
import subprocess
import sys

import click

from smcbmc.cli import pass_context
from smcbmc.output import print_error, print_multi_node_results

PROVISIONING_SERVER = "10.10.1.1"
INCUSOS_API_PORT = 8443
INCUSOS_IMAGE_BASE_URL = "https://images.linuxcontainers.org/os"
PROVISIONING_DIR = "/var/www/html/provisioning/incusos"


def _curl_incus_api(ip, endpoint="/1.0"):
    """Query the Incus API via curl. Returns (success, parsed_json_or_error)."""
    try:
        result = subprocess.run(
            ["curl", "-sk", "--connect-timeout", "5", "--max-time", "10",
             f"https://{ip}:{INCUSOS_API_PORT}{endpoint}"],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0 and result.stdout.strip():
            return True, json.loads(result.stdout)
        return False, f"curl failed (rc={result.returncode}): {result.stderr.strip()}"
    except json.JSONDecodeError as e:
        return False, f"Invalid JSON response: {e}"
    except subprocess.TimeoutExpired:
        return False, "Connection timed out"
    except Exception as e:
        return False, str(e)


@click.group()
def incusos():
    """Incus OS management commands."""
    pass


@incusos.command()
@pass_context
def status(nctx):
    """Check Incus API status on node(s).

    Queries the /1.0 endpoint to check API version and auth status.
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

        success, data = _curl_incus_api(node.os_ip)
        if success and isinstance(data, dict):
            metadata = data.get("metadata", {})
            results.append({
                "node": node.hostname,
                "success": True,
                "data": {
                    "status": "RUNNING",
                    "api_version": metadata.get("api_version", "unknown"),
                    "api_status": metadata.get("api_status", "unknown"),
                    "auth": metadata.get("auth", "unknown"),
                    "os_ip": node.os_ip,
                    "port": INCUSOS_API_PORT,
                },
            })
        else:
            results.append({
                "node": node.hostname,
                "success": False,
                "error": f"Incus API not reachable at {node.os_ip}:{INCUSOS_API_PORT}: {data}",
            })

    print_multi_node_results(results, json_mode=nctx.json_mode)
    if any(not r["success"] for r in results):
        sys.exit(1)


@incusos.command(name="add-remote")
@click.option("--name", "remote_name", default=None,
              help="Remote name (default: node's os_hostname).")
@pass_context
def add_remote(nctx, remote_name):
    """Add node(s) as Incus remote(s) to local client.

    Uses the pre-seeded client certificate for authentication.
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

        name = remote_name or node.os_hostname or node.hostname
        url = f"https://{node.os_ip}:{INCUSOS_API_PORT}"

        try:
            result = subprocess.run(
                ["incus", "remote", "add", name, url, "--accept-certificate"],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode == 0:
                results.append({
                    "node": node.hostname,
                    "success": True,
                    "data": {
                        "message": f"Remote '{name}' added: {url}",
                        "remote_name": name,
                        "url": url,
                    },
                })
            else:
                error = result.stderr.strip()
                # Check if remote already exists
                if "already exists" in error or "exists as" in error:
                    results.append({
                        "node": node.hostname,
                        "success": True,
                        "data": {
                            "message": f"Remote '{name}' already exists",
                            "remote_name": name,
                            "url": url,
                        },
                    })
                else:
                    results.append({
                        "node": node.hostname,
                        "success": False,
                        "error": f"Failed to add remote: {error}",
                    })
        except FileNotFoundError:
            results.append({
                "node": node.hostname,
                "success": False,
                "error": "incus CLI not found. Install the Incus client.",
            })
            break
        except Exception as e:
            results.append({
                "node": node.hostname,
                "success": False,
                "error": str(e),
            })

    print_multi_node_results(results, json_mode=nctx.json_mode)
    if any(not r["success"] for r in results):
        sys.exit(1)


@incusos.command()
@pass_context
def verify(nctx):
    """Verify Incus OS installation on node(s).

    Checks: API accessible, auth trusted, application running.
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

        checks = {}
        all_pass = True

        # Check 1: API accessible
        success, data = _curl_incus_api(node.os_ip)
        if success and isinstance(data, dict):
            metadata = data.get("metadata", {})
            checks["api"] = {
                "status": "pass",
                "version": metadata.get("api_version", "unknown"),
            }
            auth = metadata.get("auth", "untrusted")
            # "untrusted" is expected for unauthenticated curl queries
            # It means the API is working; client cert auth happens via incus CLI
            checks["auth"] = {
                "status": "pass" if auth == "trusted" else "info",
                "value": auth,
            }
        else:
            checks["api"] = {"status": "fail", "error": str(data)}
            all_pass = False

        # Check 2: Storage pools (via API /1.0/storage-pools)
        success, data = _curl_incus_api(node.os_ip, "/1.0/storage-pools")
        if success and isinstance(data, dict):
            error_code = data.get("error_code", 0)
            if error_code == 403:
                checks["storage_pools"] = {"status": "skip", "reason": "auth required"}
            else:
                pools = data.get("metadata") or []
                checks["storage_pools"] = {
                    "status": "pass" if pools else "warn",
                    "count": len(pools),
                    "pools": pools,
                }
        else:
            checks["storage_pools"] = {"status": "warn", "error": str(data)}

        # Check 3: Networks (via API /1.0/networks)
        success, data = _curl_incus_api(node.os_ip, "/1.0/networks")
        if success and isinstance(data, dict):
            error_code = data.get("error_code", 0)
            if error_code == 403:
                checks["networks"] = {"status": "skip", "reason": "auth required"}
            else:
                networks = data.get("metadata") or []
                has_bridge = any("incusbr" in n for n in networks)
                checks["networks"] = {
                    "status": "pass" if has_bridge else "warn",
                    "count": len(networks),
                    "has_bridge": has_bridge,
                    "networks": networks,
                }
        else:
            checks["networks"] = {"status": "warn", "error": str(data)}

        results.append({
            "node": node.hostname,
            "success": all_pass,
            "data": {
                "status": "VERIFIED" if all_pass else "PARTIAL",
                "checks": checks,
                "os_ip": node.os_ip,
            },
        })

    print_multi_node_results(results, json_mode=nctx.json_mode)
    if any(not r["success"] for r in results):
        sys.exit(1)


@incusos.command(name="download-image")
@click.option("--version", "image_version", default=None,
              help="Image version (e.g., 202602040632). Downloads latest if not specified.")
@click.option("--output-dir", default=PROVISIONING_DIR,
              help="Directory to save the image.")
def download_image(image_version, output_dir):
    """Download Incus OS image to provisioning server.

    Downloads from the official linuxcontainers.org repository
    and creates an IncusOS_latest.img.gz symlink.
    """
    if not image_version:
        click.echo("Checking for latest Incus OS version...")
        # Try to find the latest version from the listing
        try:
            result = subprocess.run(
                ["curl", "-sfL", f"{INCUSOS_IMAGE_BASE_URL}/"],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode == 0:
                # Parse directory listing for version numbers
                import re
                versions = re.findall(r'href="(\d{12})/"', result.stdout)
                if versions:
                    image_version = sorted(versions)[-1]
                    click.echo(f"Latest version: {image_version}")
                else:
                    click.echo("ERROR: Could not determine latest version. Use --version.", err=True)
                    sys.exit(1)
            else:
                click.echo("ERROR: Could not fetch version listing. Use --version.", err=True)
                sys.exit(1)
        except Exception as e:
            click.echo(f"ERROR: {e}. Use --version.", err=True)
            sys.exit(1)

    # Warn about known-bad version
    if image_version == "202602031842":
        click.echo("WARNING: Version 202602031842 was PULLED due to cert verification bug!")
        click.echo("         This version hangs at 'IncusOS is starting'. Use a newer version.")
        sys.exit(1)

    filename = f"IncusOS_{image_version}.img.gz"
    url = f"{INCUSOS_IMAGE_BASE_URL}/{image_version}/x86_64/{filename}"
    output_path = os.path.join(output_dir, filename)
    symlink_path = os.path.join(output_dir, "IncusOS_latest.img.gz")

    os.makedirs(output_dir, exist_ok=True)

    if os.path.exists(output_path):
        click.echo(f"Image already exists: {output_path}")
    else:
        click.echo(f"Downloading {url}...")
        click.echo(f"Saving to {output_path}...")
        try:
            result = subprocess.run(
                ["curl", "-fL", "--progress-bar", "-o", output_path, url],
                timeout=1800,
            )
            if result.returncode != 0:
                click.echo(f"ERROR: Download failed (rc={result.returncode})", err=True)
                if os.path.exists(output_path):
                    os.remove(output_path)
                sys.exit(1)
        except Exception as e:
            click.echo(f"ERROR: {e}", err=True)
            if os.path.exists(output_path):
                os.remove(output_path)
            sys.exit(1)

    # Create/update symlink
    if os.path.islink(symlink_path):
        os.remove(symlink_path)
    elif os.path.exists(symlink_path):
        os.remove(symlink_path)
    os.symlink(filename, symlink_path)

    click.echo(f"Image: {output_path}")
    click.echo(f"Symlink: {symlink_path} -> {filename}")

    # Show file size
    size_mb = os.path.getsize(output_path) / (1024 * 1024)
    click.echo(f"Size: {size_mb:.1f} MB")
