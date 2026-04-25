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
            "DPDFNet microphone bridge.\n\n"
            "Common:\n"
            "  python mic_bridge.py run\n"
            "  python mic_bridge.py devices\n"
            "  python mic_bridge.py run --input 18 --output 16\n\n"
            "No arguments show this help. Run flags may also be used without the\n"
            "explicit 'run' command. Device-listing flags imply 'devices'."
        ),
        formatter_class=argparse.RawTextHelpFormatter,
        epilog=(
            "Linux reusable virtual mic target:\n"
            f"  {linux_virtual_mic_command()}\n\n"
            "Version 0 has no live resampling and no sample-rate override."
        ),
    )

    subparsers = parser.add_subparsers(
        dest="command",
        required=True,
        metavar="{run,devices}",
    )

    list_parser = subparsers.add_parser(
        "devices",
        help="inspect devices and pin numeric IDs",
        formatter_class=argparse.RawTextHelpFormatter,
        description=(
            "Inspect PortAudio devices at the model's native sample rate.\n"
            "Defaults: WASAPI on Windows; PipeWire/PulseAudio/JACK/ALSA preference on Linux."
        ),
    )
    list_parser.add_argument(
        "--model",
        type=str,
        choices=sorted(MODEL_SAMPLE_RATES),
        default=DEFAULT_MODEL,
        help=f"model whose native sample rate will be probed (default: {DEFAULT_MODEL})",
    )

    list_parser.add_argument(
        "--hostapi",
        type=str,
        choices=LIST_DEVICE_HOSTAPI_CHOICES,
        default=None,
        help="host API filter",
    )
    list_parser.add_argument(
        "--all-apis",
        action="store_true",
        help="show every PortAudio host API",
    )
    list_parser.add_argument(
        "--verbose",
        action="store_true",
        help="show probe failure details",
    )

    run_parser = subparsers.add_parser(
        "run",
        help="run the live bridge",
        formatter_class=argparse.RawTextHelpFormatter,
        description=(
            "Run the live mic -> DPDFNet -> output bridge.\n"
            "Omitted devices are auto-selected only when safe."
        ),
    )
    run_parser.add_argument(
        "--input",
        type=int,
        required=False,
        help="numeric input device ID",
    )
    run_parser.add_argument(
        "--output",
        type=int,
        required=False,
        help=(
            "numeric output device ID\n"
            "Windows: usually VB-CABLE 'CABLE Input'.\n"
            "Linux: may auto-create a temporary DPDFNet_Mic target."
        ),
    )
    run_parser.add_argument(
        "--model",
        type=str,
        choices=sorted(MODEL_SAMPLE_RATES),
        default=DEFAULT_MODEL,
        help=f"DPDFNet model to use (default: {DEFAULT_MODEL})",
    )

    run_parser.add_argument(
        "--block-ms",
        type=float,
        default=None,
        help=(
            "callback block size in milliseconds\n"
            "default: 10 ms, except Linux/JACK uses a JACK-friendly exact size"
        ),
    )
    run_parser.add_argument(
        "--latency",
        type=str,
        choices=("low", "high"),
        default=DEFAULT_LATENCY,
        help=f"sounddevice latency hint (default: {DEFAULT_LATENCY})",
    )
    run_parser.add_argument(
        "--verbose",
        action="store_true",
        help="enable verbose DPDFNet model logging",
    )

    return parser
