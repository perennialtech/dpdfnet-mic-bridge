"""Linux reusable virtual microphone target helpers."""

from __future__ import annotations

import shlex

LINUX_REUSABLE_VIRTUAL_MIC_SINK_NAME = "dpdfnet_mic"
LINUX_REUSABLE_VIRTUAL_MIC_DESCRIPTION = "DPDFNet_Mic"


def linux_pipewire_virtual_mic_command_args() -> tuple[str, ...]:
    """Return the preferred PipeWire-native virtual source creation command."""
    return (
        "pw-cli",
        "create-node",
        "adapter",
        "{ "
        "factory.name=support.null-audio-sink "
        f"node.name={LINUX_REUSABLE_VIRTUAL_MIC_SINK_NAME} "
        f'node.description="{LINUX_REUSABLE_VIRTUAL_MIC_DESCRIPTION}" '
        "media.class=Audio/Source/Virtual "
        "audio.position=[ MONO ] "
        "}",
    )


def linux_virtual_mic_command_args() -> tuple[str, ...]:
    """Return the Pulse-compatible fallback command."""
    return (
        "pactl",
        "load-module",
        "module-null-sink",
        "media.class=Audio/Source/Virtual",
        f"sink_name={LINUX_REUSABLE_VIRTUAL_MIC_SINK_NAME}",
        f"sink_properties=device.description={LINUX_REUSABLE_VIRTUAL_MIC_DESCRIPTION}",
        "channels=1",
        "channel_map=mono",
    )


def linux_pipewire_virtual_mic_command() -> str:
    return shlex.join(linux_pipewire_virtual_mic_command_args())


def linux_virtual_mic_command() -> str:
    """Return the fallback Linux virtual-mic command as display text."""
    return shlex.join(linux_virtual_mic_command_args())
