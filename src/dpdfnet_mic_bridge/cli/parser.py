"""Argument parser construction."""

from __future__ import annotations

import argparse

from ..audio.hostapi import LIST_DEVICE_HOSTAPI_CHOICES
from ..bridge.types import DEFAULT_LATENCY
from ..models.registry import DEFAULT_MODEL, MODEL_SAMPLE_RATES
from ..realtime_env.types import (
    DEFAULT_LINUX_MEMLOCK_KB,
    DEFAULT_LINUX_NICE,
    DEFAULT_LINUX_RTPRIO_LIMIT,
    REALTIME_MODE_CHOICES,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "DPDFNet microphone bridge.\n"
            "\n"
            "Examples:\n"
            "  dpdfnet-mic-bridge\n"
            "  dpdfnet-mic-bridge devices\n"
            "  dpdfnet-mic-bridge --input 15 --output 19\n"
            "  dpdfnet-mic-bridge realtime status"
        ),
        formatter_class=argparse.RawTextHelpFormatter,
    )

    subparsers = parser.add_subparsers(
        dest="command",
        required=True,
        metavar="{desktop,run,devices,realtime}",
    )

    subparsers.add_parser("desktop", help="open the microphone bridge window")

    list_parser = subparsers.add_parser(
        "devices",
        help="inspect microphones and advanced native devices",
        formatter_class=argparse.RawTextHelpFormatter,
        description=(
            "List microphone sources: PulseAudio on Linux, WASAPI on Windows.\n"
            "Use --all-apis for the advanced PortAudio inventory."
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
        help="microphone ID from devices (PulseAudio source on Linux, WASAPI device on Windows)",
    )
    run_parser.add_argument(
        "--source",
        type=str,
        help="Linux PulseAudio source name (advanced alternative to --input)",
    )
    run_parser.add_argument(
        "--output",
        type=int,
        required=False,
        help=(
            "numeric output device ID\n"
            "Windows: VB-CABLE 'CABLE Input'. Linux: opt into an advanced manual PortAudio route."
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
        "--latency",
        type=str,
        choices=("low", "high"),
        default=DEFAULT_LATENCY,
        help=f"native PortAudio latency hint (default: {DEFAULT_LATENCY})",
    )
    run_parser.add_argument(
        "--realtime",
        type=str,
        choices=REALTIME_MODE_CHOICES,
        default="off",
        help="OS realtime tuning mode (default: off; auto/strict may change session-wide settings)",
    )
    run_parser.add_argument(
        "--verbose",
        action="store_true",
        help="enable verbose DPDFNet model logging",
    )

    realtime_parser = subparsers.add_parser(
        "realtime",
        help="inspect or apply OS realtime prerequisites",
        formatter_class=argparse.RawTextHelpFormatter,
        description="Inspect or apply OS-level realtime audio prerequisites.",
    )
    realtime_subparsers = realtime_parser.add_subparsers(
        dest="realtime_command",
        required=True,
        metavar="{status,apply}",
    )

    status_parser = realtime_subparsers.add_parser(
        "status",
        help="inspect realtime readiness",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    status_parser.add_argument("--json", action="store_true", help="print JSON")
    status_parser.add_argument(
        "--model",
        type=str,
        choices=sorted(MODEL_SAMPLE_RATES),
        default=DEFAULT_MODEL,
        help=f"model whose native sample rate will be probed (default: {DEFAULT_MODEL})",
    )
    status_parser.add_argument(
        "--input", type=int, required=False, help="numeric input device ID"
    )
    status_parser.add_argument(
        "--output", type=int, required=False, help="numeric output device ID"
    )
    status_parser.add_argument(
        "--latency",
        type=str,
        choices=("low", "high"),
        default=DEFAULT_LATENCY,
        help=f"native PortAudio latency hint (default: {DEFAULT_LATENCY})",
    )

    apply_parser = realtime_subparsers.add_parser(
        "apply",
        help="apply persistent OS realtime prerequisites",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    apply_parser.add_argument(
        "--yes", action="store_true", help="perform modifications"
    )
    apply_parser.add_argument("--json", action="store_true", help="print JSON")
    apply_parser.add_argument(
        "--rtprio",
        type=int,
        default=DEFAULT_LINUX_RTPRIO_LIMIT,
        help=f"Linux rtprio limit to write (default: {DEFAULT_LINUX_RTPRIO_LIMIT})",
    )
    apply_parser.add_argument(
        "--nice",
        type=int,
        default=DEFAULT_LINUX_NICE,
        help=f"Linux nice limit to write (default: {DEFAULT_LINUX_NICE})",
    )
    apply_parser.add_argument(
        "--memlock-kb",
        type=int,
        default=DEFAULT_LINUX_MEMLOCK_KB,
        help=f"Linux memlock KiB to write (default: {DEFAULT_LINUX_MEMLOCK_KB})",
    )
    apply_parser.add_argument(
        "--no-power",
        action="store_true",
        help="skip Windows power-plan changes",
    )
    apply_parser.add_argument(
        "--no-services",
        action="store_true",
        help="skip Windows service changes",
    )

    return parser
