"""Argument parser construction."""

from __future__ import annotations

import argparse

from ..audio.hostapi import LIST_DEVICE_HOSTAPI_CHOICES
from ..audio.virtual_mic import linux_virtual_mic_command
from ..bridge.config import DEFAULT_LATENCY
from ..models.registry import DEFAULT_MODEL, MODEL_SAMPLE_RATES


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "DPDFNet microphone bridge.\n"
            "\n"
            "Examples:\n"
            "  dpdfnet-mic-bridge\n"
            "  dpdfnet-mic-bridge devices\n"
            "  dpdfnet-mic-bridge --input 15 --output 19"
        ),
        formatter_class=argparse.RawTextHelpFormatter,
        epilog=(
            "Linux reusable target:\n"
            f"  {linux_virtual_mic_command()}\n\n"
            "No live resampling; the model chooses the sample rate."
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
            "List usable PortAudio devices at the model's native sample rate.\n"
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

    run_parser = subparsers.add_parser(
        "run",
        help="run the live bridge",
        formatter_class=argparse.RawTextHelpFormatter,
        description="Run the live mic -> DPDFNet -> virtual mic bridge.",
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
            "Windows: usually VB-CABLE 'CABLE Input'; Linux: usually DPDFNet_Mic."
        ),
    )
    run_parser.add_argument(
        "--model",
        type=str,
        choices=sorted(MODEL_SAMPLE_RATES),
        default=DEFAULT_MODEL,
        help=f"DPDFNet model to use (default: {DEFAULT_MODEL})",
    )

    block_group = run_parser.add_mutually_exclusive_group()
    block_group.add_argument(
        "--block-ms",
        type=float,
        default=None,
        help=(
            "callback block size in milliseconds\n"
            "default: 10 ms, except Linux/JACK uses a JACK-friendly exact size"
        ),
    )
    block_group.add_argument(
        "--block-frames",
        type=int,
        default=None,
        help="exact callback block size in frames; useful for JACK/PipeWire-JACK",
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
