"""
Implementation of the `list-devices` command.

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
    format_probe_openability,
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
) -> None:
    print(title)

    if not probes:
        print("  (none)")
        return

    for probe in probes:
        max_label = "max_in" if probe.direction == "input" else "max_out"
        print(f"  [{probe.device_id}] {probe.name}")
        print(
            "       "
            f"hostapi={probe.hostapi_name}, "
            f"{max_label}={probe.max_channels}, "
            f"default_sr={format_samplerate(probe.default_samplerate)}, "
            f"{format_probe_openability(probe, samplerate)}, "
            f"tags={format_tags(probe.tags)}"
        )


def _print_platform_usage_notes(*, samplerate: int, hostapi_filter: str) -> None:
    system = platform.system()

    if system == "Windows":
        _print_lines(
            [
                "Windows notes:",
                "  - Try: python mic_bridge.py run",
                f"  - Probe results show whether a device opens at {samplerate} Hz.",
                "  - Auto output only picks bridge targets such as VB-CABLE 'CABLE Input'.",
                "  - Apps should use VB-CABLE 'CABLE Output' as the microphone.",
                "  - If auto mode stops, run list-devices and pin --input/--output IDs.",
                "  - Device IDs can drift, rerun list-devices when they do.",
                "  - Version 0 has no separate validate command, use run and stop with Ctrl+C.",
            ]
        )
        print()
        return

    if system == "Linux":
        lines = [
            "Linux notes:",
            "  - Try: python mic_bridge.py run",
            "  - Keep input and output on the same host API.",
            "  - Auto output only picks virtual mic targets, never speakers/headphones or monitor/source endpoints.",
            f"  - If none is visible, run may create a temporary '{LINUX_VIRTUAL_MIC_DESCRIPTION}' target.",
            "  - A target created by the run is removed on exit.",
            "  - For a reusable target, create it yourself with:",
            f"      {linux_virtual_mic_command()}",
            f"  - Probe results show whether a device opens at {samplerate} Hz.",
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
        lines.extend(
            [
                "  - If auto mode stops, run list-devices and pin --input/--output IDs.",
                "  - Device IDs can drift, rerun list-devices when they do.",
                "  - Version 0 has no separate validate command, use run and stop with Ctrl+C.",
            ]
        )
        _print_lines(lines)
        print()
        return

    _print_lines(
        [
            "Platform notes:",
            "  - Try: python mic_bridge.py run",
            f"  - Probe results show whether a device opens at {samplerate} Hz.",
            "  - If pinned IDs stop working after device churn, rerun list-devices.",
        ]
    )
    print()


def list_devices_command(args: argparse.Namespace) -> int:
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
    print(f"Device discovery for {args.model} @ {probe_sample_rate} Hz")
    print(f"Fast path : {fast_path_command}")
    print(f"Host API  : {HOSTAPI_FILTER_DISPLAY_NAMES[hostapi_filter]}")
    if hostapi_filter != "all":
        print("Tip       : use --all-apis to show every PortAudio device.")
    print()

    _print_probe_section(
        title="Input candidates",
        probes=input_probes,
        samplerate=probe_sample_rate,
    )
    print()
    _print_probe_section(
        title="Output candidates",
        probes=output_probes,
        samplerate=probe_sample_rate,
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
        print("Suggested pinned command:")
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
