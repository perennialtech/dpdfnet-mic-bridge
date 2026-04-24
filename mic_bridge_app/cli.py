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
            "DPDFNet microphone bridge for Windows 10/11 and Linux PipeWire.\n"
            "No subcommand shows this help.\n"
            "Use 'run' to start the bridge and 'list-devices' to inspect or pin IDs.\n"
            "On Linux, 'run' may create a temporary target with:\n"
            f"  {linux_virtual_mic_command()}\n"
            "Version 0 has no live resampling and no separate validate command.\n"
            "Windows virtual-cable setup, such as VB-CABLE, stays outside this tool."
        ),
        formatter_class=argparse.RawTextHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python mic_bridge.py\n"
            "  python mic_bridge.py run\n"
            "  python mic_bridge.py run --input 18 --output 16\n"
            "  python mic_bridge.py list-devices\n"
            "  python -m mic_bridge_app run\n"
        ),
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser(
        "list-devices",
        help="Inspect devices and pin numeric IDs when auto mode needs help.",
        formatter_class=argparse.RawTextHelpFormatter,
        description=(
            "Inspect PortAudio devices at the model's native sample rate.\n"
            "Windows defaults to WASAPI.\n"
            "Linux defaults to PipeWire if available, otherwise PulseAudio, JACK, then ALSA.\n"
            "Use --all-apis to show every host API."
        ),
    )
    list_parser.add_argument(
        "--model",
        type=str,
        choices=sorted(MODEL_SAMPLE_RATES),
        default=DEFAULT_MODEL,
        help=f"Model whose native sample rate will be probed. Default: {DEFAULT_MODEL}",
    )
    list_parser.add_argument(
        "--samplerate",
        type=int,
        default=None,
        help=(
            "Probe rate in Hz. Version 0 requires the model's native sample "
            "rate exactly. If omitted, use the model's native rate."
        ),
    )
    list_parser.add_argument(
        "--hostapi",
        type=str,
        choices=LIST_DEVICE_HOSTAPI_CHOICES,
        default=None,
        help=(
            "Host API filter.\n"
            "Windows default: WASAPI.\n"
            "Linux default: PipeWire if available, otherwise PulseAudio, JACK, then ALSA.\n"
        ),
    )
    list_parser.add_argument(
        "--all-apis",
        action="store_true",
        help="Show every PortAudio host API. Equivalent to --hostapi all.",
    )

    run_parser = subparsers.add_parser(
        "run",
        help="Run the live bridge, auto-selecting devices when safely possible.",
        formatter_class=argparse.RawTextHelpFormatter,
        description=(
            "Run the live mic -> DPDFNet -> output bridge.\n"
            "If --input and/or --output are omitted, the command auto-selects them when safe.\n"
            "Auto output never falls back to speakers, headphones, or monitor/source endpoints."
        ),
    )
    run_parser.add_argument(
        "--input",
        type=int,
        required=False,
        help=(
            "Numeric input device ID. If omitted, auto-select a likely physical microphone."
        ),
    )
    run_parser.add_argument(
        "--output",
        type=int,
        required=False,
        help=(
            "Numeric output device ID.\n"
            "If omitted, auto-select a likely bridge output.\n"
            "Windows: usually VB-CABLE 'CABLE Input'.\n"
            "Linux: may create a temporary DPDFNet_Mic target when none is visible."
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
            "Stream rate in Hz. Version 0 requires the model's native rate "
            "exactly. If omitted, use the model's native rate."
        ),
    )
    run_parser.add_argument(
        "--block-ms",
        type=float,
        default=None,
        help=(
            "Callback block size in milliseconds.\n"
            "If omitted:\n"
            "  - Linux/JACK uses an exact JACK-friendly size, typically 21.333 ms at 48 kHz\n"
            "  - other backends: 10.0 ms\n"
            "If you override this on Linux/JACK and hear glitches, try exact values such as 10.667 or 21.333 ms."
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
        help="Enable verbose DPDFNet model logging.",
    )

    return parser
