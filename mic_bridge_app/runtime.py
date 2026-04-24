"""
Implementation of the `run` command.

This module owns device resolution, optional Linux virtual-mic creation,
one-shot model download, stream setup, the callback, live alerts, and
shutdown.
"""

from __future__ import annotations

import argparse
import platform
import shutil
import subprocess
import sys
import time
from queue import Empty, SimpleQueue

import numpy as np

from .audio_system import (
    build_device_probes,
    choose_best_auto_input,
    choose_best_auto_output,
    device_info_from_inventory,
    format_jack_block_ms_hints,
    has_bridge_output_tags,
    hostapi_filter_key_for_name,
    hostapi_name_for_device,
    infer_device_tags,
    is_likely_virtual_bridge_output,
    is_power_of_two,
    is_viable_auto_input_candidate,
    jack_block_ms_for_frames,
    linux_virtual_mic_command,
    linux_virtual_mic_command_args,
    negotiate_stream_channels,
    query_audio_inventory,
    recommended_linux_jack_block_frames,
    resolve_effective_block_size,
    same_hostapi_guidance,
    suggest_run_pair,
)
from .constants import (
    DEFAULT_BLOCK_MS,
    LINUX_VIRTUAL_MIC_DESCRIPTION,
    LINUX_VIRTUAL_MIC_SINK_NAME,
)
from .dependencies import require_dpdfnet, require_sounddevice
from .domain import (
    AudioFifo,
    AudioInventory,
    CallbackStatusCounter,
    DeviceProbe,
    DeviceSelection,
    LinuxVirtualMicLease,
    ProbeSnapshot,
    ResolvedRunSelection,
    RunConfig,
)
from .modeling import resolve_stream_sample_rate
from .notifications import RuntimeUserNotifier

_CALLBACK_RUNTIME_ISSUE_LABELS: dict[str, str] = {
    "input_underflow": "capture underflow",
    "input_overflow": "capture overflow",
    "output_underflow": "playback underflow",
    "output_overflow": "playback overflow",
}


def _eprint(*args: object) -> None:
    print(*args, file=sys.stderr)


def _scan_all_device_probes(sample_rate: int) -> ProbeSnapshot:
    inventory = query_audio_inventory()
    input_probes, output_probes = build_device_probes(
        inventory=inventory,
        hostapi_filter="all",
        samplerate=sample_rate,
    )
    return ProbeSnapshot(
        inventory=inventory,
        input_probes=input_probes,
        output_probes=output_probes,
    )


def _get_current_run_device_info(
    *,
    inventory: AudioInventory,
    device_id: int,
    role: str,
) -> dict:
    """Resolve a device ID against one already-queried PortAudio inventory."""
    if role not in {"input", "output"}:
        raise ValueError(f"Unsupported device role '{role}'.")

    try:
        return device_info_from_inventory(inventory, device_id)
    except ValueError as exc:
        lines = [
            f"{role.title()} device ID {device_id} is not available.",
            "Device IDs can change after reboots or audio-session churn.",
        ]

        if platform.system() == "Windows" and role == "output":
            lines.append(
                "If this should be VB-CABLE, make sure it is still installed and visible."
            )

        lines.extend(
            [
                "Run:",
                "  python mic_bridge.py list-devices",
                f"Then retry with a fresh --{role} <id>.",
            ]
        )
        raise ValueError("\n".join(lines)) from exc


def _candidate_input_hostapis(input_probes: list[DeviceProbe]) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()

    for probe in input_probes:
        if not is_viable_auto_input_candidate(probe):
            continue
        if probe.hostapi_name not in seen:
            seen.add(probe.hostapi_name)
            names.append(probe.hostapi_name)

    return names


def _bridge_output_hostapis(output_probes: list[DeviceProbe]) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()

    for probe in output_probes:
        if not is_likely_virtual_bridge_output(probe):
            continue
        if probe.hostapi_name not in seen:
            seen.add(probe.hostapi_name)
            names.append(probe.hostapi_name)

    return names


def _first_line(text: str | None) -> str | None:
    if text is None:
        return None

    stripped = text.strip()
    if not stripped:
        return None

    line = stripped.splitlines()[0]
    if len(line) > 180:
        return line[:177] + "..."
    return line


def _build_no_auto_input_handoff(
    *,
    sample_rate: int,
    output_hostapi_name: str | None,
    input_probes: list[DeviceProbe],
) -> str:
    visible_input_hostapis = _candidate_input_hostapis(input_probes)

    if output_hostapi_name is not None and visible_input_hostapis:
        return (
            "Could not auto-select an input on the chosen output host API.\n\n"
            f"Output host API: {output_hostapi_name}\n"
            f"Microphone-like inputs seen on: {', '.join(visible_input_hostapis)}\n"
            "Run:\n"
            "  python mic_bridge.py list-devices\n"
            "Then pin --input <id> and --output <id>."
        )

    return (
        "Could not auto-select a microphone input.\n\n"
        f"No likely microphone input opened at {sample_rate} Hz.\n"
        "Run:\n"
        "  python mic_bridge.py list-devices\n"
        "Then pin --input <id> and --output <id>."
    )


