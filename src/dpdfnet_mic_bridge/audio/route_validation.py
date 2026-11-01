"""Backend-specific route validation.

Manual --input/--output remain authoritative, but the final route must still
make sense for the backend-specific virtual microphone path.
"""

from __future__ import annotations

import platform

from .hostapi import hostapi_filter_key_for_name
from .inventory import hostapi_name_for_device
from .tags import has_bridge_output_tags, vb_cable_family
from .types import AudioInventory


def windows_vbcable_capture_name(
    *,
    inventory: AudioInventory,
    output_hostapi: str,
    output_device_id: int,
) -> str | None:
    output = next(
        (
            item
            for item in inventory.devices
            if int(item.get("id", -1)) == output_device_id
        ),
        None,
    )
    if output is None:
        return None
    family = vb_cable_family(output, "input")
    if family is None:
        return None
    renders = [
        item
        for item in inventory.devices
        if vb_cable_family(item, "input") == family
        and int(item.get("max_output_channels", 0)) > 0
        and hostapi_name_for_device(item, inventory.hostapi_names) == output_hostapi
    ]
    captures = [
        item
        for item in inventory.devices
        if vb_cable_family(item, "output") == family
        and int(item.get("max_input_channels", 0)) > 0
        and hostapi_name_for_device(item, inventory.hostapi_names) == output_hostapi
    ]
    # The hardware container GUID groups endpoint pins regardless of renamed
    # display labels. Use it when both ends expose it, but reject duplicates.
    container = str(output.get("container_id", "")).casefold()
    if container and any(item.get("container_id") for item in captures):
        peers = [
            item
            for item in renders
            if str(item.get("container_id", "")).casefold() == container
        ]
        paired = [
            item
            for item in captures
            if str(item.get("container_id", "")).casefold() == container
        ]
        if len(peers) == 1 and len(paired) == 1:
            return str(paired[0]["name"])
        # Some virtual drivers assign distinct pin containers. If there is
        # only one cable of this family, the pairing remains unambiguous.
        if len(renders) != 1 or len(captures) != 1:
            return None
    # For legacy inventories with no container identity, only a single
    # unambiguous render/capture pair is safe; matching names alone are not.
    if len(renders) == 1 and len(captures) == 1:
        return str(captures[0]["name"])
    return None


def validate_backend_route(
    *,
    inventory: AudioInventory,
    input_device_id: int,
    output_device_id: int,
    input_hostapi: str,
    output_hostapi: str,
    input_name: str,
    output_name: str,
    input_tags: tuple[str, ...],
    output_tags: tuple[str, ...],
) -> str | None:
    system = platform.system()
    output_key = hostapi_filter_key_for_name(output_hostapi)
    capture_name = None

    if system == "Windows":
        if output_key != "wasapi":
            raise ValueError(
                "Windows route validation requires WASAPI for VB-CABLE.\n"
                f"Output: id={output_device_id}, hostapi={output_hostapi}, name={output_name}"
            )

        if "vb-cable-playback" not in output_tags:
            raise ValueError(
                "Selected Windows output is not the VB-CABLE render endpoint.\n"
                "The bridge must write to VB-CABLE 'CABLE Input'; apps capture from 'CABLE Output'.\n"
                f"Output: id={output_device_id}, hostapi={output_hostapi}, name={output_name}"
            )

        capture_name = windows_vbcable_capture_name(
            inventory=inventory,
            output_hostapi=output_hostapi,
            output_device_id=output_device_id,
        )
        if capture_name is None:
            raise ValueError(
                "VB-CABLE render endpoint was selected, but the app-facing capture endpoint "
                f"matching '{output_name}' is missing or ambiguous on the same WASAPI host API."
            )

    elif system == "Linux":
        if output_key in {"pipewire", "pulse", "jack"}:
            if not has_bridge_output_tags(output_tags):
                raise ValueError(
                    "Selected Linux output is not a virtual microphone render target.\n"
                    "Pin a valid virtual output.\n"
                    f"Output: id={output_device_id}, hostapi={output_hostapi}, name={output_name}"
                )

            if "monitor" in output_tags:
                raise ValueError(
                    "Selected Linux output is a monitor/source endpoint, not the render side "
                    "the bridge can write into."
                )

    if "monitor" in input_tags:
        raise ValueError(
            "Selected input is a monitor/source endpoint. Use a physical microphone input.\n"
            f"Input: id={input_device_id}, hostapi={input_hostapi}, name={input_name}"
        )
    return capture_name
