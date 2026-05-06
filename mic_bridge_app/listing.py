"""Implementation of the `devices` command."""

from __future__ import annotations

import argparse
import platform
import sys

from .audio_system import (
    build_device_probes,
    build_run_command_suggestion,
    hostapi_filter_key_for_name,
    is_likely_virtual_bridge_output,
    is_viable_auto_input_candidate,
    linux_virtual_mic_command,
    query_audio_inventory,
    resolve_list_hostapi_filter,
    suggest_run_pair,
)
from .constants import (
    DEFAULT_MODEL,
    HOSTAPI_FILTER_DISPLAY_NAMES,
)
from .domain import DeviceProbe
from .modeling import model_native_sample_rate


def _eprint(*args: object) -> None:
    print(*args, file=sys.stderr)


def _short_hostapi_name(hostapi_name: str) -> str:
    key = hostapi_filter_key_for_name(hostapi_name)
    if key is None:
        return hostapi_name
    return {
        "pipewire": "PipeWire",
        "pulse": "Pulse",
        "jack": "JACK",
        "alsa": "ALSA",
        "wasapi": "WASAPI",
        "mme": "MME",
        "directsound": "DirectSound",
        "wdm-ks": "WDM-KS",
    }.get(key, hostapi_name)


def _probe_badges(probe: DeviceProbe) -> str:
    tags = set(probe.tags)
    badges: list[str] = []

    if probe.open_channels is not None and is_viable_auto_input_candidate(probe):
        badges.append("auto")
    elif is_likely_virtual_bridge_output(probe):
        badges.append("auto")
        badges.append("bridge")
    elif probe.open_channels is not None:
        badges.append("pin-only")
    elif "dpdfnet" in tags:
        badges.append("dpdfnet")
    if "mic" in tags:
        badges.append("mic")
    if "audio-interface" in tags:
        badges.append("interface")
    if "dummy" in tags:
        badges.append("dummy")
    if "hdmi" in tags:
        badges.append("hdmi")
    if "monitor" in tags:
        badges.append("monitor")
    if "headphones" in tags:
        badges.append("headphones")

    return ", ".join(dict.fromkeys(badges))


def _print_probe_section(
    *,
    title: str,
    probes: list[DeviceProbe],
) -> None:
    print(title)

    if not probes:
        print("  (none)")
        return

    for probe in probes:
        open_label = (
            "closed" if probe.open_channels is None else f"{probe.open_channels}ch"
        )
        details = [
            open_label,
            _short_hostapi_name(probe.hostapi_name),
        ]
        badges = _probe_badges(probe)
        if badges:
            details.append(badges)

        print(f"  [{probe.device_id}]  {probe.name}  ({', '.join(details)})")


def _print_next_steps(
    *,
    model_name: str,
    suggested_pair: tuple[DeviceProbe, DeviceProbe] | None,
    output_probes: list[DeviceProbe],
) -> None:
    system = platform.system()

    if suggested_pair is not None:
        input_probe, output_probe = suggested_pair
        print("Use:")
        print(
            "  "
            + build_run_command_suggestion(
                input_id=input_probe.device_id,
                output_id=output_probe.device_id,
                model_name=model_name,
            )
        )
    else:
        print("Use:")
        print("  python mic_bridge.py --input <id> --output <id>")

    if system == "Linux" and not any(
        is_likely_virtual_bridge_output(probe) for probe in output_probes
    ):
        print()
        print("No bridge output is visible.")
        print("Create a virtual mic target:")
        print(f"  {linux_virtual_mic_command()}")

    print()
    print("If a pinned ID stops working, run `python mic_bridge.py devices` again.")


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
        "python mic_bridge.py"
        if args.model == DEFAULT_MODEL
        else f"python mic_bridge.py --model {args.model}"
    )

    print(f"Devices ({args.model}, {probe_sample_rate} Hz)")
    print(f"Showing: {HOSTAPI_FILTER_DISPLAY_NAMES[hostapi_filter]}")
    print(f"Start  : {fast_path_command}")
    if hostapi_filter != "all":
        print("More   : python mic_bridge.py devices --all-apis")
    print(
        "Auto   : chooses devices marked auto; pin-only devices require --input/--output"
    )
    print()

    _print_probe_section(
        title="Input candidates",
        probes=input_probes,
    )
    print()
    _print_probe_section(
        title="Output candidates",
        probes=output_probes,
    )
    print()

    if hostapi_filter != "all" and not input_probes and not output_probes:
        print(
            f"No devices matched '{hostapi_filter}'. Try: python mic_bridge.py devices --all-apis"
        )
        print()

    if hostapi_filter == "all" and not input_probes and not output_probes:
        print("No PortAudio devices are currently visible.")
        if platform.system() == "Windows":
            print(
                "Check Windows sound settings and confirm VB-CABLE is installed if you need it."
            )
        elif platform.system() == "Linux":
            print(
                "Check that your audio session is running and PortAudio can see PipeWire/Pulse/JACK/ALSA."
            )
        print()

    suggested_pair = suggest_run_pair(input_probes, output_probes)

    _print_next_steps(
        model_name=args.model,
        suggested_pair=suggested_pair,
        output_probes=output_probes,
    )

    return 0
