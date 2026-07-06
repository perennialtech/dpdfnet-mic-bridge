"""Linux realtime environment helpers."""

from __future__ import annotations

import ctypes
import getpass
import glob
import os
import re
import resource
import shutil
import subprocess
import tempfile
from pathlib import Path

from .types import (AppliedAction, CheckResult, PersistentApplyRequest,
                    PersistentApplyResult)

LIMITS_PATH = Path("/etc/security/limits.d/95-dpdfnet-mic-bridge.conf")
PIPEWIRE_METADATA_KEYS = (
    "clock.force-rate",
    "clock.force-quantum",
    "clock.min-quantum",
    "clock.max-quantum",
)

_MCL_CURRENT = 1
_MCL_FUTURE = 2


def _first_line(text: str) -> str:
    stripped = text.strip()
    return stripped.splitlines()[0] if stripped else ""


def _run(
    command: list[str], *, timeout: float = 2.0
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, capture_output=True, text=True, timeout=timeout)


def _rlimit_bytes(value: int) -> int | None:
    if value == resource.RLIM_INFINITY:
        return None
    return int(value)


def check_nice_limit(target_nice: int) -> CheckResult:
    try:
        current_nice = os.getpriority(os.PRIO_PROCESS, 0)
    except (AttributeError, OSError):
        current_nice = None

    try:
        soft, hard = resource.getrlimit(resource.RLIMIT_NICE)
    except (AttributeError, OSError) as exc:
        return CheckResult(
            id="linux.nice_limit",
            title="Linux nice priority limit",
            status="warning",
            severity="warning",
            detail=f"could not inspect RLIMIT_NICE: {exc}",
            fix="grant CAP_SYS_NICE or configure PAM limits if priority setup fails",
            evidence={"target_nice": target_nice, "current_nice": current_nice},
        )

    required_rlimit = max(0, 20 - target_nice)
    unlimited = soft == resource.RLIM_INFINITY
    sufficient = (
        unlimited
        or soft >= required_rlimit
        or (current_nice is not None and current_nice <= target_nice)
    )
    status = "ok" if sufficient else "failed"
    severity = "info" if sufficient else "critical"
    detail = (
        f"current nice {current_nice}, target {target_nice}"
        if current_nice is not None
        else f"target nice {target_nice}"
    )

    return CheckResult(
        id="linux.nice_limit",
        title="Linux nice priority limit",
        status=status,
        severity=severity,
        detail=detail,
        fix=(
            None
            if sufficient
            else "run `dpdfnet-mic-bridge realtime apply --yes` and start a new login session"
        ),
        evidence={
            "soft": soft,
            "hard": hard,
            "required_rlimit": required_rlimit,
            "target_nice": target_nice,
            "current_nice": current_nice,
        },
    )


def apply_process_priority(nice_value: int) -> AppliedAction:
    try:
        current = os.getpriority(os.PRIO_PROCESS, 0)
    except AttributeError:
        current = os.nice(0)
    except OSError as exc:
        return AppliedAction(
            id="linux.process_priority",
            title="Linux process priority",
            status="failed",
            detail=f"could not read current nice value: {exc}",
            evidence={"errno": getattr(exc, "errno", None), "target_nice": nice_value},
        )

    if current <= nice_value:
        return AppliedAction(
            id="linux.process_priority",
            title="Linux process priority",
            status="applied",
            detail=f"process already at nice {current}",
            evidence={"current_nice": current, "target_nice": nice_value},
        )

    try:
        if hasattr(os, "setpriority"):
            os.setpriority(os.PRIO_PROCESS, 0, nice_value)
        else:
            os.nice(nice_value - current)
        reached = (
            os.getpriority(os.PRIO_PROCESS, 0)
            if hasattr(os, "getpriority")
            else os.nice(0)
        )
    except PermissionError as exc:
        return AppliedAction(
            id="linux.process_priority",
            title="Linux process priority",
            status="failed",
            detail=f"permission denied while setting nice {nice_value}",
            evidence={
                "errno": exc.errno,
                "current_nice": current,
                "target_nice": nice_value,
            },
        )
    except OSError as exc:
        return AppliedAction(
            id="linux.process_priority",
            title="Linux process priority",
            status="failed",
            detail=f"could not set nice {nice_value}: {exc}",
            evidence={
                "errno": exc.errno,
                "current_nice": current,
                "target_nice": nice_value,
            },
        )

    status = "applied" if reached <= nice_value else "failed"
    return AppliedAction(
        id="linux.process_priority",
        title="Linux process priority",
        status=status,
        detail=f"process nice is {reached}",
        evidence={
            "previous_nice": current,
            "current_nice": reached,
            "target_nice": nice_value,
        },
    )


