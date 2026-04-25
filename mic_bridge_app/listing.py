"""
Implementation of the `devices` command.

This module owns the fallback discovery workflow and the user-facing
explanation around automatic run behavior, probe results, and pinned commands.
"""

from __future__ import annotations

import argparse
import platform
import sys

from .audio_system import (
    build_device_probes,
    build_run_command_suggestion,
    format_jack_block_ms_hints,
    format_samplerate,
    format_tags,
    jack_block_ms_for_frames,
    linux_virtual_mic_command,
    query_audio_inventory,
    recommended_linux_jack_block_frames,
    resolve_list_hostapi_filter,
    suggest_run_pair,
)
from .constants import (
    DEFAULT_MODEL,
    HOSTAPI_FILTER_DISPLAY_NAMES,
    LINUX_VIRTUAL_MIC_DESCRIPTION,
)

from .domain import DeviceProbe
from .modeling import model_native_sample_rate


def _eprint(*args: object) -> None:
    print(*args, file=sys.stderr)


def _print_lines(lines: list[str]) -> None:
    for line in lines:
        print(line)


def _print_probe_section(
    *,
    title: str,
    probes: list[DeviceProbe],
    samplerate: int,
    verbose: bool,
) -> None:
    print(title)

    if not probes:
        print("  (none)")
        return

    for probe in probes:
        max_label = "max_in" if probe.direction == "input" else "max_out"
        open_label = "no" if probe.open_channels is None else f"{probe.open_channels}ch"
        print(
            f"  [{probe.device_id:>3}] "
            f"open@{samplerate}={open_label:<3}  "
            f"{max_label}={probe.max_channels:<2}  "
            f"default={format_samplerate(probe.default_samplerate):<6}  "
            f"{probe.hostapi_name}  "
            f"{probe.name}  "
            f"({format_tags(probe.tags)})"
        )
        if verbose and probe.open_channels is None and probe.probe_failures:
            for failure in probe.probe_failures:
                print(f"        {failure}")


def _print_platform_usage_notes(*, samplerate: int, hostapi_filter: str) -> None:
    system = platform.system()

    if system == "Windows":
        _print_lines(
            [
                "Windows notes:",
                "  - Start with: python mic_bridge.py run",
                f"  - open@{samplerate} shows whether PortAudio can open the device.",
                "  - Apps should use VB-CABLE 'CABLE Output' as the microphone.",
                "  - If IDs drift, rerun: python mic_bridge.py devices",
            ]
        )
        print()
        return

    if system == "Linux":
        lines = [
            "Linux notes:",
            "  - Start with: python mic_bridge.py run",
            "  - Keep input and output on the same host API.",
            "  - Auto output only picks virtual mic targets, never speakers/headphones or monitor/source endpoints.",
            f"  - run may create a temporary '{LINUX_VIRTUAL_MIC_DESCRIPTION}' target and remove it on exit.",
            "  - For a reusable target, create it yourself with:",
            f"      {linux_virtual_mic_command()}",
            f"  - open@{samplerate} shows whether PortAudio can open the device.",
            f"  - In apps, try '{LINUX_VIRTUAL_MIC_DESCRIPTION}' first, then its monitor/source if needed.",
        ]
        if hostapi_filter == "jack":
            auto_frames = recommended_linux_jack_block_frames(samplerate)
            auto_ms = jack_block_ms_for_frames(auto_frames, samplerate)
            lines.extend(
                [
                    "  - JACK note: omitting --block-ms uses an exact JACK-friendly block size.",
                    f"    Default: {auto_ms:.3f} ms ({auto_frames} frames) at {samplerate} Hz.",
                    f"    If you override it and hear glitches, try {format_jack_block_ms_hints(samplerate)} ms.",
                ]
            )
        lines.append("  - If IDs drift, rerun: python mic_bridge.py devices")
        _print_lines(lines)
        print()
        return

    _print_lines(
        [
            "Platform notes:",
            "  - Try: python mic_bridge.py run",
            f"  - Probe results show whether a device opens at {samplerate} Hz.",
            "  - If pinned IDs stop working after device churn, rerun devices.",
        ]
    )
    print()


def devices_command(args: argparse.Namespace) -> int:
    probe_sample_rate = model_native_sample_rate(args.model)

    if platform.system() not in ("Windows", "Linux"):
        _eprint(
            "[note] This script is primarily intended for Windows 10/11 and Linux PipeWire systems, though device listing may still work elsewhere."
        )

    inventory = query_audio_inventory()
    hostapi_filter = resolve_list_hostapi_filter(
        args.hostapi,
        args.all_apis,
        inventory.hostapi_names,
    )

    input_probes, output_probes = build_device_probes(
        inventory=inventory,
        hostapi_filter=hostapi_filter,
        samplerate=probe_sample_rate,
    )

    fast_path_command = (
        "python mic_bridge.py run"
        if args.model == DEFAULT_MODEL
        else f"python mic_bridge.py run --model {args.model}"
    )

    print()
    print(f"Devices for {args.model} @ {probe_sample_rate} Hz")
    print(f"Auto run : {fast_path_command}")
    print(f"Host API : {HOSTAPI_FILTER_DISPLAY_NAMES[hostapi_filter]}")
    if hostapi_filter != "all":
        print("Tip      : use --all-apis to show every PortAudio device.")
    print()

    _print_probe_section(
        title="Input candidates",
        probes=input_probes,
        samplerate=probe_sample_rate,
        verbose=bool(args.verbose),
    )
    print()
    _print_probe_section(
        title="Output candidates",
        probes=output_probes,
        samplerate=probe_sample_rate,
        verbose=bool(args.verbose),
    )
    print()

    if hostapi_filter != "all" and not input_probes and not output_probes:
        print(
            f"No devices matched the current host API filter '{hostapi_filter}'. Try --all-apis."
        )
        print()

    if hostapi_filter == "all" and not input_probes and not output_probes:
        print("No PortAudio devices are currently visible.")
        if platform.system() == "Windows":
            print(
                "Check Windows sound settings and confirm your audio devices, including VB-CABLE if you plan to use it, are installed beforehand."
            )
        elif platform.system() == "Linux":
            print(
                "Check that your audio session is running and that PortAudio can see the expected PipeWire, PulseAudio, JACK, or ALSA backend."
            )
        print()

    suggested_pair = suggest_run_pair(input_probes, output_probes)
    if suggested_pair is not None:
        input_probe, output_probe = suggested_pair
        print("Suggested run:")
        print(
            "  "
            + build_run_command_suggestion(
                input_id=input_probe.device_id,
                output_id=output_probe.device_id,
                model_name=args.model,
            )
        )
        if platform.system() == "Linux" and hostapi_filter == "jack":
            auto_frames = recommended_linux_jack_block_frames(probe_sample_rate)
            auto_ms = jack_block_ms_for_frames(auto_frames, probe_sample_rate)
            print(
                f"  (On Linux/JACK, omitting --block-ms auto-selects {auto_ms:.3f} ms / {auto_frames} frames.)"
            )
        print()

    _print_platform_usage_notes(
        samplerate=probe_sample_rate,
        hostapi_filter=hostapi_filter,
    )

    return 0
