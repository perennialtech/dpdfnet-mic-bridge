"""Backend latency hints and cleanup sessions."""

from __future__ import annotations

import math
import os
import platform
from contextlib import contextmanager
from types import TracebackType
from typing import Iterator

from ..audio.hostapi import hostapi_filter_key_for_name
from . import linux
from .types import (AppliedAction, RealtimeMode, RuntimeTuningRequest,
                    RuntimeTuningResult)


@contextmanager
def _temporary_env(values: dict[str, str | None]) -> Iterator[None]:
    previous: dict[str, str | None] = {key: os.environ.get(key) for key in values}
    try:
        for key, value in values.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _latency_env_values(
    *,
    hostapi_name: str,
    sample_rate: int,
    block_size: int,
    block_ms: float,
) -> dict[str, str]:
    hostapi_key = hostapi_filter_key_for_name(hostapi_name)
    values: dict[str, str] = {}

    if hostapi_key == "pipewire":
        values["PIPEWIRE_LATENCY"] = f"{block_size}/{sample_rate}"
    elif hostapi_key == "pulse":
        values["PULSE_LATENCY_MSEC"] = str(max(1, int(math.ceil(block_ms))))
        values["PIPEWIRE_LATENCY"] = f"{block_size}/{sample_rate}"

    return values


@contextmanager
def probe_backend_latency_context(
    *,
    hostapi_name: str,
    sample_rate: int,
    block_size: int,
    block_ms: float,
    mode: RealtimeMode,
) -> Iterator[None]:
    if mode == "off":
        yield
        return

    with _temporary_env(
        _latency_env_values(
            hostapi_name=hostapi_name,
            sample_rate=sample_rate,
            block_size=block_size,
            block_ms=block_ms,
        )
    ):
        yield


class BackendLatencySession:
    def __init__(self, request: RuntimeTuningRequest) -> None:
        self._request = request
        self._previous_env: dict[str, str | None] = {}
        self._pipewire_previous: dict[str, str | None] = {}
        self._restore_actions: list[AppliedAction] = []
        self._active = False

    def start(self) -> RuntimeTuningResult:
        if self._request.mode == "off":
            return RuntimeTuningResult(
                mode=self._request.mode,
                actions=(
                    AppliedAction(
                        id="backend_latency",
                        title="Backend latency session",
                        status="skipped",
                        detail="backend latency tuning disabled",
                        evidence={},
                    ),
                ),
            )

        if (
            self._request.hostapi_name is None
            or self._request.sample_rate is None
            or self._request.block_size is None
            or self._request.block_ms is None
        ):
            return RuntimeTuningResult(
                mode=self._request.mode,
                actions=(
                    AppliedAction(
                        id="backend_latency",
                        title="Backend latency session",
                        status="skipped",
                        detail="route-specific latency data is not available",
                        evidence={},
                    ),
                ),
            )

        values = _latency_env_values(
            hostapi_name=self._request.hostapi_name,
            sample_rate=self._request.sample_rate,
            block_size=self._request.block_size,
            block_ms=self._request.block_ms,
        )
        actions: list[AppliedAction] = []

        self._previous_env = {key: os.environ.get(key) for key in values}
        for key, value in values.items():
            os.environ[key] = value
            actions.append(
                AppliedAction(
                    id=f"backend.env.{key}",
                    title=f"Backend environment {key}",
                    status="applied",
                    detail=f"set {key}={value}",
                    evidence={"key": key, "value": value},
                )
            )

        hostapi_key = self._request.hostapi_key or hostapi_filter_key_for_name(
            self._request.hostapi_name
        )
        if platform.system() == "Linux" and hostapi_key == "pipewire":
            previous, capture_action = linux.capture_pipewire_metadata()
            actions.append(capture_action)
            if capture_action.status == "applied":
                self._pipewire_previous = previous
                actions.extend(
                    linux.apply_pipewire_quantum(
                        sample_rate=self._request.sample_rate,
                        block_size=self._request.block_size,
                    )
                )

        self._active = True
        return RuntimeTuningResult(mode=self._request.mode, actions=tuple(actions))

    def close(self) -> None:
        if not self._active:
            return

        for key, value in self._previous_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

        if self._pipewire_previous:
            self._restore_actions.extend(
                linux.restore_pipewire_metadata(self._pipewire_previous)
            )

        self._active = False

    @property
    def restore_actions(self) -> tuple[AppliedAction, ...]:
        return tuple(self._restore_actions)

    def __enter__(self) -> BackendLatencySession:
        self.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()
