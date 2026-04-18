"""
Shared data structures and small stateful helpers.

These types are deliberately lightweight and easy to reason about. Keeping them
centralized avoids rediscovering callback-state behavior across multiple files.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

import numpy as np

from .constants import CALLBACK_STATUS_FLAG_NAMES


@dataclass(frozen=True)
class DeviceProbe:
    device_id: int
    direction: str
    name: str
    hostapi_name: str
    default_samplerate: float
    max_channels: int
    open_channels: int | None
    probe_failures: tuple[str, ...]
    tags: tuple[str, ...]


@dataclass(frozen=True)
class RunConfig:
    model_name: str
    sample_rate: int
    block_ms: float
    requested_block_ms: float | None
    block_policy: str
    block_size: int
    input_device_id: int
    input_device_name: str
    input_hostapi_name: str
    input_channels: int
    input_tags: tuple[str, ...]
    output_device_id: int
    output_device_name: str
    output_hostapi_name: str
    output_channels: int
    output_tags: tuple[str, ...]
    latency: str


class AudioFifo:
    """
    Tiny float32 mono FIFO for callback-safe sample staging.

    Why this exists:
    `dpdfnet.StreamEnhancer.process()` may not always return exactly the same
    number of samples as the callback asked for. The FIFO lets us accept any
    returned chunk size without dropping or duplicating samples.
    """

    def __init__(self) -> None:
        self._chunks: deque[np.ndarray] = deque()
        self._size = 0

    @property
    def available(self) -> int:
        return self._size

    def push(self, samples: np.ndarray) -> None:
        x = np.asarray(samples, dtype=np.float32).reshape(-1)
        if x.size == 0:
            return
        self._chunks.append(x)
        self._size += int(x.size)

    def pop(self, count: int) -> np.ndarray:
        """
        Pop exactly `count` samples.

        If the FIFO does not have enough data, the remainder is zero-padded.
        """
        if count <= 0:
            return np.zeros(0, dtype=np.float32)

        out = np.zeros(count, dtype=np.float32)
        write_pos = 0

        while write_pos < count and self._chunks:
            chunk = self._chunks[0]
            take = min(count - write_pos, int(chunk.size))
            out[write_pos : write_pos + take] = chunk[:take]

            if take == chunk.size:
                self._chunks.popleft()
            else:
                self._chunks[0] = chunk[take:]

            self._size -= take
            write_pos += take

        return out


class CallbackStatusCounter:
    """
    Collect callback status events for a shutdown summary.

    We do not print inside the callback because printing in the audio callback
    is a bad real-time habit and can itself cause glitches.
    """

    def __init__(self) -> None:
        self.counts: dict[str, int] = {name: 0 for name in CALLBACK_STATUS_FLAG_NAMES}

    def observe(self, status) -> None:
        if not status:
            return
        for name in CALLBACK_STATUS_FLAG_NAMES:
            if bool(getattr(status, name, False)):
                self.counts[name] += 1

    def nonzero_lines(self) -> list[str]:
        lines: list[str] = []
        for name in CALLBACK_STATUS_FLAG_NAMES:
            count = self.counts.get(name, 0)
            if count:
                lines.append(f"  - {name}: {count}")
        return lines
