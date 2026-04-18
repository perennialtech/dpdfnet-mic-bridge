"""
Argument parser construction.

This module deliberately contains only CLI definition and help text. Keeping
parser setup separate from command execution makes future edits easier to audit.
"""

from __future__ import annotations

import argparse

from .audio_system import linux_virtual_mic_command
from .constants import (
    DEFAULT_LATENCY,
    DEFAULT_MODEL,
    LIST_DEVICE_HOSTAPI_CHOICES,
    MODEL_SAMPLE_RATES,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Command-line DPDFNet microphone bridge for Windows 10/11 and Linux PipeWire setups.\n"
            "Use 'list-devices' first, then 'run' with numeric device IDs.\n"
            "On Linux, PipeWire is reached through the PortAudio host APIs exposed by sounddevice,\n"
            "usually PipeWire directly if available, otherwise PulseAudio, JACK, or ALSA.\n"
            "For Linux/PipeWire virtual mic targets, the recommended creation command is:\n"
            f"  {linux_virtual_mic_command()}\n"
            "Version 0 intentionally has no separate 'validate' subcommand,\n"
            "use 'run' as the quick smoke-test path."
        ),
        formatter_class=argparse.RawTextHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python mic_bridge.py list-devices\n"
            "  python mic_bridge.py run --input 18 --output 16\n"
            "  python -m mic_bridge_app list-devices\n"
        ),
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser(
        "list-devices",
        help=(
            "List recommended audio devices and probe whether they open at the selected sample rate."
        ),
        formatter_class=argparse.RawTextHelpFormatter,
        description=(
            "List audio devices in a platform-friendly way.\n"
            "On Windows, the default view is filtered to WASAPI because that is the\n"
            "recommended starting point for this bridge.\n"
            "On Linux, the default view prefers a PipeWire host API if your PortAudio\n"
            "build exposes one, otherwise PulseAudio, then JACK, then ALSA.\n"
            "Use --all-apis to show every PortAudio host API instead."
        ),
    )
    list_parser.add_argument(
        "--model",
        type=str,
        choices=sorted(MODEL_SAMPLE_RATES),
        default=DEFAULT_MODEL,
        help=f"DPDFNet model whose native sample rate will be probed. Default: {DEFAULT_MODEL}",
    )
    list_parser.add_argument(
        "--samplerate",
        type=int,
        default=None,
        help=(
            "Probe sample rate in Hz. In Version 0 this must match the model's native "
            "sample rate exactly. If omitted, the model's native rate is used."
        ),
    )
    list_parser.add_argument(
        "--hostapi",
        type=str,
        choices=LIST_DEVICE_HOSTAPI_CHOICES,
        default=None,
        help=(
            "Host API filter for listing.\n"
            "Windows default: WASAPI.\n"
            "Linux default: PipeWire if available, otherwise PulseAudio, JACK, then ALSA.\n"
            "Use --hostapi all or --all-apis to show every PortAudio host API."
        ),
    )
    list_parser.add_argument(
        "--all-apis",
        action="store_true",
        help="Show every PortAudio host API. Equivalent to --hostapi all.",
    )

    run_parser = subparsers.add_parser(
        "run",
        help="Run the live microphone -> DPDFNet -> output-device bridge.",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    run_parser.add_argument(
        "--input",
        type=int,
        required=True,
        help="Numeric input device ID, typically your physical microphone.",
    )
    run_parser.add_argument(
        "--output",
        type=int,
        required=True,
        help=(
            "Numeric output device ID.\n"
            "Windows with VB-CABLE: usually the playback endpoint named 'CABLE Input'.\n"
            "Linux/PipeWire: usually the virtual mic target created with:\n"
            f"  {linux_virtual_mic_command()}\n"
        ),
    )
    run_parser.add_argument(
        "--model",
        type=str,
        choices=sorted(MODEL_SAMPLE_RATES),
        default=DEFAULT_MODEL,
        help=f"DPDFNet model to use. Default: {DEFAULT_MODEL}",
    )
    run_parser.add_argument(
        "--samplerate",
        type=int,
        default=None,
        help=(
            "Stream sample rate in Hz. In Version 0 this must match the model's native "
            "sample rate exactly. If omitted, the model's native rate is used."
        ),
    )
    run_parser.add_argument(
        "--block-ms",
        type=float,
        default=None,
        help=(
            "Callback block size in milliseconds.\n"
            "If omitted, the default is backend-aware:\n"
            "  - Linux/JACK: auto-select an exact JACK-friendly block size,\n"
            "    typically 21.333 ms at 48 kHz\n"
            "  - other backends: 10.0 ms\n"
            "If you override this on Linux/JACK and hear crackle/stutter,\n"
            "prefer exact values such as 10.667 or 21.333 ms at 48 kHz."
        ),
    )
    run_parser.add_argument(
        "--latency",
        type=str,
        choices=("low", "high"),
        default=DEFAULT_LATENCY,
        help=f"sounddevice latency hint. Default: {DEFAULT_LATENCY}",
    )
    run_parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose DPDFNet model resolution / download logging.",
    )

    return parser
