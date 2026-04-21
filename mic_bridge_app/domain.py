"""
Shared data structures and small stateful helpers.

These types are deliberately lightweight and easy to reason about. Keeping them
centralized avoids rediscovering device-selection and callback-state behavior
across multiple files.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

import numpy as np

from .constants import (
    CALLBACK_RUNTIME_ALERT_FLAGS,
    CALLBACK_RUNTIME_ALERT_REPEAT_COUNT,
    CALLBACK_STATUS_FLAG_NAMES,
)


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
class DeviceSelection:
    input_device_id: int
    output_device_id: int
    input_was_auto_selected: bool
    output_was_auto_selected: bool
    linux_virtual_mic_was_auto_created: bool


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
    input_was_auto_selected: bool
    output_device_id: int
    output_device_name: str
    output_hostapi_name: str
    output_channels: int
    output_tags: tuple[str, ...]
    output_was_auto_selected: bool
    linux_virtual_mic_was_auto_created: bool
    latency: str


@dataclass
class LinuxVirtualMicLease:
    """
    Tracks a temporary Linux virtual mic module created for the current run.

    Only modules created by this process belong here. Pre-existing user-created
    targets stay outside this lease and must not be auto-unloaded.
    """

    module_id: int | None = None
    pactl_path: str | None = None


class AudioFifo:
    """
    Tiny float32 mono FIFO for callback-safe sample staging.

    Why this exists:
    `dpdfnet.StreamEnhancer.process()` may not always return exactly the same
    number of samples as the callback asked for. The FIFO lets us accept any
    returned chunk size without dropping or duplicating samples.

    Implementation note:
    The FIFO keeps queued chunks plus a read offset into the head chunk. Do not
    replace partial-consume handling with `chunk = chunk[take:]` in the callback
    path, that would turn every partial read into a fresh array allocation.
    """

    def __init__(self) -> None:
        self._chunks: deque[np.ndarray] = deque()
        self._head_index = 0
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
            available_in_head = int(chunk.size) - self._head_index
            take = min(count - write_pos, available_in_head)

            start = self._head_index
            end = start + take
            out[write_pos : write_pos + take] = chunk[start:end]

            write_pos += take
            self._size -= take
            self._head_index = end

            if self._head_index >= int(chunk.size):
                self._chunks.popleft()
                self._head_index = 0

        return out


class CallbackStatusCounter:
    """
    Collect callback status events for a shutdown summary and one-shot live
    alerts.

    We do not print inside the callback because printing in the audio callback
    is a bad real-time habit and can itself cause glitches.

    Live-alert policy:
    - first real-time issue: emit one marker
    - repeated real-time issues: emit one escalation marker
    - detailed per-flag counts still belong in the shutdown summary
    """

    def __init__(self) -> None:
        self.counts: dict[str, int] = {name: 0 for name in CALLBACK_STATUS_FLAG_NAMES}
        self._emitted_first_runtime_issue_alert = False
        self._emitted_repeated_runtime_issue_alert = False

    @property
    def runtime_issue_count(self) -> int:
        return sum(self.counts.get(name, 0) for name in CALLBACK_RUNTIME_ALERT_FLAGS)

    def observe(self, status) -> tuple[str, ...]:
        if not status:
            return tuple()

        previous_runtime_issue_count = self.runtime_issue_count

        for name in CALLBACK_STATUS_FLAG_NAMES:
            if bool(getattr(status, name, False)):
                self.counts[name] += 1

        alerts: list[str] = []
        current_runtime_issue_count = self.runtime_issue_count

        if (
            current_runtime_issue_count > previous_runtime_issue_count
            and not self._emitted_first_runtime_issue_alert
        ):
            self._emitted_first_runtime_issue_alert = True
            alerts.append("first-runtime-issue")

        if (
            current_runtime_issue_count >= CALLBACK_RUNTIME_ALERT_REPEAT_COUNT
            and not self._emitted_repeated_runtime_issue_alert
        ):
            self._emitted_repeated_runtime_issue_alert = True
            alerts.append("repeated-runtime-issues")

        return tuple(alerts)

    def nonzero_runtime_issue_counts(self) -> list[tuple[str, int]]:
        pairs: list[tuple[str, int]] = []
        for name in CALLBACK_RUNTIME_ALERT_FLAGS:
            count = self.counts.get(name, 0)
            if count:
                pairs.append((name, count))
        return pairs

    def nonzero_lines(self) -> list[str]:
        lines: list[str] = []
        for name in CALLBACK_STATUS_FLAG_NAMES:
            count = self.counts.get(name, 0)
            if count:
                lines.append(f"  - {name}: {count}")
        return lines
