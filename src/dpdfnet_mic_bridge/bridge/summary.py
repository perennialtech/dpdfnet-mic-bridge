"""Run summary, warnings, and restart tuning advice."""

from __future__ import annotations

import math
import platform
import shlex

from ..audio.blocksize import next_power_of_two
from ..audio.hostapi import hostapi_filter_key_for_name
from ..audio.tags import has_bridge_output_tags
from ..models.registry import DEFAULT_MODEL
from .config import DEFAULT_LATENCY
from .types import RunConfig


def print_run_summary(config: RunConfig) -> None:
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

    parts = ["dpdfnet-mic-bridge"]
    if config.model_name != DEFAULT_MODEL:
        parts.extend(["--model", config.model_name])

    parts.extend(["--input", str(config.input_device_id)])
    if not config.output_was_auto_selected:
        parts.extend(["--output", str(config.output_device_id)])

    if config.latency != DEFAULT_LATENCY:
        parts.extend(["--latency", config.latency])

    parts.extend([flag, value])
    return shlex.join(parts), label


def print_block_tuning_next_step(config: RunConfig) -> None:
    command, label = _restart_command_for_block(config)
    print("If audio broke up, restart with:")
    print(f"  {command}")
    print(
        f"  next block {label}; current {config.block_size} frames / {config.block_ms:.3f} ms"
    )