def _build_no_auto_output_handoff(
    *,
    input_hostapi_name: str | None,
    output_probes: list[DeviceProbe],
    virtual_mic_note: str | None,
) -> str:
    visible_bridge_hostapis = _bridge_output_hostapis(output_probes)

    if input_hostapi_name is not None and visible_bridge_hostapis:
        return (
            "Could not auto-select an output on the chosen input host API.\n\n"
            f"Input host API: {input_hostapi_name}\n"
            f"Visible bridge outputs use: {', '.join(visible_bridge_hostapis)}\n"
            "Run:\n"
            "  python mic_bridge.py list-devices\n"
            "Then re-run with matching IDs."
        )

    system = platform.system()

    if system == "Windows":
        return (
            "Could not auto-select a bridge output.\n\n"
            "Auto mode only picks bridge targets such as VB-CABLE 'CABLE Input', never speakers/headphones or 'CABLE Output'.\n"
            "Run:\n"
            "  python mic_bridge.py list-devices\n"
            "Then pin --output <id>, or both --input <id> and --output <id>."
        )

    if system == "Linux":
        extra_note = f"{virtual_mic_note}\n" if virtual_mic_note is not None else ""

        return (
            "Could not auto-select a bridge output.\n\n"
            "Auto mode only picks virtual mic targets, never speakers/headphones or monitor/source endpoints.\n"
            f"{extra_note}"
            "Run:\n"
            "  python mic_bridge.py list-devices\n"
            "Create a reusable target with:\n"
            f"  {linux_virtual_mic_command()}"
        )

    return (
        "Could not auto-select a bridge output.\n\n"
        "Auto mode only picks likely bridge-style outputs, not ordinary speakers/headphones or monitor/source endpoints.\n"
        "Run:\n"
        "  python mic_bridge.py list-devices\n"
        "Then re-run with --output <id> or with both --input <id> and --output <id>."
    )


def _build_no_auto_pair_handoff(
    *,
    sample_rate: int,
    input_probes: list[DeviceProbe],
    output_probes: list[DeviceProbe],
    virtual_mic_note: str | None,
) -> str:
    visible_input_hostapis = _candidate_input_hostapis(input_probes)
    visible_bridge_hostapis = _bridge_output_hostapis(output_probes)

    if not visible_input_hostapis:
        return _build_no_auto_input_handoff(
            sample_rate=sample_rate,
            output_hostapi_name=None,
            input_probes=input_probes,
        )

    if not visible_bridge_hostapis:
        return _build_no_auto_output_handoff(
            input_hostapi_name=None,
            output_probes=output_probes,
            virtual_mic_note=virtual_mic_note,
        )

    return (
        "Could not auto-match an input and bridge output on the same host API.\n\n"
        f"Visible microphone-like inputs use: {', '.join(visible_input_hostapis)}\n"
        f"Visible bridge outputs use: {', '.join(visible_bridge_hostapis)}\n"
        "Run:\n"
        "  python mic_bridge.py list-devices\n"
        "Then pin matching IDs."
    )


def _pactl_failure_detail(completed: subprocess.CompletedProcess[str]) -> str:
    detail = _first_line(completed.stderr) or _first_line(completed.stdout)
    if detail is not None:
        return detail
    return f"`pactl` exited with status {completed.returncode}."


def _extract_pactl_module_id(text: str | None) -> int | None:
    if text is None:
        return None

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        token = stripped.split()[0]
        if token.isdigit():
            return int(token)

    return None


