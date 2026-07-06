"""Linux ephemeral virtual microphone target helpers."""

from __future__ import annotations

import platform
import shutil
import subprocess
import time
from collections.abc import Iterator
from contextlib import contextmanager

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


@contextmanager
def managed_linux_virtual_mic() -> Iterator[None]:
    """
    Create an ephemeral virtual mic node on Linux during streaming.
    It is automatically destroyed upon exit.
    """
    if platform.system() != "Linux":
        yield
        return

    pw_cli = shutil.which("pw-cli")
    pactl = shutil.which("pactl")

    node_id: str | None = None
    module_id: str | None = None

    if pw_cli is not None:
        try:
            completed = subprocess.run(
                linux_pipewire_virtual_mic_command_args(),
                capture_output=True,
                text=True,
                check=True,
                timeout=2.0,
            )
            out = completed.stdout.strip()
            if out.isdigit():
                node_id = out
            else:
                nums = [
                    w
                    for w in out.replace('"', " ").replace("'", " ").split()
                    if w.isdigit()
                ]
                if nums:
                    node_id = nums[-1]
            time.sleep(0.5)
        except Exception:
            pass

    elif pactl is not None:
        try:
            completed = subprocess.run(
                linux_virtual_mic_command_args(),
                capture_output=True,
                text=True,
                check=True,
                timeout=2.0,
            )
            out = completed.stdout.strip()
            if out.isdigit():
                module_id = out
            time.sleep(0.5)
        except Exception:
            pass

    try:
        yield
    finally:
        if pw_cli is not None and node_id is not None:
            try:
                subprocess.run(
                    [pw_cli, "destroy", node_id], capture_output=True, timeout=2.0
                )
            except Exception:
                pass
        elif pactl is not None and module_id is not None:
            try:
                subprocess.run(
                    [pactl, "unload-module", module_id],
                    capture_output=True,
                    timeout=2.0,
                )
            except Exception:
                pass
