"""Device-name tagging heuristics."""

from __future__ import annotations

import re

_CABLE_ENDPOINT = re.compile(r"\bcable(?:-([a-d]))?\s+(input|output)\b", re.I)
_CABLE_DRIVER = re.compile(r"\bcable(?:-|\s+)([a-d])\b", re.I)


def vb_cable_family(device: str | dict, direction: str) -> str | None:
    name = str(device.get("name", "")) if isinstance(device, dict) else device
    match = _CABLE_ENDPOINT.search(name)
    if match is not None and match.group(2).casefold() == direction:
        return (match.group(1) or "").casefold()
    if not isinstance(device, dict):
        return None

    # User-visible Windows pin names are mutable. PortAudio's WASAPI
    # IMMDevice property store exposes driver-owned metadata independently.
    description = str(device.get("device_description", "")).casefold()
    manufacturer = str(device.get("manufacturer", "")).casefold()
    if ("vb-audio" not in manufacturer and "vb-audio" not in description) or (
        "cable" not in description
    ):
        return None
    channels = "max_output_channels" if direction == "input" else "max_input_channels"
    if int(device.get(channels, 0)) <= 0:
        return None
    family = _CABLE_DRIVER.search(description)
    return family.group(1).casefold() if family else ""


def infer_device_tags(device: str | dict) -> tuple[str, ...]:
    name = str(device.get("name", "")) if isinstance(device, dict) else device
    text = name.casefold()
    tags: list[str] = []

    def add(condition: bool, *new_tags: str) -> None:
        if condition:
            tags.extend(new_tags)

    cable_playback = vb_cable_family(device, "input") is not None
    cable_recording = vb_cable_family(device, "output") is not None
    is_vb_audio = (
        "vb-audio" in text or "vb-cable" in text or cable_playback or cable_recording
    )

    add(is_vb_audio, "virtual")
    add(cable_playback, "vb-cable-playback")
    add(cable_recording, "vb-cable-recording")
    add("monitor of" in text or ".monitor" in text, "virtual", "monitor")
    add("null sink" in text or "virtual sink" in text, "virtual", "virtual-sink")
    add("virtual source" in text, "virtual", "virtual-source")
    add("loopback" in text, "virtual")
    add("dpdfnet" in text, "dpdfnet")
    add("microphone" in text or " mic" in text or text.startswith("mic "), "mic")
    add("speech-dispatcher" in text or "dummy" in text, "dummy")
    add(
        "hdmi" in text or "displayport" in text or "digital stereo (hdmi)" in text,
        "hdmi",
    )
    add(
        "audio codec" in text or "analog stereo" in text or "usb" in text,
        "audio-interface",
    )
    add("airpods" in text or "bluetooth" in text, "bluetooth")
    add("headphones" in text or "headset" in text, "headphones")
    add("speaker" in text, "speakers")

    return tuple(dict.fromkeys(tags))


def has_bridge_output_tags(tags: tuple[str, ...]) -> bool:
    """Return whether tags positively identify a bridge-style output."""
    tag_set = set(tags)
    return (
        "vb-cable-playback" in tag_set
        or "virtual-sink" in tag_set
        or "dpdfnet" in tag_set
    )
