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
    get_hostapi_names,
    jack_block_ms_for_frames,
    linux_virtual_mic_command,
    recommended_linux_jack_block_frames,
    resolve_list_hostapi_filter,
    suggest_run_pair,
)
from .constants import (
    DEFAULT_MODEL,
    HOSTAPI_FILTER_DISPLAY_NAMES,
    LINUX_VIRTUAL_MIC_DESCRIPTION,
)
from .dependencies import require_sounddevice
from .domain import DeviceProbe
from .modeling import resolve_stream_sample_rate


def _eprint(*args: object) -> None:
    print(*args, file=sys.stderr)


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
        print("Windows usage notes:")
        print("  - Fast path: try python mic_bridge.py first.")
        print(
            "  - Auto mode only picks likely bridge outputs such as VB-CABLE 'CABLE Input',"
        )
        print("    never speakers/headphones.")
        print(
            "  - VB-CABLE still needs to be installed and configured outside this script."
        )
        print("  - For VB-CABLE:")
        print(
            "      * this script should OUTPUT to the playback endpoint, usually named 'CABLE Input'"
        )
        print(
            "      * your apps should READ from the recording endpoint, usually named 'CABLE Output'"
        )
        print(
            f"  - Probe results above show whether a device can open at {samplerate} Hz."
        )
        print(
            "  - If auto mode stops, use the IDs above with python mic_bridge.py --input <id> --output <id>."
        )
        print("  - Version 0 intentionally has no separate 'validate' command.")
        print("    Use the run path and stop it with Ctrl+C as the quick smoke test.")
        print()
        return

    if system == "Linux":
        print("Linux PipeWire usage notes:")
        print("  - Fast path: try python mic_bridge.py first.")
        print("  - This script uses the PortAudio host APIs exposed by sounddevice.")
        print(
            "  - On PipeWire systems, prefer a real 'PipeWire' host API if your PortAudio build has one,"
        )
        print("    otherwise prefer PulseAudio, then JACK, then ALSA.")
        print("  - Keep --input and --output on the same host API.")
        print(
            "  - Auto mode only picks likely virtual mic targets for output, never speakers/headphones."
        )
        print(
            "  - If --output is omitted and no likely virtual target is visible, the run path tries"
        )
        print(
            f"    to create '{LINUX_VIRTUAL_MIC_DESCRIPTION}' automatically with pactl."
        )
        print("  - Recommended virtual mic target command:")
        print(f"      {linux_virtual_mic_command()}")
        print("  - Why this form:")
        print(
            "      * `media.class=Audio/Source/Virtual` makes many apps expose it more like a mic"
        )
        print(
            "      * `channels=1 channel_map=mono` keeps it explicitly mono and avoids left/right surprises"
        )
        print(
            "      * the bridge still writes to it as an output/playback device, despite the mic-like naming"
        )
        print(
            f"  - Probe results above show whether a device can open at {samplerate} Hz."
        )
        print(
            f"  - In apps, first try choosing '{LINUX_VIRTUAL_MIC_DESCRIPTION}' directly as the microphone."
        )
        print(
            "    If a given app does not expose it directly, try the matching monitor/source instead."
        )
        if hostapi_filter == "jack":
            auto_frames = recommended_linux_jack_block_frames(samplerate)
            auto_ms = jack_block_ms_for_frames(auto_frames, samplerate)
            print("  - Your current device view is using JACK.")
            print(
                "  - On Linux/JACK, if you omit --block-ms, 'run' auto-selects an exact JACK-friendly"
            )
            print(
                f"    block size, typically {auto_ms:.3f} ms ({auto_frames} frames) at {samplerate} Hz."
            )
            print(
                "    This avoids a common glitch case where raw 10.0 ms becomes a non-exact frame count."
            )
            print(
                "  - If you override --block-ms on JACK and hear crackle/stutter, try exact values such as:"
            )
            print(f"      {format_jack_block_ms_hints(samplerate)} ms")
        print(
            "  - If auto mode stops, use the IDs above with python mic_bridge.py --input <id> --output <id>."
        )
        print("  - Version 0 intentionally has no separate 'validate' command.")
        print("    Use the run path and stop it with Ctrl+C as the quick smoke test.")
        print()
        return

    print("Platform usage notes:")
    print(
        "  - This script is primarily intended for Windows 10/11 and Linux systems running PipeWire."
    )
    print("  - Fast path: try python mic_bridge.py first.")
    print(f"  - Probe results above show whether a device can open at {samplerate} Hz.")
    print()


def list_devices_command(args: argparse.Namespace) -> int:
    sd_mod = require_sounddevice()

    probe_sample_rate = resolve_stream_sample_rate(
        args.model,
        args.samplerate,
        context="device listing",
    )

    if platform.system() not in ("Windows", "Linux"):
        _eprint(
            "[note] This script is primarily intended for Windows 10/11 and Linux PipeWire systems, though device listing may still work elsewhere."
        )

    devices = sd_mod.query_devices()
    hostapi_names = get_hostapi_names(sd_mod)
    hostapi_filter = resolve_list_hostapi_filter(
        args.hostapi,
        args.all_apis,
        hostapi_names,
    )

    input_probes, output_probes = build_device_probes(
        devices=devices,
        hostapi_names=hostapi_names,
        hostapi_filter=hostapi_filter,
        samplerate=probe_sample_rate,
    )

    fast_path_command = (
        "python mic_bridge.py"
        if args.model == DEFAULT_MODEL
        else f"python mic_bridge.py --model {args.model}"
    )

    print()
    print(f"Device discovery for model {args.model} @ {probe_sample_rate} Hz")
    print(
        f"Fast path: try {fast_path_command} first. Use this command when auto mode needs a manual handoff or when you want to pin IDs."
    )
    print(f"Host API filter: {HOSTAPI_FILTER_DISPLAY_NAMES[hostapi_filter]}")
    if hostapi_filter != "all":
        print("Use --all-apis to show every PortAudio device.")
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
            f"No devices matched the current Host API filter '{hostapi_filter}'. Try --all-apis."
        )
        print()

    suggested_pair = suggest_run_pair(input_probes, output_probes)
    if suggested_pair is not None:
        input_probe, output_probe = suggested_pair
        print("Pinned pair:")
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
