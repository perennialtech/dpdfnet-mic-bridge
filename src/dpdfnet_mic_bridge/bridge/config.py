"""Run configuration construction and validation."""

from __future__ import annotations

import argparse
from contextlib import nullcontext
from typing import TYPE_CHECKING

from ..audio.blocksize import MAX_AUTO_BLOCK_MS, iter_auto_block_sizes
from ..audio.hostapi import same_hostapi_guidance
from ..audio.inventory import hostapi_name_for_device
from ..audio.probing import negotiate_full_duplex_channels
from ..audio.route_validation import validate_backend_route
from ..audio.tags import infer_device_tags
from ..models.registry import model_native_sample_rate
from .device_selection import get_current_run_device_info, resolve_run_device_selection
from .types import RunConfig

if TYPE_CHECKING:
    from ..realtime_env.runtime import RealtimeRuntimeManager


def build_run_config(
    args: argparse.Namespace,
    realtime_manager: RealtimeRuntimeManager | None = None,
    *,
    app_microphone_name: str | None,
) -> RunConfig:
    """
    Resolve CLI args into a concrete run configuration.

    This enforces sample-rate, host-API, channel, and block-size policy.
    """
    sample_rate = model_native_sample_rate(args.model)

    requested_input_id = args.input
    requested_output_id = args.output

    (
        input_device_id,
        output_device_id,
        input_was_auto_selected,
        output_was_auto_selected,
        inventory,
    ) = resolve_run_device_selection(
        sample_rate=sample_rate,
        requested_input_id=requested_input_id,
        requested_output_id=requested_output_id,
    )

    input_info = get_current_run_device_info(
        inventory=inventory,
        device_id=input_device_id,
        role="input",
    )
    output_info = get_current_run_device_info(
        inventory=inventory,
        device_id=output_device_id,
        role="output",
    )

    input_hostapi = hostapi_name_for_device(input_info, inventory.hostapi_names)
    output_hostapi = hostapi_name_for_device(output_info, inventory.hostapi_names)

    if int(input_info["hostapi"]) != int(output_info["hostapi"]):
        raise ValueError(
            "Input and output devices must use the same host API in Version 0.\n"
            f"Input : id={input_device_id}, hostapi={input_hostapi}, name={input_info['name']}\n"
            f"Output: id={output_device_id}, hostapi={output_hostapi}, name={output_info['name']}\n"
            f"{same_hostapi_guidance()}"
        )

    input_name = str(input_info["name"])
    output_name = str(output_info["name"])
    input_tags = infer_device_tags(input_name)
    output_tags = infer_device_tags(output_name)

    validate_backend_route(
        inventory=inventory,
        input_device_id=input_device_id,
        output_device_id=output_device_id,
        input_hostapi=input_hostapi,
        output_hostapi=output_hostapi,
        input_name=input_name,
        output_name=output_name,
        input_tags=input_tags,
        output_tags=output_tags,
    )

    last_error: Exception | None = None
    for block_size, effective_block_ms, block_policy in iter_auto_block_sizes(
        sample_rate=sample_rate,
        hostapi_name=input_hostapi,
    ):
        context = (
            realtime_manager.probe_backend_latency_context(
                hostapi_name=input_hostapi,
                sample_rate=sample_rate,
                block_size=block_size,
                block_ms=effective_block_ms,
            )
            if realtime_manager is not None
            else nullcontext()
        )

        try:
            with context:
                input_channels, output_channels = negotiate_full_duplex_channels(
                    input_device_id=input_device_id,
                    output_device_id=output_device_id,
                    samplerate=sample_rate,
                    block_size=block_size,
                    latency=args.latency,
                    input_device_info=input_info,
                    output_device_info=output_info,
                    dtype="float32",
                )
        except Exception as exc:
            last_error = exc
            continue

        return RunConfig(
            model_name=args.model,
            sample_rate=sample_rate,
            block_ms=effective_block_ms,
            block_policy=block_policy,
            block_size=block_size,
            input_device_id=input_device_id,
            native_input_device_id=input_device_id,
            input_device_name=input_name,
            input_hostapi_name=input_hostapi,
            input_channels=input_channels,
            input_tags=input_tags,
            input_was_auto_selected=input_was_auto_selected,
            output_device_id=output_device_id,
            native_output_device_id=output_device_id,
            output_device_name=output_name,
            output_hostapi_name=output_hostapi,
            output_channels=output_channels,
            output_tags=output_tags,
            output_was_auto_selected=output_was_auto_selected,
            app_microphone_name=app_microphone_name,
            latency=args.latency,
        )

    detail = "" if last_error is None else f"\nLast probe failure:\n{last_error}"
    raise RuntimeError(
        f"Could not validate an exact full-duplex stream route up to {MAX_AUTO_BLOCK_MS:.0f} ms blocks."
        f"{detail}"
    )
