"""PortAudio inventory querying through the native Rust audio engine."""

from __future__ import annotations

import platform

from .. import _native_audio as native_audio
from .stderr import silence_native_stderr
from .types import AudioInventory


def query_audio_inventory() -> AudioInventory:
    """Query native PortAudio once and return a plain-Python inventory snapshot."""
    try:
        with silence_native_stderr():
            snapshot = native_audio.query_audio_inventory()
        devices = [dict(item) for item in snapshot["devices"]]
        hostapi_names = [str(item) for item in snapshot["hostapi_names"]]
    except Exception as exc:
        detail_text = str(exc).strip()
        detail = detail_text.splitlines()[0] if detail_text else type(exc).__name__
        system = platform.system()

        if system == "Windows":
            guidance = (
                "Windows did not provide a usable native PortAudio device list. "
                "Make sure your audio devices appear in Windows sound settings, "
                "and if you plan to bridge into VB-CABLE, install it beforehand outside this script."
            )
        elif system == "Linux":
            guidance = (
                "Native PortAudio could not enumerate Linux audio devices. Make sure your "
                "audio session is running and that your PortAudio build can see the "
                "expected PipeWire, PulseAudio, JACK, or ALSA backend."
            )
        else:
            guidance = (
                "Native PortAudio could not enumerate audio devices on this system."
            )

        raise RuntimeError(
            f"Could not query audio devices.\n{guidance}\nDetails: {detail}"
        ) from exc

    inventory = AudioInventory(devices=devices, hostapi_names=hostapi_names)

    from .identity import stable_device_key

    for item in inventory.devices:
        item["stable_id"] = stable_device_key(inventory, dict(item))

    return inventory


def device_info_from_inventory(inventory: AudioInventory, device_id: int) -> dict:
    """Resolve one native PortAudio device ID from an existing inventory snapshot."""
    for item in inventory.devices:
        try:
            if int(item.get("id", -1)) == device_id:
                return dict(item)
        except (TypeError, ValueError):
            continue

    if (
        0 <= device_id < len(inventory.devices)
        and "id" not in inventory.devices[device_id]
    ):
        return dict(inventory.devices[device_id])

    raise ValueError(f"Invalid device ID {device_id}.")


def hostapi_name_for_device(info: dict, hostapi_names: list[str]) -> str:
    index = int(info["hostapi"])
    if 0 <= index < len(hostapi_names):
        return hostapi_names[index]
    return f"<unknown:{index}>"
