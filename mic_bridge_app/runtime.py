"""
Implementation of the `run` command.

This module owns:
- resolving explicit and auto-selected devices into a concrete DeviceSelection
  and RunConfig
- smooth handoffs when auto mode cannot choose safely
- optional Linux virtual mic creation when output auto-selection needs it
- optional one-shot model download when the chosen model is missing locally
- printing the run summary
- constructing the StreamEnhancer
- opening the duplex sounddevice stream
- the real-time callback
- shutdown reporting

Keep heavy architectural rationale in `ARCHITECTURE.md`. Keep parser setup in
`cli.py`. Keep host-API and probing policy in `audio_system.py`.

Refactor guardrail:
The callback path should stay direct and easy to audit. Avoid adding small
layers that obscure audio flow unless they clearly improve correctness.
"""

from __future__ import annotations

import argparse
import platform
import shlex
import shutil
import subprocess
import sys
import time

import numpy as np

from .audio_system import (
    build_device_probes,
    choose_best_auto_input,
    choose_best_auto_output,
    format_jack_block_ms_hints,
    get_device_info,
    get_hostapi_names,
    has_bridge_output_tags,
    hostapi_filter_key_for_name,
    hostapi_name_for_device,
    infer_device_tags,
    is_likely_virtual_bridge_output,
    is_power_of_two,
    is_viable_auto_input_candidate,
    jack_block_ms_for_frames,
    linux_virtual_mic_command,
    negotiate_stream_channels,
    recommended_linux_jack_block_frames,
    resolve_effective_block_size,
    same_hostapi_guidance,
    suggest_run_pair,
)
from .constants import DEFAULT_BLOCK_MS, LINUX_VIRTUAL_MIC_DESCRIPTION
from .dependencies import require_dpdfnet, require_sounddevice
from .domain import (
    AudioFifo,
    CallbackStatusCounter,
    DeviceProbe,
    DeviceSelection,
    RunConfig,
)
from .modeling import resolve_stream_sample_rate


def _eprint(*args: object) -> None:
    print(*args, file=sys.stderr)


def _scan_all_device_probes(
    sample_rate: int,
) -> tuple[list[DeviceProbe], list[DeviceProbe], list[str]]:
    sd_mod = require_sounddevice()
    devices = sd_mod.query_devices()
    hostapi_names = get_hostapi_names(sd_mod)
    input_probes, output_probes = build_device_probes(
        devices=devices,
        hostapi_names=hostapi_names,
        hostapi_filter="all",
        samplerate=sample_rate,
    )
    return input_probes, output_probes, hostapi_names


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
            "Could not auto-select a microphone input that matches the chosen output host API.\n\n"
            f"Chosen output host API: {output_hostapi_name}\n"
            f"Visible microphone-like inputs use: {', '.join(visible_input_hostapis)}\n"
            "Next step:\n"
            "  python mic_bridge.py list-devices\n"
            "Then re-run with --input <id> and --output <id>."
        )

    return (
        "Could not auto-select a microphone input.\n\n"
        f"No likely microphone input opened at {sample_rate} Hz.\n"
        "Next step:\n"
        "  python mic_bridge.py list-devices\n"
        "Then re-run with --input <id> and --output <id>."
    )


