"""ipmitool wrapper for SOL and raw IPMI commands."""

import os
import subprocess
import shutil


IPMITOOL_PATH = shutil.which("ipmitool") or "/usr/bin/ipmitool"


def _base_args(ip, user, password):
    """Build the common ipmitool arguments."""
    return [
        IPMITOOL_PATH,
        "-I", "lanplus",
        "-H", ip,
        "-U", user,
        "-P", password,
    ]


def sol_deactivate(ip, user, password):
    """Deactivate any existing SOL session. Errors are ignored."""
    cmd = _base_args(ip, user, password) + ["sol", "deactivate"]
    try:
        subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    except Exception:
        pass


def sol_activate_exec(ip, user, password):
    """Replace the current process with ipmitool sol activate.

    Uses os.execvp() for proper TTY handling. Does not return.
    """
    sol_deactivate(ip, user, password)
    args = _base_args(ip, user, password) + ["sol", "activate"]
    os.execvp(args[0], args)


def sol_capture(ip, user, password, output_file, duration=None, stream=False):
    """Capture SOL output to a file using the 'script' command for pseudo-TTY.

    The 'script' command creates its own pseudo-TTY for ipmitool SOL and
    writes all output to the specified file. We use Popen for proper timeout
    and process lifecycle management.

    Args:
        ip: BMC IP
        user: IPMI username
        password: IPMI password
        output_file: File path to write captured output
        duration: Capture duration in seconds (None = until interrupted)
        stream: If True, also stream output to stdout in real-time

    Returns:
        (success, message)
    """
    sol_deactivate(ip, user, password)
    ipmitool_args = _base_args(ip, user, password) + ["sol", "activate"]
    ipmitool_cmd = " ".join(ipmitool_args)

    # script -q suppresses its own "Script started" messages
    # script without -q shows those messages (useful for streaming)
    if stream:
        script_cmd = ["script", "-c", ipmitool_cmd, output_file]
    else:
        script_cmd = ["script", "-q", "-c", ipmitool_cmd, output_file]

    try:
        # Always use Popen for proper timeout handling.
        # script creates its own PTY internally, so we can safely redirect
        # our own stdin/stdout without affecting the SOL capture.
        if stream:
            # Let output flow to the terminal
            proc = subprocess.Popen(script_cmd)
        else:
            # Suppress terminal output; script still writes to the file
            proc = subprocess.Popen(
                script_cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

        try:
            if duration:
                proc.wait(timeout=duration)
            else:
                proc.wait()
        except subprocess.TimeoutExpired:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
            return True, f"SOL capture completed ({duration}s) to {output_file}"

        return True, f"SOL output captured to {output_file}"
    except KeyboardInterrupt:
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            pass
        return True, f"SOL capture interrupted, output in {output_file}"
    except Exception as e:
        return False, f"SOL capture error: {e}"
    finally:
        sol_deactivate(ip, user, password)


def run_raw(ip, user, password, command):
    """Run a raw ipmitool command.

    Args:
        ip: BMC IP
        user: IPMI username
        password: IPMI password
        command: ipmitool command string (e.g. "chassis status")

    Returns:
        (success, stdout, stderr)
    """
    cmd = _base_args(ip, user, password) + command.split()
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return result.returncode == 0, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return False, "", "Command timed out"
    except Exception as e:
        return False, "", str(e)
