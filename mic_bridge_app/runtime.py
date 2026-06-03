"""Implementation of the `run` command."""

from __future__ import annotations

import argparse
import math
import platform
import shlex
import sys
import time
from typing import Callable

import numpy as np

from .audio_system import (build_device_probes, choose_best_auto_input,
                           choose_best_auto_output, device_info_from_inventory,
                           has_bridge_output_tags, hostapi_filter_key_for_name,
                           hostapi_name_for_device, infer_device_tags,
                           is_likely_virtual_bridge_output,
                           is_viable_auto_input_candidate,
                           linux_virtual_mic_command,
                           negotiate_stream_channels, next_power_of_two,
                           query_audio_inventory, resolve_effective_block_size,
                           same_hostapi_guidance, suggest_run_pair)
from .constants import DEFAULT_LATENCY, DEFAULT_MODEL
from .dependencies import require_dpdfnet, require_sounddevice
from .domain import (AudioFifo, AudioInventory, CallbackStatusCounter,
                     DeviceProbe, ProbeSnapshot, RunConfig)
from .modeling import model_native_sample_rate


def _eprint(*args: object) -> None:
    print(*args, file=sys.stderr)


def _scan_device_probes(
    sample_rate: int,
    hostapi_filter: str,
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
        f"No physical-looking microphone input opened at {sample_rate} Hz. Auto mode only picks devices that look like a mic, headset, Bluetooth mic, or audio interface.\n"
        + _list_devices_handoff("pin --input and --output.")
    )


