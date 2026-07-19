"""Bridge runtime data structures."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RunConfig:
    model_name: str
    sample_rate: int
    block_ms: float
    block_policy: str
    block_size: int
    input_device_id: int
    native_input_device_id: int
    input_device_name: str
    input_hostapi_name: str
    input_channels: int
    input_tags: tuple[str, ...]
    input_was_auto_selected: bool
    output_device_id: int
    native_output_device_id: int
    output_device_name: str
    output_hostapi_name: str
    output_channels: int
    output_tags: tuple[str, ...]
    output_was_auto_selected: bool
    app_microphone_name: str | None
    latency: str
