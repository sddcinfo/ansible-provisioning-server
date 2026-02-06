"""SUM (Supermicro Update Manager) tool wrapper."""

import os
import subprocess
from pathlib import Path

from smcbmc.client import BMCError


class SUMError(BMCError):
    """SUM tool execution failure."""
    pass


# Known SUM binary locations (searched in order)
SUM_SEARCH_PATHS = [
    Path.home() / "claude" / "supermicro-sum-scripts" / "sum_2.14.0_Linux_x86_64" / "sum",
    Path("/usr/local/bin/sum"),
    Path("/usr/bin/sum"),
]


def find_sum_binary():
    """Find the SUM binary, searching known paths."""
    for path in SUM_SEARCH_PATHS:
        if path.exists() and os.access(str(path), os.X_OK):
            return str(path)
    return None


def _run_sum(ip, user, password, command, extra_args=None, timeout=60):
    """Run a SUM command and return (success, stdout, stderr).

    Raises SUMError if the binary is not found.
    """
    binary = find_sum_binary()
    if not binary:
        raise SUMError(
            "SUM tool not found. Searched: "
            + ", ".join(str(p) for p in SUM_SEARCH_PATHS)
        )

    cmd = [binary, "-i", ip, "-u", user, "-p", password, "-c", command]
    if extra_args:
        cmd.extend(extra_args)

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return result.returncode == 0, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return False, "", f"SUM command timed out after {timeout}s"
    except Exception as e:
        return False, "", str(e)


def raw_command(ip, user, password, raw_cmd, timeout=60):
    """Execute a SUM RawCommand (IPMI raw).

    Returns (success, stdout, stderr).
    """
    return _run_sum(ip, user, password, "RawCommand", ["--raw", raw_cmd], timeout=timeout)


def get_bios_config(ip, user, password, output_file, timeout=120):
    """Retrieve current BIOS configuration to a file.

    Returns (success, stdout, stderr).
    """
    return _run_sum(
        ip, user, password, "GetCurrentBiosCfg",
        ["--file", output_file, "--overwrite"],
        timeout=timeout,
    )


def set_bios_config(ip, user, password, config_file, timeout=120):
    """Apply a BIOS configuration from file.

    Returns (success, stdout, stderr).
    """
    return _run_sum(
        ip, user, password, "ChangeBiosCfg",
        ["--file", config_file, "--skip_unknown"],
        timeout=timeout,
    )


def query_product_key(ip, user, password, timeout=30):
    """Query product keys on a node.

    Returns (success, stdout, stderr).
    """
    return _run_sum(ip, user, password, "QueryProductKey", timeout=timeout)


def activate_product_key(ip, user, password, key, timeout=30):
    """Activate a product key on a node.

    Returns (success, stdout, stderr).
    """
    return _run_sum(
        ip, user, password, "ActivateProductKey",
        ["--key", key],
        timeout=timeout,
    )


def clear_product_key(ip, user, password, key_index, timeout=30):
    """Clear a product key by index.

    Returns (success, stdout, stderr).
    """
    return _run_sum(
        ip, user, password, "ClearProductKey",
        ["--key_index", str(key_index)],
        timeout=timeout,
    )


def generate_product_key(mac_address, key_type="oob"):
    """Generate a product key for the given MAC address.

    Uses ~/go/bin/supermicro-product-key tool.
    key_type: "oob" for SFT-OOB-LIC, or a SKU like "SFT-DCMS-SINGLE".

    Returns the key string or None on failure.
    """
    tool = Path.home() / "go" / "bin" / "supermicro-product-key"
    if not tool.exists():
        raise SUMError(
            f"supermicro-product-key not found at {tool}. "
            "Install with: go install github.com/zsrv/supermicro-product-key@latest"
        )

    mac_clean = mac_address.replace(":", "")

    if key_type == "oob":
        cmd = [str(tool), "oob", "encode", mac_clean]
    else:
        cmd = [str(tool), "nonjson", "encode", "--sku", key_type, mac_clean]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            return result.stdout.strip()
        raise SUMError(f"Key generation failed: {result.stderr.strip()}")
    except subprocess.TimeoutExpired:
        raise SUMError("Key generation timed out")
    except SUMError:
        raise
    except Exception as e:
        raise SUMError(f"Key generation error: {e}")


def get_bmc_config(ip, user, password, output_file, timeout=120):
    """Retrieve current BMC configuration to a file.

    Returns (success, stdout, stderr).
    """
    return _run_sum(
        ip, user, password, "GetBmcCfg",
        ["--file", output_file, "--overwrite"],
        timeout=timeout,
    )


def set_bmc_config(ip, user, password, config_file, timeout=120):
    """Apply a BMC configuration from file.

    Returns (success, stdout, stderr).
    """
    return _run_sum(
        ip, user, password, "ChangeBmcCfg",
        ["--file", config_file],
        timeout=timeout,
    )


def get_bmc_info(ip, user, password, timeout=60):
    """Get BMC firmware information.

    Returns (success, stdout, stderr).
    """
    return _run_sum(ip, user, password, "GetBmcInfo", timeout=timeout)


def get_bios_info(ip, user, password, timeout=60):
    """Get BIOS firmware information.

    Returns (success, stdout, stderr).
    """
    return _run_sum(ip, user, password, "GetBIOSInfo", timeout=timeout)


def get_dmi_info(ip, user, password, output_file, timeout=60):
    """Get DMI/SMBIOS information to a file.

    Returns (success, stdout, stderr).
    """
    return _run_sum(
        ip, user, password, "GetDmiInfo",
        ["--file", output_file, "--overwrite"],
        timeout=timeout,
    )


def run_arbitrary(ip, user, password, command, extra_args=None, timeout=60):
    """Run an arbitrary SUM command.

    Returns (success, stdout, stderr).
    """
    args = extra_args.split() if isinstance(extra_args, str) else extra_args
    return _run_sum(ip, user, password, command, args, timeout=timeout)
