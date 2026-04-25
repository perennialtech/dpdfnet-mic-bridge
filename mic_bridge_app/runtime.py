"""
Implementation of the `run` command.

This module owns device resolution, optional Linux virtual-mic creation,
one-shot model download, stream setup, the callback, live alerts, and
shutdown.
"""

from __future__ import annotations

import argparse
import platform
from collections.abc import Callable
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
    has_bridge_output_tags,
    hostapi_filter_key_for_name,
    hostapi_name_for_device,
    infer_device_tags,
    is_likely_virtual_bridge_output,
    is_viable_auto_input_candidate,
    linux_virtual_mic_command,
    linux_virtual_mic_command_args,
    negotiate_stream_channels,
    query_audio_inventory,
    resolve_effective_block_size,
    same_hostapi_guidance,
    suggest_run_pair,
)
from .constants import (
    LINUX_VIRTUAL_MIC_DISCOVERY_POLL_SECONDS,
    LINUX_VIRTUAL_MIC_DISCOVERY_TIMEOUT_SECONDS,
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
from .modeling import model_native_sample_rate
from .notifications import RuntimeUserNotifier

_CALLBACK_RUNTIME_ISSUE_LABELS: dict[str, str] = {
    "input_underflow": "capture underflow",
    "input_overflow": "capture overflow",
    "output_underflow": "playback underflow",
    "output_overflow": "playback overflow",
}


def _eprint(*args: object) -> None:
    print(*args, file=sys.stderr)


def _scan_device_probes(
    sample_rate: int, *, hostapi_filter: str = "all"
) -> ProbeSnapshot:
    inventory = query_audio_inventory()
    input_probes, output_probes = build_device_probes(
        inventory=inventory,
        hostapi_filter=hostapi_filter,
        samplerate=sample_rate,
    )
    return ProbeSnapshot(
        inventory=inventory,
        input_probes=input_probes,
        output_probes=output_probes,
    )


def _list_devices_handoff(next_step: str) -> str:
    return f"Run `python mic_bridge.py devices`, then {next_step}"


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
            _list_devices_handoff(f"retry with a fresh --{role} ID."),
        ]
        raise ValueError("\n".join(lines)) from exc


def _hostapis_for(
    probes: list[DeviceProbe],
    predicate: Callable[[DeviceProbe], bool],
) -> list[str]:
    return list(
        dict.fromkeys(probe.hostapi_name for probe in probes if predicate(probe))
    )


def _candidate_input_hostapis(input_probes: list[DeviceProbe]) -> list[str]:
    return _hostapis_for(input_probes, is_viable_auto_input_candidate)


