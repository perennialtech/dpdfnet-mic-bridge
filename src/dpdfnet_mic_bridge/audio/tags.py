"""Device-name tagging heuristics."""

from __future__ import annotations


def infer_device_tags(name: str) -> tuple[str, ...]:
    text = name.casefold()
    tags: list[str] = []

    def add(condition: bool, *new_tags: str) -> None:
        if condition:
            tags.extend(new_tags)

    is_vb_audio = (
        "vb-audio" in text
        or "vb-cable" in text
        or "cable input" in text
        or "cable output" in text
    )

    add(is_vb_audio, "virtual")
    add("cable input" in text, "vb-cable-playback")
    add("cable output" in text, "vb-cable-recording")
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
