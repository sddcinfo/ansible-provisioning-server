"""Virtual media commands: mount, unmount, status."""

import click

from smcbmc import REDFISH_MANAGERS, REDFISH_VIRTUAL_MEDIA
from smcbmc.cli import pass_context, run_on_nodes


@click.group(name="virtual-media")
def virtual_media():
    """Virtual media management commands."""
    pass


def _get_vm_base(client):
    """Discover the virtual media collection URI from the Manager endpoint.

    Supermicro BMCs may use non-standard paths (e.g. /redfish/v1/Managers/1/VM1).
    """
    try:
        mgr = client.get(REDFISH_MANAGERS)
        vm_ref = mgr.get("VirtualMedia", {})
        vm_uri = vm_ref.get("@odata.id", "")
        if vm_uri:
            return vm_uri
    except Exception:
        pass
    # Fallback to standard path
    return REDFISH_VIRTUAL_MEDIA


def _find_cd_slot(client):
    """Find the CD/DVD virtual media slot URI."""
    vm_base = _get_vm_base(client)
    data = client.get(vm_base)
    members = data.get("Members", [])

    # If the response itself looks like a single VM slot (no Members collection),
    # treat it as a direct slot
    if not members and "Id" in data:
        return vm_base, data

    for member in members:
        uri = member.get("@odata.id", "")
        if uri:
            slot = client.get(uri)
            media_types = slot.get("MediaTypes", [])
            if any(mt in ("CD", "DVD") for mt in media_types):
                return uri, slot
    # Fallback to first slot
    if members:
        uri = members[0].get("@odata.id", "")
        slot = client.get(uri)
        return uri, slot
    return None, None


def _find_smc_cfg(client, vm_base):
    """Find Supermicro OEM CfgCD endpoint for ISO mount/unmount.

    Supermicro X10/X11 BMCs use a proprietary virtual media API:
      - PATCH /redfish/v1/Managers/1/VM1/CfgCD  (set Host + Path)
      - POST  .../CfgCD/Actions/IsoConfig.Mount
      - POST  .../CfgCD/Actions/IsoConfig.UnMount
    """
    data = client.get(vm_base)
    oem = data.get("Oem", {}).get("Supermicro", {})
    vm_cfg = oem.get("VirtualMediaConfig", {})
    cfg_uri = vm_cfg.get("@odata.id", "")
    if cfg_uri:
        return cfg_uri
    # Try well-known path
    cfg_uri = vm_base.rstrip("/") + "/CfgCD"
    try:
        client.get(cfg_uri)
        return cfg_uri
    except Exception:
        return None


def _parse_iso_url(url):
    """Parse an ISO URL into host and path components.

    Accepts formats:
      - http://10.10.1.1/images/foo.iso  -> host=10.10.1.1, path=/images/foo.iso
      - 10.10.1.1/images/foo.iso         -> host=10.10.1.1, path=/images/foo.iso
      - //10.10.1.1/images/foo.iso       -> host=10.10.1.1, path=/images/foo.iso
    """
    # Strip protocol prefix
    for prefix in ("http://", "https://", "//"):
        if url.startswith(prefix):
            url = url[len(prefix):]
            break

    # Split on first /
    if "/" in url:
        host, path = url.split("/", 1)
        path = "/" + path
    else:
        host = url
        path = "/"
    return host, path


@virtual_media.command()
@click.argument("url")
@pass_context
def mount(nctx, url):
    """Mount an ISO image via virtual media.

    URL can be: http://host/path/to/image.iso or host/path/to/image.iso
    """
    def _op(client, node):
        vm_base = _get_vm_base(client)

        # Try Supermicro OEM CfgCD endpoint first
        cfg_uri = _find_smc_cfg(client, vm_base)
        if cfg_uri:
            host, path = _parse_iso_url(url)

            # Unmount any existing media first
            unmount_uri = cfg_uri + "/Actions/IsoConfig.UnMount"
            try:
                client.post(unmount_uri, {})
            except Exception:
                pass

            # Configure Host + Path
            client.patch(cfg_uri, {"Host": host, "Path": path})

            # Mount
            mount_uri = cfg_uri + "/Actions/IsoConfig.Mount"
            result = client.post(mount_uri, {})

            # Verify
            slot_uri, slot = _find_cd_slot(client)
            inserted = slot.get("Inserted", False) if slot else "unknown"

            return {
                "message": f"Mounted {url} via SMC OEM",
                "host": host,
                "path": path,
                "inserted": inserted,
            }

        # Fall back to standard Redfish
        slot_uri, slot = _find_cd_slot(client)
        if not slot_uri:
            raise Exception("No virtual media slot found")

        actions = slot.get("Actions", {})
        insert_action = actions.get("#VirtualMedia.InsertMedia", {})
        insert_uri = insert_action.get("target", "")

        if insert_uri:
            result = client.post(insert_uri, {"Image": url})
        else:
            result = client.patch(slot_uri, {"Image": url, "Inserted": True})

        return {"message": f"Mounted {url}", "slot": slot_uri}

    run_on_nodes(nctx, _op, label="virtual-media mount")


@virtual_media.command()
@pass_context
def unmount(nctx):
    """Unmount virtual media."""
    def _op(client, node):
        vm_base = _get_vm_base(client)

        # Try Supermicro OEM CfgCD endpoint first
        cfg_uri = _find_smc_cfg(client, vm_base)
        if cfg_uri:
            unmount_uri = cfg_uri + "/Actions/IsoConfig.UnMount"
            result = client.post(unmount_uri, {})
            return {"message": "Virtual media unmounted via SMC OEM"}

        # Fall back to standard Redfish
        slot_uri, slot = _find_cd_slot(client)
        if not slot_uri:
            raise Exception("No virtual media slot found")

        actions = slot.get("Actions", {})
        eject_action = actions.get("#VirtualMedia.EjectMedia", {})
        eject_uri = eject_action.get("target", "")

        if eject_uri:
            result = client.post(eject_uri, {})
        else:
            result = client.patch(slot_uri, {"Image": None, "Inserted": False})

        return {"message": "Virtual media unmounted", "slot": slot_uri}

    run_on_nodes(nctx, _op, label="virtual-media unmount")


@virtual_media.command()
@pass_context
def status(nctx):
    """Get virtual media status."""
    def _op(client, node):
        vm_base = _get_vm_base(client)
        data = client.get(vm_base)
        members = data.get("Members", [])

        # Direct slot (no collection)
        if not members and "Id" in data:
            slots = [{
                "Id": data.get("Id", ""),
                "Name": data.get("Name", ""),
                "MediaTypes": data.get("MediaTypes", []),
                "Image": data.get("Image", ""),
                "Inserted": data.get("Inserted", False),
                "ConnectedVia": data.get("ConnectedVia", data.get("ConnecteVia", "")),
            }]
            return {"VirtualMedia": slots}

        slots = []
        for member in members:
            uri = member.get("@odata.id", "")
            if uri:
                try:
                    slot = client.get(uri)
                    slots.append({
                        "Id": slot.get("Id", ""),
                        "Name": slot.get("Name", ""),
                        "MediaTypes": slot.get("MediaTypes", []),
                        "Image": slot.get("Image", ""),
                        "Inserted": slot.get("Inserted", False),
                        "ConnectedVia": slot.get("ConnectedVia", slot.get("ConnecteVia", "")),
                    })
                except Exception:
                    pass
        return {"VirtualMedia": slots}

    run_on_nodes(nctx, _op, label="virtual-media status")
