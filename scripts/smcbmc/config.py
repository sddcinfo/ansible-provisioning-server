"""Configuration loading for smcbmc - nodes.json and credentials."""

import json
import os
import sys
from dataclasses import dataclass, asdict
from typing import List, Optional


NODES_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.realpath(__file__)))),
    "nodes.json",
)
CREDENTIALS_FILE = os.path.expanduser("~/.redfish_credentials")


@dataclass
class Node:
    hostname: str
    console_ip: str
    console_mac: str = ""
    os_ip: str = ""
    os_mac: str = ""
    os_hostname: str = ""
    ceph_ip: str = ""

    def to_dict(self):
        return asdict(self)


def load_nodes(nodes_file: Optional[str] = None) -> List[Node]:
    """Load all nodes from nodes.json."""
    path = nodes_file or NODES_FILE
    try:
        with open(path) as f:
            data = json.load(f)
    except FileNotFoundError:
        print(f"Error: nodes file not found at {path}", file=sys.stderr)
        sys.exit(1)
    except json.JSONDecodeError:
        print(f"Error: could not decode JSON from {path}", file=sys.stderr)
        sys.exit(1)

    nodes = []
    for entry in data.get("nodes", []):
        nodes.append(Node(
            hostname=entry.get("hostname", ""),
            console_ip=entry.get("console_ip", ""),
            console_mac=entry.get("console_mac", ""),
            os_ip=entry.get("os_ip", ""),
            os_mac=entry.get("os_mac", ""),
            os_hostname=entry.get("os_hostname", ""),
            ceph_ip=entry.get("ceph_ip", ""),
        ))
    return nodes


def load_credentials(credentials_file: Optional[str] = None) -> tuple:
    """Load BMC credentials from ~/.redfish_credentials.

    Returns (username, password) tuple.
    """
    path = credentials_file or CREDENTIALS_FILE
    try:
        with open(path) as f:
            content = f.read().strip()
    except FileNotFoundError:
        print(f"Error: credentials file not found at {path}", file=sys.stderr)
        sys.exit(1)

    if content.startswith("REDFISH_AUTH="):
        auth_str = content.split("=", 1)[1].strip('"')
        parts = auth_str.split(":", 1)
        if len(parts) == 2:
            return parts[0], parts[1]

    print(f"Error: could not parse REDFISH_AUTH in {path}", file=sys.stderr)
    sys.exit(1)


def resolve_nodes(
    identifiers: List[str],
    all_nodes: List[Node],
) -> List[Node]:
    """Resolve node identifiers to Node objects.

    Matches on hostname, os_hostname, or raw IP (console_ip).
    """
    matched = []
    for ident in identifiers:
        ident = ident.strip()
        found = False
        for node in all_nodes:
            if ident in (node.hostname, node.os_hostname, node.console_ip):
                matched.append(node)
                found = True
                break
        if not found:
            # Treat as raw IP - create a minimal Node
            matched.append(Node(hostname=ident, console_ip=ident))
    return matched
