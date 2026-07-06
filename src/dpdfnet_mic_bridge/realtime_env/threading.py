"""Realtime setup for the current Python worker thread."""

from __future__ import annotations

import ctypes
import platform
import threading

from .types import (AppliedAction, CheckResult, RuntimeTuningRequest,
                    ThreadTuningResult)

AVRT_PRIORITY_HIGH = 2

_thread_state = threading.local()


def _skipped(role: str, detail: str) -> ThreadTuningResult:
    return ThreadTuningResult(
        requested=False,
        thread_role=role,
        actions=(
            AppliedAction(
                id="thread.realtime",
                title="Worker thread realtime setup",
                status="skipped",
                detail=detail,
                evidence={},
            ),
        ),
    )


def _configure_windows_mmcss(
    request: RuntimeTuningRequest, role: str
) -> ThreadTuningResult:
    checks: list[CheckResult] = []
    actions: list[AppliedAction] = []

    try:
        avrt = ctypes.WinDLL("avrt", use_last_error=True)
        task_index = ctypes.c_ulong(0)
        handle = avrt.AvSetMmThreadCharacteristicsW(
            ctypes.c_wchar_p(request.windows_mmcss_task),
            ctypes.byref(task_index),
        )
        if not handle:
            error = ctypes.get_last_error()
            actions.append(
                AppliedAction(
                    id="windows.worker_mmcss",
                    title="Windows worker MMCSS",
                    status="failed",
                    detail=f"AvSetMmThreadCharacteristicsW('{request.windows_mmcss_task}') failed with error {error}",
                    evidence={"last_error": error, "task": request.windows_mmcss_task},
                )
            )
            return ThreadTuningResult(True, role, tuple(checks), tuple(actions))

        _thread_state.mmcss_handle = handle
        ok = avrt.AvSetMmThreadPriority(handle, AVRT_PRIORITY_HIGH)
        error = ctypes.get_last_error()
        actions.append(
            AppliedAction(
                id="windows.worker_mmcss",
                title="Windows worker MMCSS",
                status="applied" if ok else "failed",
                detail=(
                    f"configured MMCSS task '{request.windows_mmcss_task}'"
                    if ok
                    else f"AvSetMmThreadPriority(HIGH) failed with error {error}"
                ),
                evidence={
                    "last_error": error,
                    "task": request.windows_mmcss_task,
                    "priority": AVRT_PRIORITY_HIGH,
                },
            )
        )
    except Exception as exc:
        actions.append(
            AppliedAction(
                id="windows.worker_mmcss",
                title="Windows worker MMCSS",
                status="failed",
                detail=f"MMCSS setup failed: {exc}",
                evidence={"task": request.windows_mmcss_task},
            )
        )

    return ThreadTuningResult(True, role, tuple(checks), tuple(actions))


def close_thread_realtime_context() -> None:
    handle = getattr(_thread_state, "mmcss_handle", None)
    if handle:
        try:
            ctypes.WinDLL("avrt", use_last_error=True).AvRevertMmThreadCharacteristics(
                handle
            )
        finally:
            _thread_state.mmcss_handle = None


def configure_realtime_worker_thread(
    request: RuntimeTuningRequest,
) -> ThreadTuningResult:
    role = "realtime_worker"

    if request.mode == "off":
        return _skipped(role, "worker realtime setup disabled")

    system = platform.system()
    if system == "Linux":
        # action = linux.set_current_thread_realtime(request.linux_worker_rt_priority)
        # return ThreadTuningResult(True, role, actions=(action,))

        return _skipped(
            role, "Linux Python worker RT disabled to prevent SCHED_FIFO CPU starvation"
        )

    if system == "Windows":
        return _configure_windows_mmcss(request, role)

    return ThreadTuningResult(
        requested=False,
        thread_role=role,
        actions=(
            AppliedAction(
                id="thread.realtime",
                title="Worker thread realtime setup",
                status="not_applicable",
                detail=f"no realtime worker policy implemented for {system}",
                evidence={"system": system},
            ),
        ),
    )
