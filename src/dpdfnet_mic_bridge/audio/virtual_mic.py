"""Linux virtual microphone lifecycle management."""

from __future__ import annotations

import platform
import shutil
import subprocess
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from .inventory import query_audio_inventory

LINUX_VIRTUAL_MIC_SINK_NAME = "dpdfnet_mic_bridge"
LINUX_VIRTUAL_MIC_SINK_DESCRIPTION = "DPDFNet_Mic_Bridge"
LINUX_VIRTUAL_MIC_SOURCE_NAME = f"{LINUX_VIRTUAL_MIC_SINK_NAME}.monitor"
LINUX_VIRTUAL_MIC_SOURCE_DESCRIPTION = (
    f"Monitor of {LINUX_VIRTUAL_MIC_SINK_DESCRIPTION}"
)

_VIRTUAL_MIC_VISIBILITY_TIMEOUT_SECONDS = 5.0


@dataclass(frozen=True)
class LinuxVirtualMic:
    """User-facing endpoints belonging to one managed virtual microphone."""

    render_name: str
    capture_name: str


def _first_line(text: str) -> str:
    stripped = text.strip()
    return stripped.splitlines()[0] if stripped else ""


def _run_checked(
    command: tuple[str, ...],
    *,
    operation: str,
) -> subprocess.CompletedProcess[str]:
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=3.0,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(f"{operation} failed: {exc}") from exc

    if completed.returncode != 0:
        detail = _first_line(completed.stderr) or _first_line(completed.stdout)
        if not detail:
            detail = f"command exited with status {completed.returncode}"
        raise RuntimeError(f"{operation} failed.\nDetails: {detail}")

    return completed


def _load_virtual_mic_module(pactl: str) -> list[str]:
    sink_command = (
        pactl,
        "load-module",
        "module-null-sink",
        f"sink_name={LINUX_VIRTUAL_MIC_SINK_NAME}",
        f"sink_properties=device.description={LINUX_VIRTUAL_MIC_SINK_DESCRIPTION}",
        "channels=1",
        "channel_map=mono",
    )
    completed = _run_checked(
        sink_command,
        operation="Could not create the Linux virtual microphone render sink",
    )
    module_id = completed.stdout.strip()
    if not module_id.isdecimal():
        detail = _first_line(completed.stdout) or "pactl returned no module ID"
        raise RuntimeError(
            "Could not create the Linux virtual microphone render sink.\n"
            f"Details: unexpected pactl response: {detail}"
        )

    modules = [module_id]

    source_command = (
        pactl,
        "load-module",
        "module-remap-source",
        f"master={LINUX_VIRTUAL_MIC_SOURCE_NAME}",
        f"source_name={LINUX_VIRTUAL_MIC_SINK_NAME}_capture",
        f"source_properties=device.description={LINUX_VIRTUAL_MIC_SINK_DESCRIPTION}_Capture",
    )
    try:
        completed2 = _run_checked(
            source_command,
            operation="Could not create the Linux virtual microphone capture source",
        )
        module_id2 = completed2.stdout.strip()
        if module_id2.isdecimal():
            modules.append(module_id2)
    except Exception:
        # If the remap fails, fall back to just the monitor source
        pass

    return modules


def _virtual_sink_is_visible() -> bool:
    inventory = query_audio_inventory()
    for item in inventory.devices:
        if int(item.get("max_output_channels", 0)) < 1:
            continue

        name = str(item.get("name", "")).casefold().replace(" ", "_")
        if (
            LINUX_VIRTUAL_MIC_SINK_NAME.casefold() in name
            or LINUX_VIRTUAL_MIC_SINK_DESCRIPTION.casefold() in name
        ):
            return True

    return False


def _wait_for_virtual_sink() -> None:
    deadline = time.monotonic() + _VIRTUAL_MIC_VISIBILITY_TIMEOUT_SECONDS
    last_error: Exception | None = None

    while time.monotonic() < deadline:
        try:
            if _virtual_sink_is_visible():
                return
        except Exception as exc:
            last_error = exc
        time.sleep(0.1)

    detail = ""
    if last_error is not None:
        text = _first_line(str(last_error))
        if text:
            detail = f"\nLast inventory error: {text}"

    raise RuntimeError(
        "The Linux virtual microphone was created, but its render sink did not "
        "appear in PortAudio within 5 seconds.\n"
        "Make sure PortAudio and pactl are connected to the same PipeWire or "
        "PulseAudio session."
        f"{detail}"
    )


def _unload_virtual_mic_module(pactl: str, module_ids: list[str]) -> None:
    for module_id in reversed(module_ids):
        try:
            subprocess.run(
                (pactl, "unload-module", module_id),
                capture_output=True,
                text=True,
                timeout=3.0,
            )
        except (OSError, subprocess.SubprocessError):
            pass


@contextmanager
def managed_linux_virtual_mic(
    *,
    enabled: bool,
) -> Iterator[LinuxVirtualMic | None]:
    """
    Create a render sink and app-facing monitor source for an automatic route.

    The sink is created before PortAudio device selection and its visibility is
    verified before execution continues. Normal context exit unloads the module
    and removes both endpoints.
    """
    if platform.system() != "Linux" or not enabled:
        yield None
        return

    pactl = shutil.which("pactl")
    if pactl is None:
        raise RuntimeError(
            "Automatic Linux virtual microphone creation requires `pactl`.\n"
            "Install the PulseAudio client utilities for your distribution; "
            "they also work with PipeWire's PulseAudio-compatible server. "
            "Alternatively, create a virtual microphone yourself and pin its "
            "render endpoint with --output."
        )

    module_ids = _load_virtual_mic_module(pactl)
    try:
        _wait_for_virtual_sink()
        capture_name = (
            f"{LINUX_VIRTUAL_MIC_SINK_DESCRIPTION}_Capture"
            if len(module_ids) > 1
            else LINUX_VIRTUAL_MIC_SOURCE_DESCRIPTION
        )
        yield LinuxVirtualMic(
            render_name=LINUX_VIRTUAL_MIC_SINK_DESCRIPTION,
            capture_name=capture_name,
        )
    finally:
        _unload_virtual_mic_module(pactl, module_ids)
