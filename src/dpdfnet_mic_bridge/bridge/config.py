"""Run configuration construction and validation."""

from __future__ import annotations

import argparse

from ..audio.blocksize import resolve_effective_block_size
from ..audio.hostapi import same_hostapi_guidance
from ..audio.inventory import hostapi_name_for_device
from ..audio.probing import negotiate_stream_channels
from ..audio.tags import infer_device_tags
from ..models.registry import model_native_sample_rate
from .device_selection import (get_current_run_device_info,
                               resolve_run_device_selection)
from .types import RunConfig

DEFAULT_LATENCY = "high"


def build_run_config(args: argparse.Namespace) -> RunConfig:
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

    requested_block_ms = args.block_ms
    requested_block_frames = args.block_frames
    block_size, effective_block_ms, block_policy = resolve_effective_block_size(
        sample_rate=sample_rate,
        requested_block_ms=requested_block_ms,
        requested_block_frames=requested_block_frames,
        hostapi_name=input_hostapi,
    )

    input_channels = negotiate_stream_channels(
        device_id=input_device_id,
        direction="input",
        samplerate=sample_rate,
        device_info=input_info,
        dtype="float32",
    )
    output_channels = negotiate_stream_channels(
        device_id=output_device_id,
        direction="output",
        samplerate=sample_rate,
        device_info=output_info,
        dtype="float32",
    )

    input_name = str(input_info["name"])
    output_name = str(output_info["name"])

    return RunConfig(
        model_name=args.model,
        sample_rate=sample_rate,
        block_ms=effective_block_ms,
        requested_block_ms=requested_block_ms,
        requested_block_frames=requested_block_frames,
        block_policy=block_policy,
        block_size=block_size,
        input_device_id=input_device_id,
        input_device_name=input_name,
        input_hostapi_name=input_hostapi,
        input_channels=input_channels,
        input_tags=infer_device_tags(input_name),
        input_was_auto_selected=input_was_auto_selected,
        output_device_id=output_device_id,
        output_device_name=output_name,
        output_hostapi_name=output_hostapi,
        output_channels=output_channels,
        output_tags=infer_device_tags(output_name),
        output_was_auto_selected=output_was_auto_selected,
        latency=args.latency,
    )
