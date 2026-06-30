"""Automatic audio callback block-size policy."""

from __future__ import annotations

import math
import platform
import shutil
import subprocess
from collections.abc import Iterator

from .hostapi import hostapi_filter_key_for_name

INITIAL_BLOCK_MS = 20.0
MAX_AUTO_BLOCK_MS = 100.0
AUTO_GROWTH_MS = 10.0


def next_power_of_two(value: int) -> int:
    if value <= 1:
        return 1
    return 1 << (value - 1).bit_length()


def block_ms_for_frames(frames: int, sample_rate: int) -> float:
    return (float(frames) * 1000.0) / float(sample_rate)


def frames_for_ms(ms: float, sample_rate: int) -> int:
    return max(1, int(round(sample_rate * (ms / 1000.0))))


def jack_period_frames() -> int | None:
    """
    Return the running JACK graph period size when jack_bufsize is available.

    JACK users should run the bridge at an integer multiple of the actual graph
    period. Guessing a power-of-two period is explicitly avoided here.
    """
    command = shutil.which("jack_bufsize")
    if command is None:
        return None

    try:
        completed = subprocess.run(
            [command],
            check=True,
            capture_output=True,
            text=True,
            timeout=1.0,
        )
    except (OSError, subprocess.SubprocessError):
        return None

    text = completed.stdout.strip().splitlines()
    if not text:
        return None

    try:
        value = int(text[-1].strip())
    except ValueError:
        return None

    return value if value > 0 else None


def iter_auto_block_sizes(
    *,
    sample_rate: int,
    hostapi_name: str,
    system: str | None = None,
) -> Iterator[tuple[int, float, str]]:
    """
    Yield automatic block-size candidates.

    Policy:
    - start at 20 ms;
    - grow in bounded increments when exact stream probing fails;
    - never accept user-provided block sizes;
    - for JACK, use an integer multiple of the actual graph period.
    """
    current_system = platform.system() if system is None else system
    hostapi_key = hostapi_filter_key_for_name(hostapi_name)

    if current_system == "Linux" and hostapi_key == "jack":
        period = jack_period_frames()
        if period is None:
            raise ValueError(
                "JACK block-size validation requires `jack_bufsize`, but it was not available. "
                "Install JACK tools or use a non-JACK PortAudio host API."
            )

        minimum_frames = frames_for_ms(INITIAL_BLOCK_MS, sample_rate)
        maximum_frames = frames_for_ms(MAX_AUTO_BLOCK_MS, sample_rate)
        multiplier = max(1, math.ceil(minimum_frames / period))

        while multiplier * period <= maximum_frames:
            frames = multiplier * period
            yield frames, block_ms_for_frames(frames, sample_rate), "auto-jack-period"
            multiplier += 1

        return

    block_ms = INITIAL_BLOCK_MS
    while block_ms <= MAX_AUTO_BLOCK_MS + 1e-9:
        frames = frames_for_ms(block_ms, sample_rate)
        yield frames, block_ms_for_frames(frames, sample_rate), "auto-20ms-grow"
        block_ms += AUTO_GROWTH_MS