def check_memlock_limit(required_kb: int) -> CheckResult:
    required_bytes = int(required_kb) * 1024
    try:
        soft, hard = resource.getrlimit(resource.RLIMIT_MEMLOCK)
    except (AttributeError, OSError) as exc:
        return CheckResult(
            id="linux.memlock_limit",
            title="Linux memory lock limit",
            status="failed",
            severity="critical",
            detail=f"could not inspect RLIMIT_MEMLOCK: {exc}",
            fix="configure memlock through PAM limits or systemd user limits",
            evidence={"required_bytes": required_bytes},
        )

    soft_bytes = _rlimit_bytes(soft)
    hard_bytes = _rlimit_bytes(hard)
    sufficient = soft == resource.RLIM_INFINITY or soft >= required_bytes

    return CheckResult(
        id="linux.memlock_limit",
        title="Linux memory lock limit",
        status="ok" if sufficient else "failed",
        severity="info" if sufficient else "critical",
        detail=(
            "memlock soft limit is sufficient"
            if sufficient
            else f"memlock soft limit {soft_bytes} bytes is below required {required_bytes} bytes"
        ),
        fix=(
            None
            if sufficient
            else "run `dpdfnet-mic-bridge realtime apply --yes` and start a new login session"
        ),
        evidence={
            "soft_bytes": soft_bytes,
            "hard_bytes": hard_bytes,
            "required_bytes": required_bytes,
        },
    )


def lock_current_process_memory(required_kb: int) -> AppliedAction:
    if os.geteuid() == 0:
        return AppliedAction(
            id="linux.memory_lock",
            title="Linux memory locking",
            status="skipped",
            detail="skipped mlockall while running as root; run the bridge as a normal user after realtime apply",
            evidence={"effective_uid": 0},
        )

    required_bytes = int(required_kb) * 1024
    try:
        soft, hard = resource.getrlimit(resource.RLIMIT_MEMLOCK)
        if soft != resource.RLIM_INFINITY and soft < required_bytes:
            if hard == resource.RLIM_INFINITY:
                new_soft = required_bytes
            else:
                new_soft = min(hard, required_bytes)
            if new_soft > soft:
                resource.setrlimit(resource.RLIMIT_MEMLOCK, (new_soft, hard))
    except (ValueError, OSError) as exc:
        return AppliedAction(
            id="linux.memory_lock",
            title="Linux memory locking",
            status="failed",
            detail=f"could not raise RLIMIT_MEMLOCK: {exc}",
            evidence={
                "required_bytes": required_bytes,
                "errno": getattr(exc, "errno", None),
            },
        )

    try:
        libc = ctypes.CDLL("libc.so.6", use_errno=True)
        rc = libc.mlockall(_MCL_CURRENT)
        if rc != 0:
            err = ctypes.get_errno()
            return AppliedAction(
                id="linux.memory_lock",
                title="Linux memory locking",
                status="failed",
                detail=f"mlockall failed with errno {err}",
                evidence={"errno": err, "required_bytes": required_bytes},
            )
    except Exception as exc:
        return AppliedAction(
            id="linux.memory_lock",
            title="Linux memory locking",
            status="failed",
            detail=f"mlockall unavailable: {exc}",
            evidence={"required_bytes": required_bytes},
        )

    return AppliedAction(
        id="linux.memory_lock",
        title="Linux memory locking",
        status="applied",
        detail="locked current and future process memory",
        evidence={"required_bytes": required_bytes},
    )


