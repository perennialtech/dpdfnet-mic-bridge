"""Windows realtime environment helpers."""

from __future__ import annotations

import ctypes
import subprocess
from dataclasses import dataclass

from .types import (
    AppliedAction,
    CheckResult,
    PersistentApplyRequest,
    PersistentApplyResult,
)

HIGH_PRIORITY_CLASS = 0x00000080
AVRT_PRIORITY_HIGH = 2

SERVICE_COMMANDS = (
    ("MMCSS", ("sc", "config", "MMCSS", "start=", "auto")),
    ("MMCSS", ("sc", "start", "MMCSS")),
    ("Audiosrv", ("sc", "config", "Audiosrv", "start=", "auto")),
    ("Audiosrv", ("sc", "start", "Audiosrv")),
)

POWER_COMMANDS = (
    ("powercfg", "/setactive", "SCHEME_MIN"),
    (
        "powercfg",
        "/setacvalueindex",
        "SCHEME_CURRENT",
        "SUB_PROCESSOR",
        "PROCTHROTTLEMIN",
        "100",
    ),
    (
        "powercfg",
        "/setacvalueindex",
        "SCHEME_CURRENT",
        "SUB_PROCESSOR",
        "PROCTHROTTLEMAX",
        "100",
    ),
    ("powercfg", "/setacvalueindex", "SCHEME_CURRENT", "SUB_USB", "USBSELECTIVE", "0"),
    ("powercfg", "/S", "SCHEME_CURRENT"),
)


def _first_line(text: str) -> str:
    stripped = text.strip()
    return stripped.splitlines()[0] if stripped else ""


def _run(
    command: tuple[str, ...] | list[str], *, timeout: float = 10.0
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(command), capture_output=True, text=True, timeout=timeout
    )


def apply_process_priority() -> AppliedAction:
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = kernel32.GetCurrentProcess()
        ok = kernel32.SetPriorityClass(handle, HIGH_PRIORITY_CLASS)
        error = ctypes.get_last_error()
    except Exception as exc:
        return AppliedAction(
            id="windows.process_priority",
            title="Windows process priority",
            status="failed",
            detail=f"SetPriorityClass unavailable: {exc}",
            evidence={"requested_priority_class": HIGH_PRIORITY_CLASS},
        )

    return AppliedAction(
        id="windows.process_priority",
        title="Windows process priority",
        status="applied" if ok else "failed",
        detail=(
            "set HIGH_PRIORITY_CLASS"
            if ok
            else f"SetPriorityClass failed with error {error}"
        ),
        evidence={
            "process_handle": int(handle),
            "requested_priority_class": HIGH_PRIORITY_CLASS,
            "last_error": error,
        },
    )


@dataclass
class WindowsTimerResolution:
    milliseconds: int
    active: bool = False

    def begin(self) -> AppliedAction:
        try:
            winmm = ctypes.WinDLL("winmm", use_last_error=True)
            rc = winmm.timeBeginPeriod(int(self.milliseconds))
        except Exception as exc:
            return AppliedAction(
                id="windows.timer_resolution",
                title="Windows timer resolution",
                status="failed",
                detail=f"timeBeginPeriod unavailable: {exc}",
                evidence={"milliseconds": self.milliseconds},
            )

        self.active = rc == 0
        return AppliedAction(
            id="windows.timer_resolution",
            title="Windows timer resolution",
            status="applied" if rc == 0 else "failed",
            detail=(
                f"requested {self.milliseconds} ms timer resolution"
                if rc == 0
                else f"timeBeginPeriod failed with code {rc}"
            ),
            evidence={"milliseconds": self.milliseconds, "returncode": rc},
        )

    def end(self) -> None:
        if not self.active:
            return
        try:
            ctypes.WinDLL("winmm", use_last_error=True).timeEndPeriod(
                int(self.milliseconds)
            )
        finally:
            self.active = False