def _bridge_output_hostapis(output_probes: list[DeviceProbe]) -> list[str]:
    return _hostapis_for(output_probes, is_likely_virtual_bridge_output)


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
            "No usable microphone input on the output's host API.\n"
            f"Output host API: {output_hostapi_name}\n"
            f"Inputs seen on: {', '.join(visible_input_hostapis)}\n"
            + _list_devices_handoff("pin --input and --output.")
        )

    return (
        f"No usable microphone input opened at {sample_rate} Hz.\n"
        + _list_devices_handoff("pin --input and --output.")
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
            "No bridge output on the input's host API.\n"
            f"Input host API: {input_hostapi_name}\n"
            f"Bridge outputs seen on: {', '.join(visible_bridge_hostapis)}\n"
            + _list_devices_handoff("pin matching IDs.")
        )

    system = platform.system()

    if system == "Windows":
        return (
            "No bridge output found. Auto mode only picks VB-CABLE-style playback endpoints, not speakers/headphones.\n"
            + _list_devices_handoff("pin --output, or pin both IDs.")
        )

    if system == "Linux":
        extra_note = f"{virtual_mic_note}\n" if virtual_mic_note is not None else ""

        return (
            "No bridge output found. Auto mode only picks virtual mic targets, not speakers/headphones or monitor/source endpoints.\n"
            f"{extra_note}"
            "Run `python mic_bridge.py devices` or create a reusable target:\n"
            f"  {linux_virtual_mic_command()}"
        )

    return "No bridge output found.\n" + _list_devices_handoff(
        "pin --output, or pin both IDs."
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
        "No microphone and bridge output matched on the same host API.\n"
        f"Inputs: {', '.join(visible_input_hostapis)}\n"
        f"Bridge outputs: {', '.join(visible_bridge_hostapis)}\n"
        + _list_devices_handoff("pin matching IDs.")
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
        f"Removed temporary {LINUX_VIRTUAL_MIC_DESCRIPTION}.",
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

    print(f"Model '{model_name}' is missing; downloading once...")

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

    print("Download finished.")


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

    hostapi_filter = "all"
    if requested_input_id is not None or requested_output_id is not None:
        role = "input" if requested_input_id is not None else "output"
        device_id = (
            requested_input_id
            if requested_input_id is not None
            else requested_output_id
        )
        inventory_for_filter = query_audio_inventory()
        info_for_filter = _get_current_run_device_info(
            inventory=inventory_for_filter,
            device_id=int(device_id),
            role=role,
        )
        key = hostapi_filter_key_for_name(
            hostapi_name=hostapi_name_for_device(
                info_for_filter, inventory_for_filter.hostapi_names
            )
        )
        if key is not None:
            hostapi_filter = key

    snapshot = _scan_device_probes(sample_rate, hostapi_filter=hostapi_filter)
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

        print(f"Setting up {LINUX_VIRTUAL_MIC_DESCRIPTION}...")
        status, detail = _try_create_linux_virtual_mic_target(linux_virtual_mic_lease)
        if status == "failed":
            virtual_mic_note = f"Automatic Linux target creation failed for '{LINUX_VIRTUAL_MIC_DESCRIPTION}': {detail or 'unknown `pactl` error.'}"
            return

        deadline = time.monotonic() + LINUX_VIRTUAL_MIC_DISCOVERY_TIMEOUT_SECONDS
        while True:
            snapshot = _scan_device_probes(
                sample_rate,
                hostapi_filter=hostapi_filter,
            )
            if visible_bridge_output_exists():
                break
            if time.monotonic() >= deadline:
                break
            time.sleep(LINUX_VIRTUAL_MIC_DISCOVERY_POLL_SECONDS)

        if visible_bridge_output_exists():
            if status == "created":
                linux_virtual_mic_was_auto_created = True
                print(f"Ready: temporary {LINUX_VIRTUAL_MIC_DESCRIPTION}.")
            else:
                print(f"Using existing {LINUX_VIRTUAL_MIC_DESCRIPTION}.")
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

    sample_rate = model_native_sample_rate(args.model)

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
        device_info=input_info,
        dtype="float32",
    )
    output_channels = negotiate_stream_channels(
        device_id=selection.output_device_id,
        direction="output",
        samplerate=sample_rate,
        device_info=output_info,
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

    block_line = f"{config.block_size} frames / {config.block_ms:.3f} ms"
    if config.block_policy == "auto-linux-jack":
        block_line += " (JACK)"
    elif config.block_policy == "explicit":
        block_line += f" (requested {config.requested_block_ms:.3f} ms)"

    print()
    print("Mic bridge")
    print(f"  model  {config.model_name} @ {config.sample_rate} Hz")
    print(
        f"  audio  {config.input_hostapi_name}, {block_line}, latency {config.latency}"
    )
    print(
        "  in     "
        f"{config.input_device_name} "
        f"(id {config.input_device_id}, {config.input_channels}ch, "
        f"{'auto' if config.input_was_auto_selected else 'manual'})"
    )
    print(
        "  out    "
        f"{config.output_device_name} "
        f"(id {config.output_device_id}, {config.output_channels}ch, "
        f"{'auto' if config.output_was_auto_selected else 'manual'})"
    )

    if config.linux_virtual_mic_was_auto_created:
        print(f"  target temporary {LINUX_VIRTUAL_MIC_DESCRIPTION}; removed on exit")

    if system == "Windows":
        print("  app    select VB-CABLE 'CABLE Output'")

    elif system == "Linux":
        print(f"  app    select {LINUX_VIRTUAL_MIC_DESCRIPTION} or its monitor/source")

    warnings: list[str] = []
    if should_print_generic_nonbridge_warning:
        if system == "Windows":
            warnings.append("output does not look like VB-CABLE 'CABLE Input'")
        elif system == "Linux":
            warnings.append("output does not look like a virtual mic target")
        else:
            warnings.append("output does not look like a bridge target")

    if "vb-cable-recording" in input_tags or "vb-cable-playback" in input_tags:
        warnings.append("input looks like a VB-CABLE endpoint")
    if "vb-cable-recording" in output_tags:
        warnings.append("output looks like a recording endpoint")
    if "monitor" in input_tags:
        warnings.append("input looks like a monitor/source")
    if system == "Linux" and "monitor" in output_tags:
        warnings.append("output looks like a monitor/source")
    if system == "Windows" and input_hostapi_key != "wasapi":
        warnings.append("WASAPI is usually best on Windows")

    if warnings:
        print("  warn")
        for line in warnings:
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
    count = status_counter.runtime_issue_count

    if alert_key == "first-runtime-issue":
        notifier.attention(
            title="Audio glitch detected",
            body=(
                f"{count} callback warning(s). If audio breaks up, increase --block-ms."
            ),
            urgency="normal",
        )
        return

    if alert_key == "repeated-runtime-issues":
        notifier.attention(
            title="Audio glitches continue",
            body=f"{count} callback warnings so far. Try a larger --block-ms.",
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
        body=f"{summary} See the terminal for the next step.",
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

    print("Loading model...")
    model_start = time.perf_counter()
    enhancer = _build_stream_enhancer(
        dpdfnet_mod,
        model_name=config.model_name,
        verbose=bool(args.verbose),
    )
    model_elapsed = time.perf_counter() - model_start
    print(f"Model ready ({model_elapsed:.2f}s).")

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

    print("Starting audio...")

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
            print("Streaming. Ctrl+C stops.")
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
        print("Stopping...")
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
            lines.append(_list_devices_handoff("retry or use a larger --block-ms."))
            raise RuntimeError("\n".join(lines)) from fatal_callback_error[0]

        detail = _first_line(str(stream_error)) or str(stream_error)
        if streaming_started:
            _notify_unexpected_stream_stop(
                notifier=notifier,
                detail=detail,
                callback_failed=False,
            )

        lines = ["Failed to open or run the audio stream.", f"Details: {detail}"]
        lines.append(_list_devices_handoff("retry with fresh IDs if needed."))
        raise RuntimeError("\n".join(lines)) from stream_error

    nonzero_status_lines = status_counter.nonzero_lines()
    print()
    if nonzero_status_lines:
        compact = ", ".join(
            line.strip().removeprefix("- ").replace(": ", "=")
            for line in nonzero_status_lines
        )
        print(f"Audio warnings: {compact}")
    else:
        print("Stopped cleanly.")
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