def set_current_thread_realtime(priority: int) -> AppliedAction:
    class SchedParam(ctypes.Structure):
        _fields_ = [("sched_priority", ctypes.c_int)]

    try:
        libc = ctypes.CDLL("libc.so.6", use_errno=True)
        pthread_self = libc.pthread_self
        pthread_self.restype = ctypes.c_ulong
        pthread_setschedparam = libc.pthread_setschedparam
        pthread_setschedparam.argtypes = [
            ctypes.c_ulong,
            ctypes.c_int,
            ctypes.POINTER(SchedParam),
        ]
        pthread_setschedparam.restype = ctypes.c_int
        param = SchedParam(int(priority))
        rc = pthread_setschedparam(pthread_self(), os.SCHED_FIFO, ctypes.byref(param))
    except Exception as exc:
        return AppliedAction(
            id="linux.thread_realtime",
            title="Linux thread realtime scheduling",
            status="failed",
            detail=f"pthread_setschedparam unavailable: {exc}",
            evidence={"priority": priority},
        )

    if rc != 0:
        return AppliedAction(
            id="linux.thread_realtime",
            title="Linux thread realtime scheduling",
            status="failed",
            detail=f"SCHED_FIFO priority {priority} failed with errno {rc}",
            evidence={"errno": rc, "priority": priority},
        )

    return AppliedAction(
        id="linux.thread_realtime",
        title="Linux thread realtime scheduling",
        status="applied",
        detail=f"configured SCHED_FIFO priority {priority}",
        evidence={"priority": priority},
    )


def check_jack_block_size(block_size: int, period_frames: int | None) -> CheckResult:
    if period_frames is None or period_frames <= 0:
        return CheckResult(
            id="linux.jack_period",
            title="JACK period size",
            status="failed",
            severity="critical",
            detail="could not determine JACK graph period with jack_bufsize",
            fix="install JACK tools or use a non-JACK PortAudio host API",
            evidence={"block_size": block_size, "period_frames": period_frames},
        )

    ok = block_size % period_frames == 0
    return CheckResult(
        id="linux.jack_period",
        title="JACK period size",
        status="ok" if ok else "failed",
        severity="info" if ok else "critical",
        detail=(
            f"block size {block_size} is a JACK period multiple"
            if ok
            else f"block size {block_size} is not a multiple of JACK period {period_frames}"
        ),
        fix=(
            None
            if ok
            else "choose a block size that is an integer JACK period multiple"
        ),
        evidence={"block_size": block_size, "period_frames": period_frames},
    )


def capture_pipewire_metadata() -> tuple[dict[str, str | None], AppliedAction]:
    command = shutil.which("pw-metadata")
    if command is None:
        return {}, AppliedAction(
            id="linux.pipewire_metadata_capture",
            title="PipeWire metadata capture",
            status="failed",
            detail="pw-metadata was not found",
            evidence={},
        )

    try:
        completed = _run([command, "-n", "settings"], timeout=2.0)
    except (OSError, subprocess.SubprocessError) as exc:
        return {}, AppliedAction(
            id="linux.pipewire_metadata_capture",
            title="PipeWire metadata capture",
            status="failed",
            detail=f"could not run pw-metadata: {exc}",
            evidence={},
        )

    values: dict[str, str | None] = {key: None for key in PIPEWIRE_METADATA_KEYS}
    pattern = re.compile(r"key:'(?P<key>[^']+)'\s+value:'(?P<value>[^']*)'")
    for line in completed.stdout.splitlines():
        match = pattern.search(line)
        if match and match.group("key") in values:
            values[match.group("key")] = match.group("value")

    status = "applied" if completed.returncode == 0 else "failed"
    return values, AppliedAction(
        id="linux.pipewire_metadata_capture",
        title="PipeWire metadata capture",
        status=status,
        detail=(
            "captured PipeWire settings metadata"
            if status == "applied"
            else "pw-metadata query failed"
        ),
        evidence={
            "returncode": completed.returncode,
            "stdout": _first_line(completed.stdout),
            "stderr": _first_line(completed.stderr),
        },
    )


