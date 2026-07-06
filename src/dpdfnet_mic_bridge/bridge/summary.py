"""Run summary, warnings, and restart tuning advice."""

from __future__ import annotations

import platform
import shlex

from ..audio.hostapi import hostapi_filter_key_for_name
from ..audio.tags import has_bridge_output_tags
from ..models.registry import DEFAULT_MODEL
from ..realtime_env.types import RuntimeTuningResult
from .config import DEFAULT_LATENCY
from .types import RunConfig


def print_run_summary(
    config: RunConfig,
    realtime_result: RuntimeTuningResult | None = None,
    realtime_warnings: list[str] | None = None,
) -> None:
    system = platform.system()
    input_tags = set(config.input_tags)
    output_tags = set(config.output_tags)

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
    if config.block_policy == "auto-jack-period":
        block_line += " (JACK period multiple)"
    elif config.block_policy == "auto-20ms-grow":
        block_line += " (auto)"

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

    if realtime_result is not None:
        applied = [
            action.id.rsplit(".", 1)[-1]
            for action in realtime_result.actions
            if action.status == "applied"
        ]
        failed = [
            action.id.rsplit(".", 1)[-1]
            for action in realtime_result.actions
            if action.status == "failed"
        ]
        status_parts = [realtime_result.mode]
        if applied:
            status_parts.append("applied " + ",".join(applied[:4]))
        if failed:
            status_parts.append("failed " + ",".join(failed[:4]))
        print(f"  rt     {'; '.join(status_parts)}")

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

    if realtime_warnings:
        warnings.extend(realtime_warnings)

    if warnings:
        print("  warn")
        for line in warnings:
            print(f"  - {line}")
    print()


def _restart_command_for_same_route(config: RunConfig) -> str:
    parts = ["dpdfnet-mic-bridge"]
    if config.model_name != DEFAULT_MODEL:
        parts.extend(["--model", config.model_name])

    parts.extend(["--input", str(config.input_device_id)])
    if not config.output_was_auto_selected:
        parts.extend(["--output", str(config.output_device_id)])

    if config.latency != DEFAULT_LATENCY:
        parts.extend(["--latency", config.latency])

    return shlex.join(parts)


def print_block_tuning_next_step(config: RunConfig) -> None:
    command = _restart_command_for_same_route(config)
    print("Block sizing is automatic. If audio broke up, restart the same route:")
    print(f"  {command}")
    print(
        f"  current automatic block {config.block_size} frames / {config.block_ms:.3f} ms"
    )