def _service_running(name: str) -> CheckResult:
    try:
        completed = _run(["sc", "query", name], timeout=5.0)
    except (OSError, subprocess.SubprocessError) as exc:
        return CheckResult(
            id=f"windows.service.{name.casefold()}",
            title=f"Windows {name} service",
            status="failed",
            severity="critical",
            detail=f"could not query service {name}: {exc}",
            fix="run `dpdfnet-mic-bridge realtime apply --yes` as Administrator",
            evidence={"service": name},
        )

    running = (
        completed.returncode == 0
        and "STATE" in completed.stdout
        and "RUNNING" in completed.stdout
    )
    return CheckResult(
        id=f"windows.service.{name.casefold()}",
        title=f"Windows {name} service",
        status="ok" if running else "failed",
        severity="info" if running else "critical",
        detail=f"{name} is running" if running else f"{name} is not running",
        fix=(
            None
            if running
            else "run `dpdfnet-mic-bridge realtime apply --yes` as Administrator"
        ),
        evidence={
            "service": name,
            "returncode": completed.returncode,
            "stdout": _first_line(completed.stdout),
            "stderr": _first_line(completed.stderr),
        },
    )


def check_mmcss_service() -> CheckResult:
    return _service_running("MMCSS")


def check_audio_service() -> CheckResult:
    return _service_running("Audiosrv")


def check_power_plan() -> CheckResult:
    try:
        completed = _run(["powercfg", "/getactivescheme"], timeout=5.0)
    except (OSError, subprocess.SubprocessError) as exc:
        return CheckResult(
            id="windows.power_plan",
            title="Windows power plan",
            status="warning",
            severity="warning",
            detail=f"could not query active power scheme: {exc}",
            fix="use High Performance or an equivalent low-latency power profile",
            evidence={},
        )

    output = completed.stdout.strip()
    high_performance = "High performance" in output or "SCHEME_MIN" in output
    return CheckResult(
        id="windows.power_plan",
        title="Windows power plan",
        status="ok" if high_performance else "warning",
        severity="info" if high_performance else "warning",
        detail=(
            "High Performance power plan is active"
            if high_performance
            else "active power plan may throttle audio processing"
        ),
        fix=(
            None
            if high_performance
            else "run `dpdfnet-mic-bridge realtime apply --yes` as Administrator"
        ),
        evidence={
            "returncode": completed.returncode,
            "stdout": _first_line(completed.stdout),
            "stderr": _first_line(completed.stderr),
        },
    )


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def build_persistent_command_plan(
    *, power: bool, services: bool
) -> list[tuple[str, ...]]:
    commands: list[tuple[str, ...]] = []
    if services:
        commands.extend(command for _, command in SERVICE_COMMANDS)
    if power:
        commands.extend(POWER_COMMANDS)
    return commands


def _run_persistent_command(command: tuple[str, ...]) -> AppliedAction:
    try:
        completed = _run(command, timeout=15.0)
    except (OSError, subprocess.SubprocessError) as exc:
        return AppliedAction(
            id="windows.persistent_command",
            title="Windows persistent realtime command",
            status="failed",
            detail=f"{' '.join(command)} failed: {exc}",
            evidence={"command": list(command)},
        )

    benign = (
        completed.returncode == 0
        or "already" in completed.stdout.casefold()
        or "already" in completed.stderr.casefold()
    )
    return AppliedAction(
        id="windows.persistent_command",
        title="Windows persistent realtime command",
        status="applied" if benign else "failed",
        detail=(
            f"ran {' '.join(command)}"
            if benign
            else f"{' '.join(command)} failed with {completed.returncode}"
        ),
        evidence={
            "command": list(command),
            "returncode": completed.returncode,
            "stdout": _first_line(completed.stdout),
            "stderr": _first_line(completed.stderr),
        },
    )


def apply_persistent_setup(request: PersistentApplyRequest) -> PersistentApplyResult:
    checks = (check_mmcss_service(), check_audio_service(), check_power_plan())
    commands = build_persistent_command_plan(
        power=request.windows_power, services=request.windows_services
    )

    if not request.yes:
        actions = tuple(
            AppliedAction(
                id="windows.persistent_command",
                title="Windows persistent realtime command",
                status="skipped",
                detail=f"would run {' '.join(command)}",
                evidence={"command": list(command)},
            )
            for command in commands
        )
        return PersistentApplyResult(checks=checks, actions=actions)

    if not is_admin():
        return PersistentApplyResult(
            checks=checks,
            actions=(
                AppliedAction(
                    id="windows.administrator",
                    title="Windows Administrator privileges",
                    status="failed",
                    detail="Persistent Windows setup requires Administrator.",
                    evidence={},
                ),
            ),
        )

    return PersistentApplyResult(
        checks=checks,
        actions=tuple(_run_persistent_command(command) for command in commands),
    )