def set_pipewire_metadata_value(key: str, value: str | int) -> AppliedAction:
    command = shutil.which("pw-metadata")
    if command is None:
        return AppliedAction(
            id=f"linux.pipewire_metadata.{key}",
            title=f"PipeWire metadata {key}",
            status="failed",
            detail="pw-metadata was not found",
            evidence={"key": key, "value": value},
        )

    try:
        completed = _run([command, "-n", "settings", "0", key, str(value)], timeout=2.0)
    except (OSError, subprocess.SubprocessError) as exc:
        return AppliedAction(
            id=f"linux.pipewire_metadata.{key}",
            title=f"PipeWire metadata {key}",
            status="failed",
            detail=f"could not set {key}: {exc}",
            evidence={"key": key, "value": value},
        )

    return AppliedAction(
        id=f"linux.pipewire_metadata.{key}",
        title=f"PipeWire metadata {key}",
        status="applied" if completed.returncode == 0 else "failed",
        detail=(
            f"set {key}={value}"
            if completed.returncode == 0
            else f"failed to set {key}={value}"
        ),
        evidence={
            "key": key,
            "value": value,
            "returncode": completed.returncode,
            "stdout": _first_line(completed.stdout),
            "stderr": _first_line(completed.stderr),
        },
    )


def apply_pipewire_quantum(sample_rate: int, block_size: int) -> list[AppliedAction]:
    return [
        set_pipewire_metadata_value("clock.force-rate", sample_rate),
        set_pipewire_metadata_value("clock.force-quantum", block_size),
        set_pipewire_metadata_value("clock.min-quantum", block_size),
        set_pipewire_metadata_value("clock.max-quantum", block_size),
    ]


def restore_pipewire_metadata(previous: dict[str, str | None]) -> list[AppliedAction]:
    actions: list[AppliedAction] = []
    for key in PIPEWIRE_METADATA_KEYS:
        value = previous.get(key)
        actions.append(
            set_pipewire_metadata_value(key, "0" if value is None else value)
        )
    return actions


def check_cpu_governor() -> CheckResult:
    paths = glob.glob("/sys/devices/system/cpu/cpu*/cpufreq/scaling_governor")
    if not paths:
        return CheckResult(
            id="linux.cpu_governor",
            title="Linux CPU governor",
            status="not_applicable",
            severity="info",
            detail="CPU governor files are not visible",
            evidence={},
        )

    governors: dict[str, str] = {}
    for path in paths:
        try:
            governors[path] = Path(path).read_text(encoding="utf-8").strip()
        except OSError:
            governors[path] = "<unreadable>"

    non_performance = {
        path: value for path, value in governors.items() if value != "performance"
    }
    return CheckResult(
        id="linux.cpu_governor",
        title="Linux CPU governor",
        status="ok" if not non_performance else "warning",
        severity="info" if not non_performance else "warning",
        detail=(
            "CPU governor is performance"
            if not non_performance
            else "some CPUs are not using performance governor"
        ),
        fix=(
            None
            if not non_performance
            else "run `dpdfnet-mic-bridge realtime apply --yes` as root"
        ),
        evidence={"governors": governors},
    )


def apply_cpu_governor_performance() -> AppliedAction:
    if shutil.which("powerprofilesctl") is not None:
        try:
            completed = _run(["powerprofilesctl", "set", "performance"], timeout=5.0)
        except (OSError, subprocess.SubprocessError) as exc:
            return AppliedAction(
                id="linux.cpu_governor",
                title="Linux CPU governor",
                status="failed",
                detail=f"powerprofilesctl failed: {exc}",
                evidence={},
            )
        return AppliedAction(
            id="linux.cpu_governor",
            title="Linux CPU governor",
            status="applied" if completed.returncode == 0 else "failed",
            detail=(
                "requested performance power profile"
                if completed.returncode == 0
                else "powerprofilesctl failed"
            ),
            evidence={
                "command": "powerprofilesctl set performance",
                "returncode": completed.returncode,
                "stdout": _first_line(completed.stdout),
                "stderr": _first_line(completed.stderr),
            },
        )

    paths = glob.glob("/sys/devices/system/cpu/cpu*/cpufreq/scaling_governor")
    if not paths:
        return AppliedAction(
            id="linux.cpu_governor",
            title="Linux CPU governor",
            status="not_applicable",
            detail="CPU governor files are not visible",
            evidence={},
        )

    failures: list[str] = []
    for path in paths:
        try:
            Path(path).write_text("performance\n", encoding="utf-8")
        except OSError as exc:
            failures.append(f"{path}: {exc}")

    return AppliedAction(
        id="linux.cpu_governor",
        title="Linux CPU governor",
        status="applied" if not failures else "failed",
        detail=(
            "set CPU governors to performance"
            if not failures
            else "; ".join(failures[:3])
        ),
        evidence={"paths": paths, "failure_count": len(failures)},
    )