def _build_no_auto_output_handoff(
    *,
    input_hostapi_name: str | None,
    output_probes: list[DeviceProbe],
    create_attempted: bool,
    create_error: str | None,
) -> str:
    visible_bridge_hostapis = _bridge_output_hostapis(output_probes)

    if input_hostapi_name is not None and visible_bridge_hostapis:
        return (
            "Could not auto-select a bridge output that matches the chosen input host API.\n\n"
            f"Chosen input host API: {input_hostapi_name}\n"
            f"Visible bridge outputs use: {', '.join(visible_bridge_hostapis)}\n"
            "Next step:\n"
            "  python mic_bridge.py list-devices\n"
            "Then re-run with matching IDs."
        )

    system = platform.system()

    if system == "Windows":
        return (
            "Could not auto-select a bridge output.\n\n"
            "Auto mode only picks a likely virtual bridge target such as VB-CABLE 'CABLE Input', never speakers/headphones or the recording-side endpoint.\n"
            "Install and configure VB-CABLE first, then run:\n"
            "  python mic_bridge.py list-devices\n"
            "and re-run with --output <id> or with both --input <id> and --output <id>."
        )

    if system == "Linux":
        creation_line = ""
        detail = _first_line(create_error)
        if create_attempted and detail is not None:
            creation_line = f"Automatic creation attempt: {detail}\n"
        elif create_attempted:
            creation_line = (
                f"I tried to create '{LINUX_VIRTUAL_MIC_DESCRIPTION}' automatically, "
                "but it did not become usable.\n"
            )

        return (
            "Could not auto-select a bridge output.\n\n"
            "Auto mode only picks a likely virtual mic target, never speakers/headphones or monitor/source endpoints.\n"
            f"{creation_line}"
            "Next step:\n"
            "  python mic_bridge.py list-devices\n"
            "If needed, create it manually with:\n"
            f"  {linux_virtual_mic_command()}"
        )

    return (
        "Could not auto-select a bridge output.\n\n"
        "Auto mode only picks likely bridge-style outputs, not ordinary speakers/headphones or monitor/source endpoints.\n"
        "Next step:\n"
        "  python mic_bridge.py list-devices\n"
        "Then re-run with --output <id> or with both --input <id> and --output <id>."
    )


def _build_no_auto_pair_handoff(
    *,
    sample_rate: int,
    input_probes: list[DeviceProbe],
    output_probes: list[DeviceProbe],
    create_attempted: bool,
    create_error: str | None,
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
            create_attempted=create_attempted,
            create_error=create_error,
        )

    return (
        "Could not auto-match a microphone input and bridge output on the same host API.\n\n"
        f"Visible microphone-like inputs use: {', '.join(visible_input_hostapis)}\n"
        f"Visible bridge outputs use: {', '.join(visible_bridge_hostapis)}\n"
        "Next step:\n"
        "  python mic_bridge.py list-devices\n"
        "Then re-run with matching IDs."
    )


def _try_create_linux_virtual_mic_target() -> tuple[bool, str | None]:
    command = shlex.split(linux_virtual_mic_command())

    if shutil.which("pactl") is None:
        return False, "`pactl` was not found."

    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        return False, str(exc)

    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        if not detail:
            detail = f"`pactl` exited with status {completed.returncode}."
        return False, detail

    time.sleep(0.35)
    return True, None


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

    print(f"Model '{model_name}' is not available locally, downloading it once...")

    try:
        completed = subprocess.run(command, check=False)
    except OSError as exc:
        raise RuntimeError(
            "Could not start the DPDFNet downloader.\n"
            f"Run this once manually, then try again:\n"
            f"  {display_command}"
        ) from exc

    if completed.returncode != 0:
        raise RuntimeError(
            "Automatic model download failed.\n"
            f"Run this once manually, then try again:\n"
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
            f"Model '{model_name}' could not be loaded after the automatic download: {exc}"
        ) from exc