def _build_no_auto_output_handoff(
    *,
    input_hostapi_name: str | None,
    output_probes: list[DeviceProbe],
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
        return (
            "No bridge output found. Auto mode only picks virtual mic targets, not speakers/headphones or monitor/source endpoints.\n"
            "Create a virtual mic target, then rerun `python mic_bridge.py devices`:\n"
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
        )

    return (
        "No microphone and bridge output matched on the same host API.\n"
        f"Inputs: {', '.join(visible_input_hostapis)}\n"
        f"Bridge outputs: {', '.join(visible_bridge_hostapis)}\n"
        + _list_devices_handoff("pin matching IDs.")
    )


def _build_stream_enhancer(dpdfnet_mod, *, model_name: str, verbose: bool):
    try:
        return dpdfnet_mod.StreamEnhancer(model=model_name, verbose=verbose)
    except Exception as exc:
        detail = _first_line(str(exc)) or type(exc).__name__
        raise RuntimeError(
            f"Could not load DPDFNet model '{model_name}'.\n"
            "If the model has not been downloaded yet, run:\n"
            f"  dpdfnet download {model_name}\n"
            f"Details: {detail}"
        ) from exc


def _snapshot_for_selection(
    *,
    sample_rate: int,
    requested_input_id: int | None,
    requested_output_id: int | None,
) -> ProbeSnapshot:
    hostapi_filter = "all"

    if (requested_input_id is None) != (requested_output_id is None):
        inventory = query_audio_inventory()
        role = "input" if requested_input_id is not None else "output"
        device_id = (
            requested_input_id
            if requested_input_id is not None
            else requested_output_id
        )
        assert device_id is not None

        pinned_info = _get_current_run_device_info(
            inventory=inventory,
            device_id=device_id,
            role=role,
        )
        hostapi_key = hostapi_filter_key_for_name(
            hostapi_name_for_device(pinned_info, inventory.hostapi_names)
        )
        if hostapi_key is not None:
            hostapi_filter = hostapi_key

    return _scan_device_probes(sample_rate, hostapi_filter=hostapi_filter)


def _resolve_run_device_selection(
    *,
    sample_rate: int,
    requested_input_id: int | None,
    requested_output_id: int | None,
) -> tuple[int, int, bool, bool, AudioInventory]:
    """Return input id, output id, input-auto flag, output-auto flag, inventory."""
    if requested_input_id is not None and requested_output_id is not None:
        inventory = query_audio_inventory()
        _get_current_run_device_info(
            inventory=inventory,
            device_id=requested_input_id,
            role="input",
        )
        _get_current_run_device_info(
            inventory=inventory,
            device_id=requested_output_id,
            role="output",
        )
        return requested_input_id, requested_output_id, False, False, inventory

    snapshot = _snapshot_for_selection(
        sample_rate=sample_rate,
        requested_input_id=requested_input_id,
        requested_output_id=requested_output_id,
    )

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
            return (
                requested_input_id,
                output_probe.device_id,
                False,
                True,
                snapshot.inventory,
            )

        raise ValueError(
            _build_no_auto_output_handoff(
                input_hostapi_name=input_hostapi,
                output_probes=snapshot.output_probes,
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
            return (
                input_probe.device_id,
                requested_output_id,
                True,
                False,
                snapshot.inventory,
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
        return (
            input_probe.device_id,
            output_probe.device_id,
            True,
            True,
            snapshot.inventory,
        )

    raise ValueError(
        _build_no_auto_pair_handoff(
            sample_rate=sample_rate,
            input_probes=snapshot.input_probes,
            output_probes=snapshot.output_probes,
        )
    )


def _build_run_config(
    args: argparse.Namespace,
) -> RunConfig:
    """
    Resolve CLI args into a concrete run configuration.

    This enforces sample-rate, host-API, channel, and block-size policy.
    """

    sample_rate = model_native_sample_rate(args.model)

    requested_input_id = args.input
    requested_output_id = args.output

    (
        input_device_id,
        output_device_id,
        input_was_auto_selected,
        output_was_auto_selected,
        inventory,
    ) = _resolve_run_device_selection(
        sample_rate=sample_rate,
        requested_input_id=requested_input_id,
        requested_output_id=requested_output_id,
    )

    input_info = _get_current_run_device_info(
        inventory=inventory,
        device_id=input_device_id,
        role="input",
    )
    output_info = _get_current_run_device_info(
        inventory=inventory,
        device_id=output_device_id,
        role="output",
    )

    input_hostapi = hostapi_name_for_device(input_info, inventory.hostapi_names)
    output_hostapi = hostapi_name_for_device(output_info, inventory.hostapi_names)

    if int(input_info["hostapi"]) != int(output_info["hostapi"]):
        raise ValueError(
            "Input and output devices must use the same host API in Version 0.\n"
            f"Input : id={input_device_id}, hostapi={input_hostapi}, name={input_info['name']}\n"
            f"Output: id={output_device_id}, hostapi={output_hostapi}, name={output_info['name']}\n"
            f"{same_hostapi_guidance()}"
        )

    requested_block_ms = args.block_ms
    requested_block_frames = args.block_frames
    block_size, effective_block_ms, block_policy = resolve_effective_block_size(
        sample_rate=sample_rate,
        requested_block_ms=requested_block_ms,
        requested_block_frames=requested_block_frames,
        hostapi_name=input_hostapi,
    )

    input_channels = negotiate_stream_channels(
        device_id=input_device_id,
        direction="input",
        samplerate=sample_rate,
        device_info=input_info,
        dtype="float32",
    )
    output_channels = negotiate_stream_channels(
        device_id=output_device_id,
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
        requested_block_frames=requested_block_frames,
        block_policy=block_policy,
        block_size=block_size,
        input_device_id=input_device_id,
        input_device_name=input_name,
        input_hostapi_name=input_hostapi,
        input_channels=input_channels,
        input_tags=infer_device_tags(input_name),
        input_was_auto_selected=input_was_auto_selected,
        output_device_id=output_device_id,
        output_device_name=output_name,
        output_hostapi_name=output_hostapi,
        output_channels=output_channels,
        output_tags=infer_device_tags(output_name),
        output_was_auto_selected=output_was_auto_selected,
        latency=args.latency,
    )


def _print_run_summary(config: RunConfig) -> None:
    system = platform.system()
    input_tags = set(config.input_tags)
    output_tags = set(config.output_tags)

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
    elif config.block_policy == "explicit-frames":
        block_line += f" (requested {config.requested_block_frames} frames)"

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

    if system == "Windows":
        print("  app    select VB-CABLE 'CABLE Output'")

    elif system == "Linux":
        print(f"  app    select '{config.output_device_name}' as the microphone")

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
    if (
        system == "Windows"
        and hostapi_filter_key_for_name(config.input_hostapi_name) != "wasapi"
    ):
        warnings.append("WASAPI is usually best on Windows")

    if warnings:
        print("  warn")
        for line in warnings:
            print(f"  - {line}")
    print()


def _next_block_setting(config: RunConfig) -> tuple[str, str, str]:
    """
    Return (flag, value, label) for the next sensible restart.

    JACK users generally think in exact period sizes, so suggest frames there.
    Other backends get a rounded millisecond suggestion.
    """
    if hostapi_filter_key_for_name(config.input_hostapi_name) == "jack":
        next_frames = next_power_of_two(config.block_size + 1)
        next_ms = (float(next_frames) * 1000.0) / float(config.sample_rate)
        return (
            "--block-frames",
            str(next_frames),
            f"{next_frames} frames / {next_ms:.3f} ms",
        )

    next_ms = int(math.ceil(max(20.0, config.block_ms + 5.0, config.block_ms * 1.5)))
    next_frames = int(round(config.sample_rate * (next_ms / 1000.0)))
    return "--block-ms", str(next_ms), f"{next_frames} frames / {next_ms:.3f} ms"


def _restart_command_for_block(config: RunConfig) -> tuple[str, str]:
    flag, value, label = _next_block_setting(config)

    parts = ["python", "mic_bridge.py"]
    if config.model_name != DEFAULT_MODEL:
        parts.extend(["--model", config.model_name])

    parts.extend(["--input", str(config.input_device_id)])
    if not config.output_was_auto_selected:
        parts.extend(["--output", str(config.output_device_id)])

    if config.latency != DEFAULT_LATENCY:
        parts.extend(["--latency", config.latency])

    parts.extend([flag, value])
    return shlex.join(parts), label


def _print_block_tuning_next_step(config: RunConfig) -> None:
    command, label = _restart_command_for_block(config)
    print("If audio broke up, restart with:")
    print(f"  {command}")
    print(
        f"  next block {label}; current {config.block_size} frames / {config.block_ms:.3f} ms"
    )


def _run_command_impl(
    args: argparse.Namespace,
    *,
    sd_mod,
    dpdfnet_mod,
) -> int:
    if platform.system() not in ("Windows", "Linux"):
        _eprint(
            "[note] This script is primarily intended for Windows 10/11 and Linux PipeWire systems. It may still work elsewhere, but that is not the supported target."
        )

    config = _build_run_config(args)
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

    def audio_callback(indata, outdata, frames, _time_info, status) -> None:
        try:
            status_counter.observe(status)

            if indata.shape[1] == 1:
                mono_in = np.asarray(indata[:, 0], dtype=np.float32)
            else:
                mono_in = np.asarray(
                    np.mean(indata, axis=1, dtype=np.float32),
                    dtype=np.float32,
                )

            enhanced = enhancer.process(mono_in, sample_rate=config.sample_rate)
            output_fifo.push(enhanced)

            mono_out = output_fifo.pop(frames)

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
            print("Streaming. Ctrl+C stops.")
            print()

            while True:
                time.sleep(0.5)

    except KeyboardInterrupt:
        interrupted = True
    except Exception as exc:
        stream_error = exc

    if interrupted:
        print()
        print("Stopping...")
    elif stream_error is not None:
        if fatal_callback_error:
            lines = ["The audio callback aborted unexpectedly."]
            detail = _first_line(str(fatal_callback_error[0]))
            if detail is not None:
                lines.append(f"Details: {detail}")
            lines.append(
                _list_devices_handoff(
                    "retry, or restart with the block-size command printed above if timing warnings appeared."
                )
            )
            raise RuntimeError("\n".join(lines)) from fatal_callback_error[0]

        detail = _first_line(str(stream_error)) or str(stream_error)

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
        _print_block_tuning_next_step(config)
    else:
        print("Stopped cleanly.")
    print()

    return 0


def run_command(args: argparse.Namespace) -> int:
    sd_mod = require_sounddevice()
    dpdfnet_mod = require_dpdfnet()
    return _run_command_impl(
        args,
        sd_mod=sd_mod,
        dpdfnet_mod=dpdfnet_mod,
    )
