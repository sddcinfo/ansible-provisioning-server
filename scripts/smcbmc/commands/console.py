"""Console commands: screenshot, info."""

import os
import re
import sys
import time
from http.cookiejar import CookieJar
from urllib import request, parse, error
import ssl

import click

from smcbmc.cli import pass_context, run_on_nodes
from smcbmc.output import print_error


@click.group()
def console():
    """Console screenshot and info commands."""
    pass


def _cgi_session(host, username, password):
    """Authenticate via CGI and return (opener, csrf_name, csrf_token).

    Auth flow:
      1. POST /cgi/login.cgi with username/password -> get SID cookie
      2. GET /cgi/url_redirect.cgi?url_name=topmenu -> extract CSRF token
    """
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    cookie_jar = CookieJar()
    opener = request.build_opener(
        request.HTTPCookieProcessor(cookie_jar),
        request.HTTPSHandler(context=ctx),
    )

    # Step 1: Login
    login_url = f"https://{host}/cgi/login.cgi"
    login_data = parse.urlencode({
        "name": username,
        "pwd": password,
    }).encode("utf-8")

    resp = opener.open(login_url, login_data, timeout=15)
    resp.read()

    # Verify SID cookie
    sid = None
    for cookie in cookie_jar:
        if cookie.name == "SID":
            sid = cookie.value
            break

    if not sid:
        raise Exception("CGI login failed: no SID cookie returned")

    # Step 2: Get CSRF token from topmenu page
    topmenu_url = f"https://{host}/cgi/url_redirect.cgi?url_name=topmenu"
    resp = opener.open(topmenu_url, timeout=15)
    body = resp.read().decode("utf-8", errors="replace")

    match = re.search(
        r'SmcCsrfInsert\s*\(\s*"([^"]+)"\s*,\s*"([^"]+)"\s*\)', body
    )
    if match:
        csrf_name = match.group(1)
        csrf_token = match.group(2)
    else:
        csrf_name = None
        csrf_token = None

    return opener, csrf_name, csrf_token


def _cgi_logout(opener, host, csrf_name=None, csrf_token=None):
    """Logout from CGI session."""
    try:
        logout_url = f"https://{host}/cgi/logout.cgi"
        req = request.Request(logout_url)
        if csrf_name and csrf_token:
            req.add_header(csrf_name, csrf_token)
        opener.open(req, timeout=10)
    except Exception:
        pass


@console.command()
@click.option("--output-dir", default=".", help="Directory to save screenshots.")
@pass_context
def screenshot(nctx, output_dir):
    """Take a screenshot of the server console.

    Uses the BMC web CGI interface to capture a console preview.
    Requires the BMC to support CapturePreview (ATEN/Supermicro IPMI).
    """
    os.makedirs(output_dir, exist_ok=True)

    def _op(client, node):
        opener, csrf_name, csrf_token = _cgi_session(
            node.console_ip, nctx.username, nctx.password
        )
        try:
            base = f"https://{node.console_ip}"

            # Initialize capture preview
            init_req = request.Request(f"{base}/cgi/CapturePreview.cgi")
            if csrf_name and csrf_token:
                init_req.add_header(csrf_name, csrf_token)
            opener.open(init_req, timeout=15).read()

            # Brief delay for the BMC to prepare the capture
            time.sleep(2)

            # Get the screenshot
            preview_req = request.Request(
                f"{base}/cgi/CapturePreview.cgi?action=GetPreview"
            )
            if csrf_name and csrf_token:
                preview_req.add_header(csrf_name, csrf_token)
            resp = opener.open(preview_req, timeout=15)
            image_data = resp.read()

            if not image_data or len(image_data) < 100:
                return {
                    "message": "Screenshot capture returned no image data. "
                    "The BMC may not support console preview or no video signal is present.",
                    "size": len(image_data) if image_data else 0,
                }

            filename = f"{node.hostname}_screenshot.png"
            filepath = os.path.join(output_dir, filename)
            with open(filepath, "wb") as f:
                f.write(image_data)

            return {
                "message": f"Screenshot saved to {filepath}",
                "file": filepath,
                "size": len(image_data),
            }
        finally:
            _cgi_logout(opener, node.console_ip, csrf_name, csrf_token)

    run_on_nodes(nctx, _op, label="console screenshot")


@console.command()
@pass_context
def info(nctx):
    """Get console/KVM information."""
    from smcbmc import REDFISH_MANAGERS, REDFISH_MANAGER_NETWORK_PROTOCOL

    def _op(client, node):
        result = {}

        # Manager info
        mgr = client.get(REDFISH_MANAGERS)
        result["ManagerType"] = mgr.get("ManagerType", "N/A")
        result["FirmwareVersion"] = mgr.get("FirmwareVersion", "N/A")
        result["Model"] = mgr.get("Model", "N/A")

        # Network protocol info (KVM ports, etc.)
        try:
            proto = client.get(REDFISH_MANAGER_NETWORK_PROTOCOL)
            kvmp = proto.get("KVMIP", {})
            if kvmp:
                result["KVMIP"] = {
                    "Port": kvmp.get("Port", "N/A"),
                    "Enabled": kvmp.get("ProtocolEnabled", "N/A"),
                }
            ipmi_info = proto.get("IPMI", {})
            if ipmi_info:
                result["IPMI"] = {
                    "Port": ipmi_info.get("Port", "N/A"),
                    "Enabled": ipmi_info.get("ProtocolEnabled", "N/A"),
                }
        except Exception:
            pass

        return result

    run_on_nodes(nctx, _op, label="console info")
