"""Structured data shared by realtime environment helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

REALTIME_MODE_CHOICES = ("auto", "strict", "off")

DEFAULT_LINUX_NICE = -10
DEFAULT_LINUX_WORKER_RT_PRIORITY = 10
DEFAULT_LINUX_CALLBACK_RT_PRIORITY = 20
DEFAULT_LINUX_RTPRIO_LIMIT = 30
DEFAULT_LINUX_MEMLOCK_KB = 1_048_576

DEFAULT_WINDOWS_TIMER_MS = 1
DEFAULT_WINDOWS_MMCSS_TASK = "Pro Audio"

RealtimeMode = Literal["auto", "strict", "off"]
CheckStatus = Literal["ok", "warning", "failed", "skipped", "not_applicable"]
Severity = Literal["info", "warning", "critical"]
ActionStatus = Literal["applied", "failed", "skipped", "not_applicable"]


@dataclass(frozen=True)
class CheckResult:
    id: str
    title: str
    status: CheckStatus
    severity: Severity
    detail: str
    fix: str | None = None
    evidence: dict[str, object] = field(default_factory=dict)

    @property
    def is_problem(self) -> bool:
        return self.status in {"warning", "failed"}

    @property
    def is_critical_failure(self) -> bool:
        return self.status == "failed" and self.severity == "critical"

    @property
    def warning_line(self) -> str | None:
        if not self.is_problem:
            return None
        suffix = f"; {self.fix}" if self.fix else ""
        return f"{self.id}: {self.detail}{suffix}"


@dataclass(frozen=True)
class AppliedAction:
    id: str
    title: str
    status: ActionStatus
    detail: str
    evidence: dict[str, object] = field(default_factory=dict)

    @property
    def failed(self) -> bool:
        return self.status == "failed"

    @property
    def warning_line(self) -> str | None:
        if self.status != "failed":
            return None
        return f"{self.id}: {self.detail}"


@dataclass(frozen=True)
class RuntimeTuningRequest:
    mode: RealtimeMode
    sample_rate: int | None = None
    block_size: int | None = None
    block_ms: float | None = None
    hostapi_name: str | None = None
    hostapi_key: str | None = None
    latency: str | None = None
    linux_nice: int = DEFAULT_LINUX_NICE
    linux_worker_rt_priority: int = DEFAULT_LINUX_WORKER_RT_PRIORITY
    linux_callback_rt_priority: int = DEFAULT_LINUX_CALLBACK_RT_PRIORITY
    linux_memlock_kb: int = DEFAULT_LINUX_MEMLOCK_KB
    windows_timer_ms: int = DEFAULT_WINDOWS_TIMER_MS
    windows_mmcss_task: str = DEFAULT_WINDOWS_MMCSS_TASK


@dataclass(frozen=True)
class RuntimeTuningResult:
    mode: RealtimeMode
    checks: tuple[CheckResult, ...] = ()
    actions: tuple[AppliedAction, ...] = ()

    @property
    def warnings(self) -> tuple[str, ...]:
        lines: list[str] = []
        for check in self.checks:
            line = check.warning_line
            if line is not None:
                lines.append(line)
        for action in self.actions:
            line = action.warning_line
            if line is not None:
                lines.append(line)
        return tuple(lines)

    @property
    def critical_failures(self) -> tuple[CheckResult, ...]:
        return tuple(check for check in self.checks if check.is_critical_failure)

    @property
    def failed_actions(self) -> tuple[AppliedAction, ...]:
        return tuple(action for action in self.actions if action.failed)

    @property
    def ok(self) -> bool:
        return not self.critical_failures and not self.failed_actions


@dataclass(frozen=True)
class ThreadTuningResult:
    requested: bool
    thread_role: str
    checks: tuple[CheckResult, ...] = ()
    actions: tuple[AppliedAction, ...] = ()

    @property
    def ok(self) -> bool:
        if not self.requested:
            return True
        return not any(check.is_critical_failure for check in self.checks) and not any(
            action.failed for action in self.actions
        )

    @property
    def warning_lines(self) -> tuple[str, ...]:
        lines: list[str] = []
        for check in self.checks:
            line = check.warning_line
            if line is not None:
                lines.append(f"{self.thread_role}: {line}")
        for action in self.actions:
            line = action.warning_line
            if line is not None:
                lines.append(f"{self.thread_role}: {line}")
        return tuple(lines)


@dataclass(frozen=True)
class PersistentApplyRequest:
    yes: bool
    linux_rtprio: int = DEFAULT_LINUX_RTPRIO_LIMIT
    linux_nice: int = DEFAULT_LINUX_NICE
    linux_memlock_kb: int = DEFAULT_LINUX_MEMLOCK_KB
    windows_power: bool = True
    windows_services: bool = True


@dataclass(frozen=True)
class PersistentApplyResult:
    checks: tuple[CheckResult, ...] = ()
    actions: tuple[AppliedAction, ...] = ()

    @property
    def warnings(self) -> tuple[str, ...]:
        lines: list[str] = []
        for check in self.checks:
            line = check.warning_line
            if line is not None:
                lines.append(line)
        for action in self.actions:
            line = action.warning_line
            if line is not None:
                lines.append(line)
        return tuple(lines)

    @property
    def critical_failures(self) -> tuple[CheckResult, ...]:
        return tuple(check for check in self.checks if check.is_critical_failure)

    @property
    def failed_actions(self) -> tuple[AppliedAction, ...]:
        return tuple(action for action in self.actions if action.failed)

    @property
    def ok(self) -> bool:
        return not self.critical_failures and not self.failed_actions