def render_pam_limits_content(
    username: str, *, rtprio: int, nice: int, memlock_kb: int
) -> str:
    return (
        "# Managed by dpdfnet-mic-bridge.\n"
        "# Grants the current user enough scheduling and memory-lock budget for low-latency audio.\n"
        f"{username} - rtprio {rtprio}\n"
        f"{username} - nice {nice}\n"
        f"{username} - memlock {memlock_kb}\n"
    )


def _target_username() -> str:
    return os.environ.get("SUDO_USER") or getpass.getuser()


def _write_managed_limits_file(content: str) -> AppliedAction:
    try:
        LIMITS_PATH.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(
            prefix=".95-dpdfnet-mic-bridge.", dir=str(LIMITS_PATH.parent), text=True
        )
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
        os.chmod(temp_name, 0o644)
        os.replace(temp_name, LIMITS_PATH)
        os.chmod(LIMITS_PATH, 0o644)
        read_back = LIMITS_PATH.read_text(encoding="utf-8")
    except OSError as exc:
        return AppliedAction(
            id="linux.pam_limits",
            title="Linux PAM realtime limits",
            status="failed",
            detail=f"could not write {LIMITS_PATH}: {exc}",
            evidence={"errno": exc.errno, "path": str(LIMITS_PATH)},
        )

    if read_back != content:
        return AppliedAction(
            id="linux.pam_limits",
            title="Linux PAM realtime limits",
            status="failed",
            detail=f"verification readback differed for {LIMITS_PATH}",
            evidence={"path": str(LIMITS_PATH)},
        )

    return AppliedAction(
        id="linux.pam_limits",
        title="Linux PAM realtime limits",
        status="applied",
        detail=f"wrote {LIMITS_PATH}; start a new login session for limits to apply",
        evidence={"path": str(LIMITS_PATH)},
    )


def apply_persistent_setup(request: PersistentApplyRequest) -> PersistentApplyResult:
    username = _target_username()
    content = render_pam_limits_content(
        username,
        rtprio=request.linux_rtprio,
        nice=request.linux_nice,
        memlock_kb=request.linux_memlock_kb,
    )
    checks = (check_cpu_governor(),)
    actions: list[AppliedAction] = []

    if not request.yes:
        actions.append(
            AppliedAction(
                id="linux.pam_limits",
                title="Linux PAM realtime limits",
                status="skipped",
                detail=f"would write {LIMITS_PATH}",
                evidence={"path": str(LIMITS_PATH), "content": content},
            )
        )
        actions.append(
            AppliedAction(
                id="linux.cpu_governor",
                title="Linux CPU governor",
                status="skipped",
                detail="would request performance CPU governor",
                evidence={},
            )
        )
        return PersistentApplyResult(checks=checks, actions=tuple(actions))

    if os.geteuid() != 0:
        actions.append(
            AppliedAction(
                id="linux.pam_limits",
                title="Linux PAM realtime limits",
                status="failed",
                detail="Persistent Linux setup requires root. Re-run with sudo.",
                evidence={"effective_uid": os.geteuid()},
            )
        )
        return PersistentApplyResult(checks=checks, actions=tuple(actions))

    actions.append(_write_managed_limits_file(content))
    actions.append(apply_cpu_governor_performance())
    return PersistentApplyResult(checks=checks, actions=tuple(actions))
