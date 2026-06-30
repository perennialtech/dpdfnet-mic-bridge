"""Linux reusable virtual microphone target helpers."""

from __future__ import annotations

import shlex

LINUX_REUSABLE_VIRTUAL_MIC_SINK_NAME = "dpdfnet_mic"
LINUX_REUSABLE_VIRTUAL_MIC_DESCRIPTION = "DPDFNet_Mic"


def linux_virtual_mic_command_args() -> tuple[str, ...]:
    """Return the recommended `pactl load-module ...` command as argv."""
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


def linux_virtual_mic_command() -> str:
    """Return the same recommended Linux virtual-mic command as display text."""
    return shlex.join(linux_virtual_mic_command_args())
