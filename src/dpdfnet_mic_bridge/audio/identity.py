"""Persistent device identity helpers."""

from __future__ import annotations

import hashlib

from .inventory import hostapi_name_for_device
from .types import AudioInventory


def stable_device_key(inventory: AudioInventory, info: dict) -> str:
    """
    Return a persistent matching key.

    PortAudio numeric IDs are session-local. When backend-native GUIDs are not
    exposed by PortAudio, use a normalized hostapi/name/channel signature so
    saved routes survive ordinary ID reshuffles.
    """
    hostapi = hostapi_name_for_device(info, inventory.hostapi_names)
    payload = "|".join(
        (
            hostapi.casefold().strip(),
            str(info.get("name", "")).casefold().strip(),
            str(info.get("max_input_channels", "")),
            str(info.get("max_output_channels", "")),
            str(info.get("default_samplerate", "")),
        )
    )
    digest = hashlib.sha256(payload.encode("utf-8", errors="replace")).hexdigest()
    return f"portaudio:{digest[:24]}"
