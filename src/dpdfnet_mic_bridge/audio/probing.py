"""PortAudio device probing and channel negotiation through native Rust."""

from __future__ import annotations

import os
import sys
from collections.abc import Iterable
from contextlib import contextmanager

from .. import _native_audio as native_audio
from .hostapi import hostapi_matches_filter
from .inventory import hostapi_name_for_device
from .selection import device_probe_sort_key
from .tags import infer_device_tags
from .types import AudioInventory, DeviceProbe


@contextmanager
def _silence_native_stderr():
    """
    Hide noisy native diagnostics during probe calls.

    ALSA/PortAudio can write directly to fd 2 while native check calls try
    devices that are expected to fail. The Python exception is still captured
    and shown in the normal UI; the raw native spam is not useful.
    """
    try:
        stderr_fd = sys.stderr.fileno()
        saved_fd = os.dup(stderr_fd)
        devnull_fd = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull_fd, stderr_fd)
    except (AttributeError, OSError, ValueError):
        yield
        return

    try:
        yield
    finally:
        try:
            os.dup2(saved_fd, stderr_fd)
        finally:
            os.close(saved_fd)
            os.close(devnull_fd)


def iter_input_channel_candidates(max_channels: int) -> Iterable[int]:
    """Prefer mono, then stereo, then native max."""
    seen: set[int] = set()
    for candidate in (1, 2, max_channels):
        if 1 <= candidate <= max_channels and candidate not in seen:
            seen.add(candidate)
            yield candidate


def iter_output_channel_candidates(max_channels: int) -> Iterable[int]:
    """Prefer stereo, then mono, then native max."""
    seen: set[int] = set()
    for candidate in (2, 1, max_channels):
        if 1 <= candidate <= max_channels and candidate not in seen:
            seen.add(candidate)
            yield candidate


def probe_device_channels(
    *,
    device_id: int,
    direction: str,
    samplerate: int,
    device_info: dict,
    dtype: str = "float32",
) -> tuple[int | None, tuple[str, ...]]:
    info = dict(device_info)

    if direction == "input":
        max_channels = int(info["max_input_channels"])
        check = native_audio.check_input_settings
        channel_candidates = iter_input_channel_candidates(max_channels)
    elif direction == "output":
        max_channels = int(info["max_output_channels"])
        check = native_audio.check_output_settings
        channel_candidates = iter_output_channel_candidates(max_channels)
    else:
        raise ValueError(f"Unsupported direction '{direction}'")

    if max_channels < 1:
        return None, (f"device exposes no {direction} channels",)

    failures: list[str] = []
    for channels in channel_candidates:
        try:
            with _silence_native_stderr():
                check(device_id, channels, samplerate, dtype)
            return channels, tuple()
        except Exception as exc:
            failures.append(f"{channels}ch -> {exc}")

    return None, tuple(failures)


def negotiate_stream_channels(
    *,
    device_id: int,
    direction: str,
    samplerate: int,
    device_info: dict,
    dtype: str = "float32",
) -> int:
    """Pick an input/output channel count the native engine can open."""
    channels, failures = probe_device_channels(
        device_id=device_id,
        direction=direction,
        samplerate=samplerate,
        device_info=device_info,
        dtype=dtype,
    )

    if channels is not None:
        return channels

    failure_text = "\n".join(f"  - {line}" for line in failures)
    raise RuntimeError(
        f"Could not open {direction} device {device_id} at {samplerate} Hz, dtype={dtype}.\n"
        f"Tried channel counts:\n{failure_text}"
    )


def negotiate_full_duplex_channels(
    *,
    input_device_id: int,
    output_device_id: int,
    samplerate: int,
    block_size: int,
    latency: str,
    input_device_info: dict,
    output_device_info: dict,
    dtype: str = "float32",
) -> tuple[int, int]:
    """
    Pick channel counts by opening the exact full-duplex stream configuration.

    This replaces independent input/output probing for the actual run path:
    device pair, channel counts, sample rate, block size, latency mode, dtype,
    and PortAudio stream flags are all validated together.
    """
    input_failures: list[str] = []
    for input_channels in iter_input_channel_candidates(
        int(input_device_info["max_input_channels"])
    ):
        for output_channels in iter_output_channel_candidates(
            int(output_device_info["max_output_channels"])
        ):
            try:
                with _silence_native_stderr():
                    native_audio.check_full_duplex_stream_settings(
                        input_device_id,
                        output_device_id,
                        input_channels,
                        output_channels,
                        samplerate,
                        block_size,
                        latency,
                        dtype,
                    )
                return input_channels, output_channels
            except Exception as exc:
                input_failures.append(
                    f"{input_channels}ch in / {output_channels}ch out -> {exc}"
                )

    failure_text = "\n".join(f"  - {line}" for line in input_failures)
    raise RuntimeError(
        "Could not open the exact full-duplex stream configuration.\n"
        f"input_device={input_device_id}, output_device={output_device_id}, "
        f"samplerate={samplerate}, block_size={block_size}, latency={latency}, dtype={dtype}\n"
        f"Tried channel pairs:\n{failure_text}"
    )


def build_device_probes(
    *,
    inventory: AudioInventory,
    hostapi_filter: str,
    samplerate: int,
) -> tuple[list[DeviceProbe], list[DeviceProbe]]:
    input_probes: list[DeviceProbe] = []
    output_probes: list[DeviceProbe] = []

    direction_specs = (
        ("input", "max_input_channels", input_probes),
        ("output", "max_output_channels", output_probes),
    )

    for position, raw_info in enumerate(inventory.devices):
        info = dict(raw_info)
        device_id = int(info.get("id", position))
        hostapi_name = hostapi_name_for_device(info, inventory.hostapi_names)

        if not hostapi_matches_filter(hostapi_name, hostapi_filter):
            continue

        name = str(info["name"])
        tags = infer_device_tags(name)
        default_sr = float(info["default_samplerate"])

        for direction, max_channel_key, bucket in direction_specs:
            max_channels = int(info[max_channel_key])
            if max_channels < 1:
                continue

            open_channels, failures = probe_device_channels(
                device_id=device_id,
                direction=direction,
                samplerate=samplerate,
                dtype="float32",
                device_info=info,
            )
            bucket.append(
                DeviceProbe(
                    device_id=device_id,
                    direction=direction,
                    name=name,
                    hostapi_name=hostapi_name,
                    default_samplerate=default_sr,
                    max_channels=max_channels,
                    open_channels=open_channels,
                    probe_failures=failures,
                    tags=tags,
                )
            )

    input_probes.sort(key=device_probe_sort_key)
    output_probes.sort(key=device_probe_sort_key)

    return input_probes, output_probes
