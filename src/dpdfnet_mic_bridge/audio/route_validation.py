"""Backend-specific route validation.

Manual --input/--output remain authoritative, but the final route must still
make sense for the backend-specific virtual microphone path.
"""

from __future__ import annotations

import platform
import shutil

from .hostapi import hostapi_filter_key_for_name
from .inventory import hostapi_name_for_device
from .tags import has_bridge_output_tags
from .types import AudioInventory


def _windows_vbcable_capture_exists(
    *,
    inventory: AudioInventory,
    output_hostapi: str,
) -> bool:
    for item in inventory.devices:
        name = str(item.get("name", "")).casefold()
        if "cable output" not in name:
            continue
        if int(item.get("max_input_channels", 0)) < 1:
            continue
        if (
            hostapi_name_for_device(dict(item), inventory.hostapi_names)
            != output_hostapi
        ):
            continue
        return True
    return False


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
) -> None:
    system = platform.system()
    input_key = hostapi_filter_key_for_name(input_hostapi)
    output_key = hostapi_filter_key_for_name(output_hostapi)

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

        if not _windows_vbcable_capture_exists(
            inventory=inventory,
            output_hostapi=output_hostapi,
        ):
            raise ValueError(
                "VB-CABLE render endpoint was selected, but the app-facing capture endpoint "
                "'CABLE Output' is not visible on the same WASAPI host API."
            )

    elif system == "Linux":
        if output_key == "pipewire" and shutil.which("pw-cli") is None:
            raise ValueError(
                "PipeWire route validation requires `pw-cli`, but it was not found. "
                "Install PipeWire tools or choose a non-PipeWire route manually."
            )

        if output_key in {"pipewire", "pulse", "jack"}:
            if not has_bridge_output_tags(output_tags):
                raise ValueError(
                    "Selected Linux output is not a virtual microphone render target.\n"
                    "Create the PipeWire virtual microphone target first or pin a valid output.\n"
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