def _resolve_run_device_ids(
    *,
    sample_rate: int,
    requested_input_id: int | None,
    requested_output_id: int | None,
) -> DeviceSelection:
    """
    Resolve explicit and auto-selected device IDs.

    Auto-selection policy is intentionally asymmetric:
    - inputs may pick a broad physical-looking mic candidate
    - outputs remain strict and must look like bridge targets
    - on Linux, omitting --output allows one automatic pactl creation attempt
      if no likely bridge-style output is visible at all
    """
    if requested_input_id is not None and requested_output_id is not None:
        return DeviceSelection(
            input_device_id=requested_input_id,
            output_device_id=requested_output_id,
            input_was_auto_selected=False,
            output_was_auto_selected=False,
            linux_virtual_mic_was_created=False,
        )

    input_probes, output_probes, hostapi_names = _scan_all_device_probes(sample_rate)
    linux_virtual_mic_was_created = False
    create_attempted = False
    create_error: str | None = None

    def maybe_create_linux_virtual_mic() -> None:
        nonlocal input_probes
        nonlocal output_probes
        nonlocal hostapi_names
        nonlocal linux_virtual_mic_was_created
        nonlocal create_attempted
        nonlocal create_error

        if platform.system() != "Linux" or requested_output_id is not None:
            return

        # One-shot graph mutation is reserved for the case where no likely
        # bridge output is visible at all. If a bridge target already exists but
        # only on a mismatched host API, Version 0 hands off instead of trying
        # to mutate the graph further.
        if any(is_likely_virtual_bridge_output(probe) for probe in output_probes):
            return

        create_attempted = True
        print(
            f"No likely virtual mic target is visible, trying to create '{LINUX_VIRTUAL_MIC_DESCRIPTION}' automatically..."
        )
        created, error = _try_create_linux_virtual_mic_target()
        if not created:
            create_error = error
            return

        linux_virtual_mic_was_created = True
        input_probes, output_probes, hostapi_names = _scan_all_device_probes(
            sample_rate
        )

        if any(is_likely_virtual_bridge_output(probe) for probe in output_probes):
            print(
                f"Created Linux virtual mic target '{LINUX_VIRTUAL_MIC_DESCRIPTION}'."
            )
        else:
            create_error = "The target did not become visible through PortAudio."

    if requested_input_id is not None and requested_output_id is None:
        input_info = get_device_info(requested_input_id)
        input_hostapi = hostapi_name_for_device(input_info, hostapi_names)

        output_probe = choose_best_auto_output(
            output_probes,
            hostapi_name=input_hostapi,
        )
        if output_probe is not None:
            return DeviceSelection(
                input_device_id=requested_input_id,
                output_device_id=output_probe.device_id,
                input_was_auto_selected=False,
                output_was_auto_selected=True,
                linux_virtual_mic_was_created=linux_virtual_mic_was_created,
            )

        if not any(is_likely_virtual_bridge_output(probe) for probe in output_probes):
            maybe_create_linux_virtual_mic()
            input_hostapi = hostapi_name_for_device(input_info, hostapi_names)
            output_probe = choose_best_auto_output(
                output_probes,
                hostapi_name=input_hostapi,
            )
            if output_probe is not None:
                return DeviceSelection(
                    input_device_id=requested_input_id,
                    output_device_id=output_probe.device_id,
                    input_was_auto_selected=False,
                    output_was_auto_selected=True,
                    linux_virtual_mic_was_created=linux_virtual_mic_was_created,
                )

        raise ValueError(
            _build_no_auto_output_handoff(
                input_hostapi_name=input_hostapi,
                output_probes=output_probes,
                create_attempted=create_attempted,
                create_error=create_error,
            )
        )

    if requested_input_id is None and requested_output_id is not None:
        output_info = get_device_info(requested_output_id)
        output_hostapi = hostapi_name_for_device(output_info, hostapi_names)

        input_probe = choose_best_auto_input(
            input_probes,
            hostapi_name=output_hostapi,
        )
        if input_probe is not None:
            return DeviceSelection(
                input_device_id=input_probe.device_id,
                output_device_id=requested_output_id,
                input_was_auto_selected=True,
                output_was_auto_selected=False,
                linux_virtual_mic_was_created=linux_virtual_mic_was_created,
            )

        raise ValueError(
            _build_no_auto_input_handoff(
                sample_rate=sample_rate,
                output_hostapi_name=output_hostapi,
                input_probes=input_probes,
            )
        )

    suggested_pair = suggest_run_pair(input_probes, output_probes)
    if suggested_pair is not None:
        input_probe, output_probe = suggested_pair
        return DeviceSelection(
            input_device_id=input_probe.device_id,
            output_device_id=output_probe.device_id,
            input_was_auto_selected=True,
            output_was_auto_selected=True,
            linux_virtual_mic_was_created=linux_virtual_mic_was_created,
        )

    if not any(is_likely_virtual_bridge_output(probe) for probe in output_probes):
        maybe_create_linux_virtual_mic()
        suggested_pair = suggest_run_pair(input_probes, output_probes)
        if suggested_pair is not None:
            input_probe, output_probe = suggested_pair
            return DeviceSelection(
                input_device_id=input_probe.device_id,
                output_device_id=output_probe.device_id,
                input_was_auto_selected=True,
                output_was_auto_selected=True,
                linux_virtual_mic_was_created=linux_virtual_mic_was_created,
            )

    raise ValueError(
        _build_no_auto_pair_handoff(
            sample_rate=sample_rate,
            input_probes=input_probes,
            output_probes=output_probes,
            create_attempted=create_attempted,
            create_error=create_error,
        )
    )


