"""PortAudio inventory querying."""

from __future__ import annotations

import platform

from ..dependencies import require_sounddevice
from .types import AudioInventory


def get_hostapi_names(sd_mod) -> list[str]:
    hostapis = sd_mod.query_hostapis()
    return [str(item["name"]) for item in hostapis]


def query_audio_inventory() -> AudioInventory:
    """Query PortAudio once and return a plain-Python inventory snapshot."""
    sd_mod = require_sounddevice()

    try:
        devices = [dict(item) for item in sd_mod.query_devices()]
        hostapi_names = get_hostapi_names(sd_mod)
    except Exception as exc:
        detail_text = str(exc).strip()
        detail = detail_text.splitlines()[0] if detail_text else type(exc).__name__
        system = platform.system()

        if system == "Windows":
            guidance = (
                "Windows did not provide a usable PortAudio device list. "
                "Make sure your audio devices appear in Windows sound settings, "
                "and if you plan to bridge into VB-CABLE, install it beforehand outside this script."
            )
        elif system == "Linux":
            guidance = (
                "PortAudio could not enumerate Linux audio devices. Make sure your "
                "audio session is running and that your PortAudio build can see the "
                "expected PipeWire, PulseAudio, JACK, or ALSA backend."
            )
        else:
            guidance = "PortAudio could not enumerate audio devices on this system."

        raise RuntimeError(
            f"Could not query audio devices.\n{guidance}\nDetails: {detail}"
        ) from exc

    return AudioInventory(devices=devices, hostapi_names=hostapi_names)


def device_info_from_inventory(inventory: AudioInventory, device_id: int) -> dict:
    """Resolve one device from an existing inventory snapshot."""
    if not 0 <= device_id < len(inventory.devices):
        raise ValueError(f"Invalid device ID {device_id}.")

    return dict(inventory.devices[device_id])


def hostapi_name_for_device(info: dict, hostapi_names: list[str]) -> str:
    index = int(info["hostapi"])
    if 0 <= index < len(hostapi_names):
        return hostapi_names[index]
    return f"<unknown:{index}>"
