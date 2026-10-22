"""Linux virtual microphone lifecycle management."""

from __future__ import annotations

import platform
import json
import re
import shlex
import subprocess
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from .clients import client_environment, client_path

LINUX_VIRTUAL_MIC_SINK_NAME = "dpdfnet_mic_bridge"
LINUX_VIRTUAL_MIC_SINK_DESCRIPTION = "DPDFNet_Mic_Bridge"
LINUX_VIRTUAL_MIC_SOURCE_DESCRIPTION = "DPDFNet_Mic"

_VIRTUAL_MIC_VISIBILITY_TIMEOUT_SECONDS = 5.0


@dataclass(frozen=True)
class LinuxVirtualMic:
    """User-facing endpoints belonging to one managed virtual microphone."""

    render_name: str
    capture_name: str
    module_ids: tuple[str, ...] = ()


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
            env=client_environment(),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(f"{operation} failed: {exc}") from exc

    if completed.returncode != 0:
        detail = _first_line(completed.stderr) or _first_line(completed.stdout)
        if not detail:
            detail = f"command exited with status {completed.returncode}"
        raise RuntimeError(f"{operation} failed.\nDetails: {detail}")

    return completed


def _load_virtual_mic_module(
    pactl: str, sink_name: str, on_created=None, sample_rate: int = 48000
) -> list[str]:
    sink_command = (
        pactl,
        "load-module",
        "module-null-sink",
        f"sink_name={sink_name}",
        f"sink_properties=device.description={LINUX_VIRTUAL_MIC_SINK_DESCRIPTION}",
        # Server PCM defaults can quantize low-level enhanced audio to silence.
        "format=float32le",
        f"rate={sample_rate}",
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
        f"master={sink_name}.monitor",
        f"source_name={sink_name}_capture",
        f"source_properties=device.description={LINUX_VIRTUAL_MIC_SOURCE_DESCRIPTION}",
        "channels=1",
        "channel_map=mono",
    )
    try:
        if on_created:
            on_created(tuple(modules))
        completed2 = _run_checked(
            source_command,
            operation="Could not create the Linux virtual microphone capture source",
        )
        module_id2 = completed2.stdout.strip()
        if module_id2.isdecimal():
            modules.append(module_id2)
            if on_created:
                on_created(tuple(modules))
        else:
            raise RuntimeError("The capture source returned an invalid module ID.")
    except Exception:
        cleanup_owned_endpoint(sink_name, modules)
        raise

    return modules


def pulse_list(kind: str) -> list[dict]:
    if kind == "modules":
        # Some pactl releases omit module IDs in JSON. The tab-separated short
        # form includes IDs on both PulseAudio and PipeWire-Pulse servers.
        result = _run_checked(
            (client_path("pactl"), "list", "short", "modules"),
            operation="Could not check virtual microphone module ownership",
        )
        modules = []
        for line in result.stdout.splitlines():
            fields = line.split("\t", 3)
            if len(fields) < 2 or not fields[0].isdecimal():
                # Native PipeWire modules can print multiline argument blocks;
                # continuation lines do not identify a module to unload.
                continue
            modules.append(
                {
                    "index": int(fields[0]),
                    "name": fields[1],
                    "argument": fields[2] if len(fields) > 2 else "",
                }
            )
        return modules
    result = _run_checked(
        (client_path("pactl"), "--format=json", "list", kind),
        operation="Could not query the desktop audio session. Start PulseAudio or PipeWire's PulseAudio service",
    )
    items = json.loads(result.stdout)
    if not isinstance(items, list):
        raise RuntimeError("The audio server returned an invalid endpoint list.")
    return items


def _wait_for_virtual_sink(sink_name: str) -> None:
    deadline = time.monotonic() + _VIRTUAL_MIC_VISIBILITY_TIMEOUT_SECONDS
    last_error: Exception | None = None

    while time.monotonic() < deadline:
        try:
            sinks = pulse_list("sinks")
            sources = pulse_list("sources")
            if any(item["name"] == sink_name for item in sinks) and any(
                item["name"] == f"{sink_name}_capture" for item in sources
            ):
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
        "The audio server did not expose both virtual microphone endpoints within 5 seconds.\n"
        "A PulseAudio-compatible desktop audio session with remap-source support is required."
        f"{detail}"
    )


def _unload_virtual_mic_module(pactl: str, module_ids: list[str]) -> None:
    failures = []
    for module_id in reversed(module_ids):
        try:
            _run_checked(
                (pactl, "unload-module", module_id),
                operation=f"Could not remove owned audio module {module_id}",
            )
        except RuntimeError as exc:
            failures.append(str(exc))
    if failures:
        raise RuntimeError("\n".join(failures))


def cleanup_owned_endpoint(sink: str, module_ids: list[str]) -> None:
    """Recover after a stuck child; never unload a reused, unrelated module ID."""
    if not re.fullmatch(r"dpdfnet_mic_bridge_[0-9a-f]{32}", sink):
        raise ValueError("Invalid owned endpoint name")
    modules = {str(item["index"]): item for item in pulse_list("modules")}
    owned = []
    for module_id in module_ids:
        module = modules.get(str(module_id))
        if module is None:
            continue
        arguments = shlex.split(module.get("argument", ""))
        if (
            module["name"] == "module-null-sink" and f"sink_name={sink}" in arguments
        ) or (
            module["name"] == "module-remap-source"
            and f"master={sink}.monitor" in arguments
            and f"source_name={sink}_capture" in arguments
        ):
            owned.append(str(module_id))
    _unload_virtual_mic_module(client_path("pactl"), owned)


@contextmanager
def managed_linux_virtual_mic(
    *,
    enabled: bool,
    on_created=None,
    sample_rate: int = 48000,
) -> Iterator[LinuxVirtualMic | None]:
    """
    Create and verify an owned render sink and a real app-facing capture source.

    Unique server names isolate simultaneous runs and pre-existing user modules.
    A monitor-only fallback is not a successful virtual microphone setup.
    """
    if platform.system() != "Linux" or not enabled:
        yield None
        return

    pactl = client_path("pactl")
    sink_name = f"{LINUX_VIRTUAL_MIC_SINK_NAME}_{uuid.uuid4().hex}"

    def notify(module_ids):
        if on_created:
            on_created(
                LinuxVirtualMic(sink_name, f"{sink_name}_capture", tuple(module_ids))
            )

    module_ids = _load_virtual_mic_module(pactl, sink_name, notify, sample_rate)
    try:
        _wait_for_virtual_sink(sink_name)
        yield LinuxVirtualMic(
            render_name=sink_name,
            capture_name=f"{sink_name}_capture",
            module_ids=tuple(module_ids),
        )
    finally:
        cleanup_owned_endpoint(sink_name, module_ids)