def _build_run_config(args: argparse.Namespace) -> RunConfig:
    """
    Resolve CLI args into a concrete run configuration.

    Version 0 rules enforced here:
    - stream sample rate must equal model native sample rate
    - input and output devices must belong to the same host API
    - channel counts are negotiated conservatively, but input/output use
      different preference orders for practical reasons
    - Linux/JACK block sizing auto-adjusts when `--block-ms` is omitted
    - auto mode may resolve omitted devices, but output auto-selection remains
      deliberately stricter than input auto-selection
    """
    sd_mod = require_sounddevice()

    sample_rate = resolve_stream_sample_rate(args.model, args.samplerate, context="run")

    requested_input_id = None if args.input is None else int(args.input)
    requested_output_id = None if args.output is None else int(args.output)

    selection = _resolve_run_device_ids(
        sample_rate=sample_rate,
        requested_input_id=requested_input_id,
        requested_output_id=requested_output_id,
    )

    hostapi_names = get_hostapi_names(sd_mod)

    input_info = get_device_info(selection.input_device_id)
    output_info = get_device_info(selection.output_device_id)

    if int(input_info["max_input_channels"]) < 1:
        raise ValueError(
            f"Selected input device {selection.input_device_id} exposes no input channels: {input_info['name']}"
        )

    if int(output_info["max_output_channels"]) < 1:
        raise ValueError(
            f"Selected output device {selection.output_device_id} exposes no output channels: {output_info['name']}"
        )

    input_hostapi = hostapi_name_for_device(input_info, hostapi_names)
    output_hostapi = hostapi_name_for_device(output_info, hostapi_names)

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
        linux_virtual_mic_was_created=selection.linux_virtual_mic_was_created,
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

    if config.input_was_auto_selected and config.output_was_auto_selected:
        print("  - Input and output were auto-selected.")
    elif config.input_was_auto_selected:
        print("  - Input was auto-selected.")
    elif config.output_was_auto_selected:
        print("  - Output was auto-selected.")

    if config.linux_virtual_mic_was_created:
        print(
            f"  - The Linux virtual mic target '{LINUX_VIRTUAL_MIC_DESCRIPTION}' was created automatically for this run."
        )

    print(
        "  - First enhanced output arrives after roughly one full model window (~20 ms)."
    )
    if config.input_channels > 1:
        print("  - Opened input will be downmixed to mono before enhancement.")
    if config.output_channels > 1:
        print("  - Enhanced mono will be duplicated across all opened output channels.")
    else:
        print("  - Enhanced mono will be written to a single opened output channel.")

    print("  - This script writes enhanced audio to your chosen output device.")

    if system == "Windows":
        print(
            "  - If that output device is VB-CABLE 'CABLE Input', then your apps should use"
        )
        print("    VB-CABLE 'CABLE Output' as the microphone.")
        if input_hostapi_key != "wasapi":
            print(
                "  - Windows note: WASAPI is the recommended host API starting point."
            )

    elif system == "Linux":
        print(
            "  - On Linux/PipeWire, if that output device is your virtual mic target,"
        )
        print("    apps should first try selecting it directly as the microphone.")
        print(
            "  - If a given app does not list it directly, try the matching monitor/source."
        )
        print(
            "  - Linux note: sounddevice reaches PipeWire through PortAudio host APIs,"
        )
        print("    usually a real PipeWire backend if available, otherwise PulseAudio,")
        print("    JACK, or ALSA.")
        if input_hostapi_key not in {"pipewire", "pulse", "jack", "alsa"}:
            print(
                "  - Linux note: a PipeWire-compatible PortAudio host API is recommended if available."
            )

        if "dpdfnet" in output_tags and config.output_channels == 1:
            print(
                "  - Linux note: a mono virtual mic target is fine here and avoids left/right routing surprises."
            )

        if input_hostapi_key == "jack":
            auto_frames = recommended_linux_jack_block_frames(config.sample_rate)
            auto_ms = jack_block_ms_for_frames(auto_frames, config.sample_rate)
            if config.block_policy == "auto-linux-jack":
                print(
                    "  - Linux/JACK note: because --block-ms was omitted, the script auto-selected"
                )
                print(
                    f"    {config.block_ms:.3f} ms ({config.block_size} frames), an exact JACK-friendly block size."
                )
            elif not is_power_of_two(config.block_size):
                print(
                    "  - Linux/JACK note: this block size is not an exact power-of-two frame count."
                )
                print(f"    If you hear stutter/crackle, try --block-ms {auto_ms:.3f}")
                print(
                    f"    or another exact JACK-friendly value such as {format_jack_block_ms_hints(config.sample_rate)} ms."
                )

    if (
        config.block_policy == "default-10ms"
        and abs(config.block_ms - DEFAULT_BLOCK_MS) > 1e-6
    ):
        print(
            "  - Note: block size is rounded to whole frames, so the effective block duration may differ slightly."
        )

    if should_print_generic_nonbridge_warning:
        print(
            "  - Warning: selected output does not look like a dedicated bridge target."
        )
        print("    Enhanced audio will be sent there directly.")
        if system == "Windows":
            print(
                "    For the usual VB-CABLE flow, --output should be the playback endpoint,"
            )
            print("    usually named 'CABLE Input'.")
        elif system == "Linux":
            print("    For the usual virtual microphone flow, --output should be")
            print("    a virtual mic sink target rather than speakers/headphones.")

    if "vb-cable-recording" in input_tags or "vb-cable-playback" in input_tags:
        print(
            "  - Warning: selected input looks like a VB-CABLE endpoint, not a physical microphone."
        )
    if "vb-cable-recording" in output_tags:
        print(
            "  - Warning: selected output looks like a recording endpoint. For VB-CABLE,"
            " you usually want 'CABLE Input' as --output."
        )
    if "monitor" in input_tags:
        print(
            "  - Warning: selected input looks like a monitor/source, not a physical microphone."
        )
    if system == "Linux" and "monitor" in output_tags:
        print(
            "  - Warning: selected output looks like a monitor/source, not a sink playback device."
        )
    print()


