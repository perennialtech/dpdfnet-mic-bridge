"""High-level realtime runtime manager used by the run command."""

from __future__ import annotations

import os
import platform
from contextlib import nullcontext
from dataclasses import replace
from types import TracebackType
from typing import TYPE_CHECKING, ContextManager

from ..audio.blocksize import jack_period_frames
from ..audio.hostapi import hostapi_filter_key_for_name
from . import linux, windows
from .backend_latency import (BackendLatencySession,
                              probe_backend_latency_context)
from .errors import RealtimeSetupError
from .types import (AppliedAction, CheckResult, RuntimeTuningRequest,
                    RuntimeTuningResult)

if TYPE_CHECKING:
    from ..bridge.types import RunConfig


class RealtimeRuntimeManager:
    def __init__(self, request: RuntimeTuningRequest) -> None:
        self.request = request
        self._route_request = request
        self._timer: windows.WindowsTimerResolution | None = None
        self._backend_session: BackendLatencySession | None = None
        self._results: list[RuntimeTuningResult] = []
        self._closed = False

    def apply_process_tuning(self) -> RuntimeTuningResult:
        if self.request.mode == "off":
            result = RuntimeTuningResult(
                mode=self.request.mode,
                actions=(
                    AppliedAction(
                        id="process_tuning",
                        title="Process realtime tuning",
                        status="skipped",
                        detail="realtime tuning disabled",
                        evidence={},
                    ),
                ),
            )
            self._results.append(result)
            return result

        system = platform.system()
        checks: list[CheckResult] = []
        actions: list[AppliedAction] = []

        if system == "Linux" and os.geteuid() == 0:
            result = RuntimeTuningResult(
                mode=self.request.mode,
                checks=(
                    CheckResult(
                        id="linux.runtime_root",
                        title="Linux runtime user",
                        status="failed" if self.request.mode == "strict" else "warning",
                        severity=(
                            "critical" if self.request.mode == "strict" else "warning"
                        ),
                        detail="the audio runtime is running as root",
                        fix="run `dpdfnet-mic-bridge realtime apply --yes` with sudo once, then run the bridge as a normal user",
                        evidence={"effective_uid": 0},
                    ),
                ),
                actions=(
                    AppliedAction(
                        id="linux.process_priority",
                        title="Linux process priority",
                        status="skipped",
                        detail="skipped runtime priority tuning while running as root",
                        evidence={"effective_uid": 0},
                    ),
                ),
            )
            self._results.append(result)
            return result

        if system == "Linux":
            checks.append(linux.check_nice_limit(self.request.linux_nice))
            actions.append(linux.apply_process_priority(self.request.linux_nice))
        elif system == "Windows":
            checks.extend(
                [
                    windows.check_mmcss_service(),
                    windows.check_audio_service(),
                    windows.check_power_plan(),
                ]
            )
            actions.append(windows.apply_process_priority())
            self._timer = windows.WindowsTimerResolution(self.request.windows_timer_ms)
            actions.append(self._timer.begin())
        else:
            actions.append(
                AppliedAction(
                    id="process_tuning",
                    title="Process realtime tuning",
                    status="not_applicable",
                    detail=f"no process realtime policy implemented for {system}",
                    evidence={"system": system},
                )
            )

        result = RuntimeTuningResult(
            mode=self.request.mode, checks=tuple(checks), actions=tuple(actions)
        )
        self._results.append(result)
        return result

    def apply_route_tuning(self, config: RunConfig) -> RuntimeTuningResult:
        hostapi_key = hostapi_filter_key_for_name(config.input_hostapi_name)
        self._route_request = replace(
            self.request,
            sample_rate=config.sample_rate,
            block_size=config.block_size,
            block_ms=config.block_ms,
            hostapi_name=config.input_hostapi_name,
            hostapi_key=hostapi_key,
            latency=config.latency,
        )

        checks: list[CheckResult] = []
        if platform.system() == "Linux" and hostapi_key == "jack":
            checks.append(
                linux.check_jack_block_size(config.block_size, jack_period_frames())
            )

        if self._backend_session is not None:
            self._backend_session.close()

        self._backend_session = BackendLatencySession(self._route_request)
        session_result = self._backend_session.start()
        result = RuntimeTuningResult(
            mode=self.request.mode,
            checks=tuple(checks) + session_result.checks,
            actions=session_result.actions,
        )
        self._results.append(result)
        return result

    def lock_memory_after_model_load(self) -> RuntimeTuningResult:
        if self.request.mode == "off":
            result = RuntimeTuningResult(
                mode=self.request.mode,
                actions=(
                    AppliedAction(
                        id="memory_lock",
                        title="Memory locking",
                        status="skipped",
                        detail="realtime tuning disabled",
                        evidence={},
                    ),
                ),
            )
            self._results.append(result)
            return result

        checks: list[CheckResult] = []
        actions: list[AppliedAction] = []
        if platform.system() == "Linux":
            checks.append(linux.check_memlock_limit(self.request.linux_memlock_kb))
            actions.append(
                linux.lock_current_process_memory(self.request.linux_memlock_kb)
            )
        else:
            actions.append(
                AppliedAction(
                    id="memory_lock",
                    title="Memory locking",
                    status="not_applicable",
                    detail=f"memory locking not implemented for {platform.system()}",
                    evidence={"system": platform.system()},
                )
            )

        result = RuntimeTuningResult(
            mode=self.request.mode, checks=tuple(checks), actions=tuple(actions)
        )
        self._results.append(result)
        return result

    def strict_raise_if_needed(self, result: RuntimeTuningResult, context: str) -> None:
        if self.request.mode != "strict":
            return

        failures: list[str] = []
        failures.extend(
            check.warning_line or check.detail for check in result.critical_failures
        )
        failures.extend(
            action.warning_line or action.detail for action in result.failed_actions
        )
        if failures:
            detail = "\n".join(f"  - {line}" for line in failures)
            raise RealtimeSetupError(
                f"{context} failed in strict realtime mode.\n{detail}"
            )

    def warning_lines(self) -> list[str]:
        lines: list[str] = []
        for result in self._results:
            lines.extend(f"realtime: {line}" for line in result.warnings)

        if self._backend_session is not None:
            for action in self._backend_session.restore_actions:
                line = action.warning_line
                if line is not None:
                    lines.append(f"realtime: {line}")

        return list(dict.fromkeys(lines))

    def native_callback_enabled(self) -> bool:
        return self.request.mode != "off"

    def linux_callback_priority(self) -> int:
        return self.request.linux_callback_rt_priority

    def worker_request(self) -> RuntimeTuningRequest:
        return self._route_request

    def probe_backend_latency_context(
        self,
        *,
        hostapi_name: str,
        sample_rate: int,
        block_size: int,
        block_ms: float,
    ) -> ContextManager[None]:
        if self.request.mode == "off":
            return nullcontext()
        return probe_backend_latency_context(
            hostapi_name=hostapi_name,
            sample_rate=sample_rate,
            block_size=block_size,
            block_ms=block_ms,
            mode=self.request.mode,
        )

    def close(self) -> None:
        if self._closed:
            return
        if self._backend_session is not None:
            self._backend_session.close()
        if self._timer is not None:
            self._timer.end()
        self._closed = True

    def __enter__(self) -> RealtimeRuntimeManager:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()
