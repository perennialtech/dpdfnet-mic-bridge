"""Audio callback block-size policy."""

from __future__ import annotations

import math
import platform

from .hostapi import hostapi_filter_key_for_name

DEFAULT_BLOCK_MS = 10.0

# On Linux/JACK, Python + PortAudio tends to behave more reliably when given a
# somewhat roomier exact block size instead of raw 10.0 ms.
LINUX_JACK_AUTO_MIN_BLOCK_MS = 20.0
LINUX_JACK_AUTO_MIN_BLOCK_FRAMES = 512


def next_power_of_two(value: int) -> int:
    if value <= 1:
        return 1
    return 1 << (value - 1).bit_length()


def jack_block_ms_for_frames(frames: int, sample_rate: int) -> float:
    return (float(frames) * 1000.0) / float(sample_rate)


def recommended_linux_jack_block_frames(sample_rate: int) -> int:
    """Return a conservative exact block size for Linux/JACK auto mode."""
    target_frames = max(
        int(math.ceil(sample_rate * (LINUX_JACK_AUTO_MIN_BLOCK_MS / 1000.0))),
        LINUX_JACK_AUTO_MIN_BLOCK_FRAMES,
    )
    return next_power_of_two(target_frames)


def resolve_effective_block_size(
    *,
    sample_rate: int,
    requested_block_ms: float | None,
    requested_block_frames: int | None,
    hostapi_name: str,
    system: str | None = None,
) -> tuple[int, float, str]:
    """
    Resolve the stream block size to an exact frame count.

    Returns:
        (block_size_frames, effective_block_ms, policy_name)

    Policy names:
    - explicit
    - explicit-frames
    - default-10ms
    - auto-linux-jack
    """
    current_system = platform.system() if system is None else system

    if requested_block_ms is not None and requested_block_frames is not None:
        raise ValueError("Use either --block-ms or --block-frames, not both.")

    if requested_block_frames is not None:
        if requested_block_frames <= 0:
            raise ValueError("--block-frames must be > 0.")
        block_size = int(requested_block_frames)
        effective_ms = jack_block_ms_for_frames(block_size, sample_rate)
        return block_size, effective_ms, "explicit-frames"

    if requested_block_ms is not None:
        if requested_block_ms <= 0:
            raise ValueError("--block-ms must be > 0.")
        block_size = int(round(sample_rate * (requested_block_ms / 1000.0)))
        if block_size <= 0:
            raise ValueError(
                f"--block-ms={requested_block_ms} produced invalid block_size={block_size}."
            )
        effective_ms = jack_block_ms_for_frames(block_size, sample_rate)
        return block_size, effective_ms, "explicit"

    hostapi_key = hostapi_filter_key_for_name(hostapi_name)
    if current_system == "Linux" and hostapi_key == "jack":
        block_size = recommended_linux_jack_block_frames(sample_rate)
        effective_ms = jack_block_ms_for_frames(block_size, sample_rate)
        return block_size, effective_ms, "auto-linux-jack"

    block_size = int(round(sample_rate * (DEFAULT_BLOCK_MS / 1000.0)))
    effective_ms = jack_block_ms_for_frames(block_size, sample_rate)
    return block_size, effective_ms, "default-10ms"