def run_command(args: argparse.Namespace) -> int:
    sd_mod = require_sounddevice()
    dpdfnet_mod = require_dpdfnet()

    if platform.system() not in ("Windows", "Linux"):
        _eprint(
            "[note] This script is primarily intended for Windows 10/11 and Linux PipeWire systems. It may still work elsewhere, but that is not the supported target."
        )

    config = _build_run_config(args)
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

    def audio_callback(indata, outdata, frames, time_info, status) -> None:
        """
        Real-time audio callback.

        Key design constraints:
        - no printing here
        - no model loading here
        - no disk I/O here
        - no device queries here
        - keep logic straightforward

        Safe flow:
        1) downmix input to mono
        2) feed one chunk into StreamEnhancer
        3) push returned samples into FIFO
        4) pop exactly `frames` samples for playback
        5) duplicate mono across all output channels if needed
        """
        del time_info  # unused in Version 0

        try:
            status_counter.observe(status)

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
            print("Streaming.")
            print("Press Ctrl+C to stop.")
            print()
            while True:
                time.sleep(0.5)

    except KeyboardInterrupt:
        print()
        print("Stopping bridge...")
    except Exception as exc:
        if fatal_callback_error:
            raise RuntimeError(
                "The audio callback aborted due to an internal error."
            ) from fatal_callback_error[0]
        raise RuntimeError(f"Failed to open or run the audio stream: {exc}") from exc

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