def _linux_virtual_mic_module_id(pactl_path: str) -> int | None:
    try:
        completed = subprocess.run(
            [pactl_path, "list", "short", "modules"],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return None

    if completed.returncode != 0:
        return None

    for line in completed.stdout.splitlines():
        if not line.strip():
            continue
        raw_columns = line.split("\t")
        columns = raw_columns if len(raw_columns) >= 2 else line.split(maxsplit=2)
        if len(columns) < 2 or not columns[0].strip().isdigit():
            continue
        args_text = " ".join(columns[2:]).strip()
        if (
            columns[1].strip() == "module-null-sink"
            and f"sink_name={LINUX_VIRTUAL_MIC_SINK_NAME}" in args_text
        ):
            return int(columns[0].strip())
    return None


def _linux_virtual_mic_sink_exists(pactl_path: str) -> bool | None:
    try:
        completed = subprocess.run(
            [pactl_path, "list", "short", "sinks"],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return None

    if completed.returncode != 0:
        return None

    for line in completed.stdout.splitlines():
        if not line.strip():
            continue
        columns = line.split("\t") if "\t" in line else line.split()
        if len(columns) >= 2 and columns[1].strip() == LINUX_VIRTUAL_MIC_SINK_NAME:
            return True

    return False


def _try_create_linux_virtual_mic_target(
    lease: LinuxVirtualMicLease,
) -> tuple[str, str | None]:
    """Try to create the temporary Linux virtual mic target once."""
    pactl_path = shutil.which("pactl")
    if pactl_path is None:
        return "failed", "`pactl` was not found."

    try:
        sink_exists = _linux_virtual_mic_sink_exists(pactl_path)
    except KeyboardInterrupt as exc:
        raise KeyboardInterrupt(
            f"Interrupted while checking for '{LINUX_VIRTUAL_MIC_DESCRIPTION}'."
        ) from exc

    if sink_exists is True:
        return "already-exists", None

    command = list(linux_virtual_mic_command_args())
    command[0] = pactl_path

    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
        )
    except KeyboardInterrupt as exc:
        raise KeyboardInterrupt(
            f"Interrupted while creating '{LINUX_VIRTUAL_MIC_DESCRIPTION}'."
        ) from exc
    except OSError as exc:
        return "failed", str(exc)

    if completed.returncode != 0:
        return "failed", _pactl_failure_detail(completed)

    module_id = _extract_pactl_module_id(completed.stdout)
    if module_id is None:
        module_id = _linux_virtual_mic_module_id(pactl_path)
    if module_id is None:
        return (
            "failed",
            "The virtual mic target was created, but its Pactl module ID could not be determined for cleanup.",
        )

    lease.module_id = module_id
    lease.pactl_path = pactl_path
    time.sleep(0.35)
    return "created", None


def _looks_like_missing_unloaded_module(detail: str) -> bool:
    text = detail.casefold()
    return "no such entity" in text or "no such module" in text or "not found" in text


def _cleanup_auto_created_linux_virtual_mic(
    lease: LinuxVirtualMicLease,
) -> tuple[str | None, str | None]:
    """
    Unload the temporary Linux virtual mic target created for this run, if any.

    Returns:
        (success_message, handoff_message)
    """
    if platform.system() != "Linux" or lease.module_id is None:
        return None, None

    module_id = int(lease.module_id)
    command_display = f"pactl unload-module {module_id}"
    pactl_path = lease.pactl_path or shutil.which("pactl")
    if pactl_path is None:
        return (
            None,
            f"Could not remove the temporary Linux virtual mic target.\nIf it is still present, run:\n  {command_display}",
        )

    try:
        completed = subprocess.run(
            [pactl_path, "unload-module", str(module_id)],
            check=False,
            capture_output=True,
            text=True,
        )
    except KeyboardInterrupt:
        return (
            None,
            f"Interrupted while removing the temporary Linux virtual mic target.\nIf it is still present, run:\n  {command_display}",
        )
    except OSError as exc:
        return (
            None,
            f"Could not remove the temporary Linux virtual mic target.\nDetails: {exc}\nIf it is still present, run:\n  {command_display}",
        )

    if completed.returncode != 0:
        detail = _pactl_failure_detail(completed)
        if _looks_like_missing_unloaded_module(detail):
            lease.module_id = None
            lease.pactl_path = None
            return None, None
        return (
            None,
            f"Could not remove the temporary Linux virtual mic target cleanly.\nDetails: {detail}\nIf it is still present, run:\n  {command_display}",
        )

    lease.module_id = None
    lease.pactl_path = None
    return (
        f"Removed temporary Linux virtual mic target '{LINUX_VIRTUAL_MIC_DESCRIPTION}'.",
        None,
    )


def _looks_like_missing_model_error(exc: BaseException, model_name: str) -> bool:
    text = f"{type(exc).__name__}: {exc}".casefold()
    missing_markers = (
        "not found",
        "missing",
        "no such file",
        "does not exist",
        "could not find",
        "cannot find",
        "download",
    )
    model_markers = (
        model_name.casefold(),
        "model",
        ".onnx",
    )

    return any(marker in text for marker in missing_markers) and any(
        marker in text for marker in model_markers
    )


def _download_dpdfnet_model_once(model_name: str) -> None:
    display_command = f"dpdfnet download {model_name}"
    cli_path = shutil.which("dpdfnet")
    command = (
        [cli_path, "download", model_name]
        if cli_path is not None
        else [sys.executable, "-m", "dpdfnet", "download", model_name]
    )

    print(f"Model '{model_name}' is missing, downloading it once...")

    try:
        completed = subprocess.run(command, check=False)
    except KeyboardInterrupt as exc:
        raise KeyboardInterrupt(
            "Interrupted during automatic model download.\n"
            f"Run this once manually, then retry:\n"
            f"  {display_command}"
        ) from exc
    except OSError as exc:
        raise RuntimeError(
            f"Could not start the DPDFNet downloader.\nRun this once manually, then try again:\n  {display_command}"
        ) from exc

    if completed.returncode != 0:
        raise RuntimeError(
            "Automatic model download did not finish successfully.\n"
            "Run this once manually, then try again:\n"
            f"  {display_command}"
        )

    print("Download finished, loading the model...")


def _build_stream_enhancer(dpdfnet_mod, *, model_name: str, verbose: bool):
    try:
        return dpdfnet_mod.StreamEnhancer(model=model_name, verbose=verbose)
    except Exception as exc:
        if not _looks_like_missing_model_error(exc, model_name):
            raise

    _download_dpdfnet_model_once(model_name)

    try:
        return dpdfnet_mod.StreamEnhancer(model=model_name, verbose=verbose)
    except Exception as exc:
        raise RuntimeError(
            f"Model '{model_name}' still could not be loaded after the automatic download.\n"
            f"Run this once manually, then retry:\n  dpdfnet download {model_name}\nDetails: {_first_line(str(exc)) or exc}"
        ) from exc


def _resolve_run_device_selection(
    *,
    sample_rate: int,
    requested_input_id: int | None,
    requested_output_id: int | None,
    linux_virtual_mic_lease: LinuxVirtualMicLease,
) -> ResolvedRunSelection:
    """
    Resolve explicit and auto-selected device IDs.

    The result stays tied to one current PortAudio inventory snapshot.
    """
    if requested_input_id is not None and requested_output_id is not None:
        return ResolvedRunSelection(
            selection=DeviceSelection(
                input_device_id=requested_input_id,
                output_device_id=requested_output_id,
                input_was_auto_selected=False,
                output_was_auto_selected=False,
                linux_virtual_mic_was_auto_created=False,
            ),
            inventory=query_audio_inventory(),
        )

    snapshot = _scan_all_device_probes(sample_rate)
    linux_virtual_mic_was_auto_created = False
    virtual_mic_note: str | None = None

    def finish_selection(
        *,
        input_device_id: int,
        output_device_id: int,
        input_was_auto_selected: bool,
        output_was_auto_selected: bool,
    ) -> ResolvedRunSelection:
        return ResolvedRunSelection(
            selection=DeviceSelection(
                input_device_id=input_device_id,
                output_device_id=output_device_id,
                input_was_auto_selected=input_was_auto_selected,
                output_was_auto_selected=output_was_auto_selected,
                linux_virtual_mic_was_auto_created=linux_virtual_mic_was_auto_created,
            ),
            inventory=snapshot.inventory,
        )

    def visible_bridge_output_exists() -> bool:
        return any(
            is_likely_virtual_bridge_output(probe) for probe in snapshot.output_probes
        )

    def maybe_create_linux_virtual_mic() -> None:
        nonlocal snapshot
        nonlocal linux_virtual_mic_was_auto_created
        nonlocal virtual_mic_note

        if platform.system() != "Linux" or requested_output_id is not None:
            return

        if visible_bridge_output_exists():
            return

        print(
            f"No likely virtual mic target is visible, trying to create temporary '{LINUX_VIRTUAL_MIC_DESCRIPTION}'..."
        )
        status, detail = _try_create_linux_virtual_mic_target(linux_virtual_mic_lease)
        if status == "failed":
            virtual_mic_note = f"Automatic Linux target creation failed for '{LINUX_VIRTUAL_MIC_DESCRIPTION}': {detail or 'unknown `pactl` error.'}"
            return

        snapshot = _scan_all_device_probes(sample_rate)

        if visible_bridge_output_exists():
            if status == "created":
                linux_virtual_mic_was_auto_created = True
                print(
                    f"Created temporary Linux target '{LINUX_VIRTUAL_MIC_DESCRIPTION}'."
                )
            else:
                print(f"Using existing Linux target '{LINUX_VIRTUAL_MIC_DESCRIPTION}'.")
            return

        if status == "created":
            linux_virtual_mic_was_auto_created = True
            virtual_mic_note = f"Created '{LINUX_VIRTUAL_MIC_DESCRIPTION}', but PortAudio still does not expose it as a usable output."
            return

        virtual_mic_note = f"'{LINUX_VIRTUAL_MIC_DESCRIPTION}' already exists, but PortAudio still does not expose it as a usable output."

    if requested_input_id is not None and requested_output_id is None:
        input_info = _get_current_run_device_info(
            inventory=snapshot.inventory,
            device_id=requested_input_id,
            role="input",
        )
        input_hostapi = hostapi_name_for_device(
            input_info,
            snapshot.inventory.hostapi_names,
        )

        output_probe = choose_best_auto_output(
            snapshot.output_probes,
            hostapi_name=input_hostapi,
        )
        if output_probe is not None:
            return finish_selection(
                input_device_id=requested_input_id,
                output_device_id=output_probe.device_id,
                input_was_auto_selected=False,
                output_was_auto_selected=True,
            )

        if not visible_bridge_output_exists():
            maybe_create_linux_virtual_mic()
            input_info = _get_current_run_device_info(
                inventory=snapshot.inventory,
                device_id=requested_input_id,
                role="input",
            )
            input_hostapi = hostapi_name_for_device(
                input_info,
                snapshot.inventory.hostapi_names,
            )
            output_probe = choose_best_auto_output(
                snapshot.output_probes,
                hostapi_name=input_hostapi,
            )
            if output_probe is not None:
                return finish_selection(
                    input_device_id=requested_input_id,
                    output_device_id=output_probe.device_id,
                    input_was_auto_selected=False,
                    output_was_auto_selected=True,
                )

        raise ValueError(
            _build_no_auto_output_handoff(
                input_hostapi_name=input_hostapi,
                output_probes=snapshot.output_probes,
                virtual_mic_note=virtual_mic_note,
            )
        )

    if requested_input_id is None and requested_output_id is not None:
        output_info = _get_current_run_device_info(
            inventory=snapshot.inventory,
            device_id=requested_output_id,
            role="output",
        )
        output_hostapi = hostapi_name_for_device(
            output_info,
            snapshot.inventory.hostapi_names,
        )

        input_probe = choose_best_auto_input(
            snapshot.input_probes,
            hostapi_name=output_hostapi,
        )
        if input_probe is not None:
            return finish_selection(
                input_device_id=input_probe.device_id,
                output_device_id=requested_output_id,
                input_was_auto_selected=True,
                output_was_auto_selected=False,
            )

        raise ValueError(
            _build_no_auto_input_handoff(
                sample_rate=sample_rate,
                output_hostapi_name=output_hostapi,
                input_probes=snapshot.input_probes,
            )
        )

    suggested_pair = suggest_run_pair(snapshot.input_probes, snapshot.output_probes)
    if suggested_pair is not None:
        input_probe, output_probe = suggested_pair
        return finish_selection(
            input_device_id=input_probe.device_id,
            output_device_id=output_probe.device_id,
            input_was_auto_selected=True,
            output_was_auto_selected=True,
        )

    if not visible_bridge_output_exists():
        maybe_create_linux_virtual_mic()
        suggested_pair = suggest_run_pair(snapshot.input_probes, snapshot.output_probes)
        if suggested_pair is not None:
            input_probe, output_probe = suggested_pair
            return finish_selection(
                input_device_id=input_probe.device_id,
                output_device_id=output_probe.device_id,
                input_was_auto_selected=True,
                output_was_auto_selected=True,
            )

    raise ValueError(
        _build_no_auto_pair_handoff(
            sample_rate=sample_rate,
            input_probes=snapshot.input_probes,
            output_probes=snapshot.output_probes,
            virtual_mic_note=(
                None
                if virtual_mic_note is None
                else (
                    f"{virtual_mic_note}\n"
                    "The run will remove any temporary target it created before exiting."
                )
            ),
        )
    )


def _build_run_config(
    args: argparse.Namespace,
    *,
    linux_virtual_mic_lease: LinuxVirtualMicLease,
) -> RunConfig:
    """
    Resolve CLI args into a concrete run configuration.

    This enforces sample-rate, host-API, channel, and block-size policy.
    """

    sample_rate = resolve_stream_sample_rate(args.model, args.samplerate, context="run")

    requested_input_id = None if args.input is None else int(args.input)
    requested_output_id = None if args.output is None else int(args.output)

    resolved_selection = _resolve_run_device_selection(
        sample_rate=sample_rate,
        requested_input_id=requested_input_id,
        requested_output_id=requested_output_id,
        linux_virtual_mic_lease=linux_virtual_mic_lease,
    )
    selection = resolved_selection.selection
    inventory = resolved_selection.inventory

    input_info = _get_current_run_device_info(
        inventory=inventory,
        device_id=selection.input_device_id,
        role="input",
    )
    output_info = _get_current_run_device_info(
        inventory=inventory,
        device_id=selection.output_device_id,
        role="output",
    )

    if int(input_info["max_input_channels"]) < 1:
        raise ValueError(
            f"Selected input device {selection.input_device_id} exposes no input channels: {input_info['name']}"
        )

    if int(output_info["max_output_channels"]) < 1:
        raise ValueError(
            f"Selected output device {selection.output_device_id} exposes no output channels: {output_info['name']}"
        )

    input_hostapi = hostapi_name_for_device(input_info, inventory.hostapi_names)
    output_hostapi = hostapi_name_for_device(output_info, inventory.hostapi_names)

    if int(input_info["hostapi"]) != int(output_info["hostapi"]):
        raise ValueError(
            "Input and output devices must use the same host API in Version 0.\n"
            f"Input : id={selection.input_device_id}, hostapi={input_hostapi}, name={input_info['name']}\n"
            f"Output: id={selection.output_device_id}, hostapi={output_hostapi}, name={output_info['name']}\n"
            f"{same_hostapi_guidance()}"
        )

    requested_block_ms = None if args.block_ms is None else float(args.block_ms)
    block_size, effective_block_ms, block_policy = resolve_effective_block_size(
        sample_rate=sample_rate,
        requested_block_ms=requested_block_ms,
        hostapi_name=input_hostapi,
    )

    input_channels = negotiate_stream_channels(
        device_id=selection.input_device_id,
        direction="input",
        samplerate=sample_rate,
        dtype="float32",
    )
    output_channels = negotiate_stream_channels(
        device_id=selection.output_device_id,
        direction="output",
        samplerate=sample_rate,
        dtype="float32",
    )

    input_name = str(input_info["name"])
    output_name = str(output_info["name"])

    return RunConfig(
        model_name=args.model,
        sample_rate=sample_rate,
        block_ms=effective_block_ms,
        requested_block_ms=requested_block_ms,
        block_policy=block_policy,
        block_size=block_size,
        input_device_id=selection.input_device_id,
        input_device_name=input_name,
        input_hostapi_name=input_hostapi,
        input_channels=input_channels,
        input_tags=infer_device_tags(input_name),
        input_was_auto_selected=selection.input_was_auto_selected,
        output_device_id=selection.output_device_id,
        output_device_name=output_name,
        output_hostapi_name=output_hostapi,
        output_channels=output_channels,
        output_tags=infer_device_tags(output_name),
        output_was_auto_selected=selection.output_was_auto_selected,
        linux_virtual_mic_was_auto_created=selection.linux_virtual_mic_was_auto_created,
        latency=str(args.latency),
    )


def _print_run_summary(config: RunConfig) -> None:
    system = platform.system()
    input_tags = set(config.input_tags)
    output_tags = set(config.output_tags)
    input_hostapi_key = hostapi_filter_key_for_name(config.input_hostapi_name)

    # Reuse the same positive bridge-tag rule as auto mode so the run summary
    # does not quietly drift away from the selection policy.
    output_looks_like_bridge_target = (
        has_bridge_output_tags(config.output_tags)
        and "monitor" not in output_tags
        and "vb-cable-recording" not in output_tags
    )
    should_print_generic_nonbridge_warning = (
        not output_looks_like_bridge_target
        and "monitor" not in output_tags
        and "vb-cable-recording" not in output_tags
    )

    if config.block_policy == "explicit":
        block_line = (
            f"{config.block_size} frames "
            f"({config.block_ms:.3f} ms effective, "
            f"{config.requested_block_ms:.3f} ms requested)"
        )
    elif config.block_policy == "auto-linux-jack":
        block_line = (
            f"{config.block_size} frames "
            f"({config.block_ms:.3f} ms auto-selected for Linux/JACK)"
        )
    else:
        block_line = f"{config.block_size} frames ({config.block_ms:.3f} ms default)"

    print()
    print("Run configuration")
    print("-----------------")
    print(f"Model                : {config.model_name}")
    print(f"Model/native SR      : {config.sample_rate} Hz")
    print(f"Block size           : {block_line}")
    print(f"Latency hint         : {config.latency}")
    print(
        f"Input device         : [{config.input_device_id}] {config.input_device_name}"
    )
    print(f"Input host API       : {config.input_hostapi_name}")
    print(f"Input channels open  : {config.input_channels}")
    print(
        f"Output device        : [{config.output_device_id}] {config.output_device_name}"
    )
    print(f"Output host API      : {config.output_hostapi_name}")
    print(f"Output channels open : {config.output_channels}")
    print()
    print("Notes:")

    notes: list[str] = []

    if config.input_was_auto_selected and config.output_was_auto_selected:
        notes.append("Input and output were auto-selected.")
    elif config.input_was_auto_selected:
        notes.append("Input was auto-selected.")
    elif config.output_was_auto_selected:
        notes.append("Output was auto-selected.")

    if config.linux_virtual_mic_was_auto_created:
        notes.append(
            f"'{LINUX_VIRTUAL_MIC_DESCRIPTION}' was created for this run and will be removed on exit."
        )
        notes.append(
            "Its numeric output ID is temporary. Create the target manually if you want a reusable one."
        )

    notes.append("First enhanced audio arrives after about one model window (~20 ms).")
    if config.input_channels > 1:
        notes.append("Input is downmixed to mono before enhancement.")
    if config.output_channels > 1:
        notes.append("Enhanced mono is duplicated across all opened output channels.")
    else:
        notes.append("Enhanced mono is written to one output channel.")
    notes.append("Important runtime problems raise a short attention alert.")

    if system == "Windows":
        notes.append(
            "If this is VB-CABLE 'CABLE Input', apps should use 'CABLE Output' as the microphone."
        )
        if input_hostapi_key != "wasapi":
            notes.append("WASAPI is the recommended Windows host API starting point.")

    elif system == "Linux":
        notes.append(
            "If this is your virtual mic target, apps should select it first, then its monitor/source if needed."
        )
        if input_hostapi_key not in {"pipewire", "pulse", "jack", "alsa"}:
            notes.append(
                "A PipeWire-compatible PortAudio host API is preferred when available."
            )

        if input_hostapi_key == "jack":
            auto_frames = recommended_linux_jack_block_frames(config.sample_rate)
            auto_ms = jack_block_ms_for_frames(auto_frames, config.sample_rate)
            if config.block_policy == "auto-linux-jack":
                notes.append(
                    f"Linux/JACK auto-selected {config.block_ms:.3f} ms ({config.block_size} frames)."
                )
            elif not is_power_of_two(config.block_size):
                notes.append(
                    "Linux/JACK: this block size is not an exact power-of-two frame count."
                )
                notes.append(
                    f"If you hear glitches, try --block-ms {auto_ms:.3f} or another exact value such as {format_jack_block_ms_hints(config.sample_rate)} ms."
                )

    if (
        config.block_policy == "default-10ms"
        and abs(config.block_ms - DEFAULT_BLOCK_MS) > 1e-6
    ):
        notes.append(
            "Block duration is rounded to whole frames, so the effective milliseconds differ slightly."
        )

    if should_print_generic_nonbridge_warning:
        if system == "Windows":
            notes.append(
                "Warning: output does not look like a bridge target. For VB-CABLE, --output should usually be 'CABLE Input'."
            )
        elif system == "Linux":
            notes.append(
                "Warning: output does not look like a bridge target. For the usual Linux flow, --output should be a virtual mic sink."
            )
        else:
            notes.append(
                "Warning: output does not look like a bridge target. Enhanced audio will be sent there directly."
            )

    if "vb-cable-recording" in input_tags or "vb-cable-playback" in input_tags:
        notes.append(
            "Warning: input looks like a VB-CABLE endpoint, not a physical microphone."
        )
    if "vb-cable-recording" in output_tags:
        notes.append(
            "Warning: output looks like a recording endpoint. For VB-CABLE, you usually want 'CABLE Input'."
        )
    if "monitor" in input_tags:
        notes.append(
            "Warning: input looks like a monitor/source, not a physical microphone."
        )
    if system == "Linux" and "monitor" in output_tags:
        notes.append(
            "Warning: output looks like a monitor/source, not a sink playback device."
        )

    for line in notes:
        print(f"  - {line}")
    print()


def _format_runtime_issue_summary(status_counter: CallbackStatusCounter) -> str | None:
    parts: list[str] = []

    for name, count in status_counter.nonzero_runtime_issue_counts():
        label = _CALLBACK_RUNTIME_ISSUE_LABELS.get(name, name.replace("_", " "))
        if count == 1:
            parts.append(label)
        else:
            parts.append(f"{label} x{count}")

    if not parts:
        return None

    return ", ".join(parts)


def _emit_live_runtime_alert(
    *,
    notifier: RuntimeUserNotifier,
    status_counter: CallbackStatusCounter,
    alert_key: str,
) -> None:
    summary = _format_runtime_issue_summary(status_counter)

    if alert_key == "first-runtime-issue":
        lines = [
            "PortAudio reported a real-time audio problem.",
        ]
        if summary is not None:
            lines.append(f"So far: {summary}.")
        lines.append(
            "If this repeats, try a larger --block-ms or a different device pair."
        )
        notifier.attention(
            title="Audio glitch detected",
            body="\n".join(lines),
            urgency="normal",
        )
        return

    if alert_key == "repeated-runtime-issues":
        lines = [
            "Real-time audio problems have been reported repeatedly.",
        ]
        if summary is not None:
            lines.append(f"So far: {summary}.")
        lines.append(
            "If it keeps happening, stop the run, re-run list-devices, or try a larger --block-ms."
        )
        notifier.attention(
            title="Audio glitches are continuing",
            body="\n".join(lines),
            urgency="normal",
        )
        return


def _drain_live_runtime_alerts(
    *,
    notifier: RuntimeUserNotifier,
    status_counter: CallbackStatusCounter,
    live_alerts: SimpleQueue[str],
) -> None:
    while True:
        try:
            alert_key = live_alerts.get_nowait()
        except Empty:
            return

        _emit_live_runtime_alert(
            notifier=notifier,
            status_counter=status_counter,
            alert_key=alert_key,
        )


def _looks_like_device_loss(detail: str) -> bool:
    text = detail.casefold()
    markers = (
        "device unavailable",
        "device removed",
        "device disconnected",
        "device lost",
        "device is no longer available",
        "invalid device",
        "no device",
        "unanticipated host error",
        "host error",
        "stream closed",
        "stream is stopped",
        "jack server is not running",
        "broken pipe",
        "connection terminated",
        "transport is not running",
    )
    return any(marker in text for marker in markers)


def _notify_unexpected_stream_stop(
    *,
    notifier: RuntimeUserNotifier,
    detail: str,
    callback_failed: bool,
) -> None:
    if callback_failed:
        summary = "The live run aborted inside the audio callback and will exit."
    elif _looks_like_device_loss(detail):
        summary = "The selected audio device disappeared, became unavailable, or the audio backend dropped the stream."
    else:
        summary = "The live audio stream stopped unexpectedly and the run will exit."

    notifier.attention(
        title="Bridge stopped unexpectedly",
        body=f"{summary}\nSee the terminal for the next step.",
        urgency="critical",
    )


def _append_user_note(message: str, note: str) -> str:
    base = message.strip()
    if not base:
        return note
    return f"{base}\n{note}"


def _run_command_impl(
    args: argparse.Namespace,
    *,
    sd_mod,
    dpdfnet_mod,
    notifier: RuntimeUserNotifier,
    linux_virtual_mic_lease: LinuxVirtualMicLease,
) -> int:
    if platform.system() not in ("Windows", "Linux"):
        _eprint(
            "[note] This script is primarily intended for Windows 10/11 and Linux PipeWire systems. It may still work elsewhere, but that is not the supported target."
        )

    config = _build_run_config(
        args,
        linux_virtual_mic_lease=linux_virtual_mic_lease,
    )
    _print_run_summary(config)

    print("Preparing DPDFNet model...")
    model_start = time.perf_counter()
    enhancer = _build_stream_enhancer(
        dpdfnet_mod,
        model_name=config.model_name,
        verbose=bool(args.verbose),
    )
    model_elapsed = time.perf_counter() - model_start
    print(f"Model ready in {model_elapsed:.2f} s.")

    output_fifo = AudioFifo()
    status_counter = CallbackStatusCounter()
    fatal_callback_error: list[BaseException] = []
    live_alerts: SimpleQueue[str] = SimpleQueue()
    streaming_started = False

    def audio_callback(indata, outdata, frames, time_info, status) -> None:
        """
        Real-time audio callback.

        Downmix to mono, run the enhancer, stage output through the FIFO, and
        fill the output buffer. Live alerts are queued here and emitted by the
        main thread.
        """
        del time_info  # unused in Version 0

        try:
            for alert_key in status_counter.observe(status):
                live_alerts.put(alert_key)

            if indata.ndim != 2:
                raise RuntimeError(
                    f"Expected 2D input buffer, got shape {indata.shape}"
                )

            if indata.shape[1] == 1:
                mono_in = np.asarray(indata[:, 0], dtype=np.float32)
            else:
                mono_in = np.mean(indata, axis=1, dtype=np.float32)

            enhanced = enhancer.process(mono_in, sample_rate=config.sample_rate)
            output_fifo.push(enhanced)

            mono_out = output_fifo.pop(frames)

            outdata.fill(0.0)
            outdata[:, :] = mono_out[:, None]

        except Exception as exc:
            if not fatal_callback_error:
                fatal_callback_error.append(exc)
            outdata.fill(0.0)
            raise sd_mod.CallbackAbort from exc

    print("Opening duplex audio stream...")

    stream_error: Exception | None = None
    interrupted = False

    try:
        with sd_mod.Stream(
            samplerate=config.sample_rate,
            blocksize=config.block_size,
            device=(config.input_device_id, config.output_device_id),
            channels=(config.input_channels, config.output_channels),
            dtype="float32",
            latency=config.latency,
            callback=audio_callback,
        ):
            streaming_started = True
            print("Streaming.")
            print("Press Ctrl+C to stop.")
            print()

            while True:
                _drain_live_runtime_alerts(
                    notifier=notifier,
                    status_counter=status_counter,
                    live_alerts=live_alerts,
                )
                time.sleep(0.5)

    except KeyboardInterrupt:
        interrupted = True
    except Exception as exc:
        stream_error = exc
    finally:
        _drain_live_runtime_alerts(
            notifier=notifier,
            status_counter=status_counter,
            live_alerts=live_alerts,
        )

    if interrupted:
        print()
        print("Stopping bridge...")
    elif stream_error is not None:
        if fatal_callback_error:
            _notify_unexpected_stream_stop(
                notifier=notifier,
                detail=_first_line(str(fatal_callback_error[0])) or "callback abort",
                callback_failed=True,
            )
            lines = ["The audio callback aborted unexpectedly."]
            detail = _first_line(str(fatal_callback_error[0]))
            if detail is not None:
                lines.append(f"Details: {detail}")
            lines.extend(
                [
                    "Run:",
                    "  python mic_bridge.py list-devices",
                    "Then retry, or try a different device pair or block size.",
                ]
            )
            raise RuntimeError("\n".join(lines)) from fatal_callback_error[0]

        detail = _first_line(str(stream_error)) or str(stream_error)
        if streaming_started:
            _notify_unexpected_stream_stop(
                notifier=notifier,
                detail=detail,
                callback_failed=False,
            )

        lines = ["Failed to open or run the audio stream.", f"Details: {detail}"]
        lines.extend(
            [
                "Run:",
                "  python mic_bridge.py list-devices",
                "Then retry with fresh IDs if needed.",
            ]
        )
        raise RuntimeError("\n".join(lines)) from stream_error

    nonzero_status_lines = status_counter.nonzero_lines()
    if nonzero_status_lines:
        print()
        print("Callback status summary:")
        for line in nonzero_status_lines:
            print(line)
        print()
    else:
        print()
        print("No callback underflow/overflow flags were observed.")
        print()

    return 0


def run_command(args: argparse.Namespace) -> int:
    sd_mod = require_sounddevice()
    dpdfnet_mod = require_dpdfnet()
    notifier = RuntimeUserNotifier()
    linux_virtual_mic_lease = LinuxVirtualMicLease()

    result: int | None = None
    interrupted: KeyboardInterrupt | None = None
    failure: Exception | None = None

    try:
        result = _run_command_impl(
            args,
            sd_mod=sd_mod,
            dpdfnet_mod=dpdfnet_mod,
            notifier=notifier,
            linux_virtual_mic_lease=linux_virtual_mic_lease,
        )
    except KeyboardInterrupt as exc:
        interrupted = exc
    except Exception as exc:
        failure = exc

    cleanup_success_message, cleanup_handoff_message = (
        _cleanup_auto_created_linux_virtual_mic(linux_virtual_mic_lease)
    )
    cleanup_note = cleanup_handoff_message or cleanup_success_message

    if interrupted is not None:
        if cleanup_note is not None:
            base_message = str(interrupted).strip() or "Interrupted."
            raise KeyboardInterrupt(
                _append_user_note(base_message, cleanup_note)
            ) from interrupted
        raise interrupted

    if failure is not None:
        if cleanup_note is not None:
            raise RuntimeError(
                _append_user_note(str(failure), cleanup_note)
            ) from failure
        raise failure

    if cleanup_handoff_message is not None:
        raise RuntimeError(cleanup_handoff_message)

    if cleanup_success_message is not None:
        print(cleanup_success_message)
        print()

    return 0 if result is None else result
