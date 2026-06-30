"""Best-effort realtime/thread-priority helpers."""

from __future__ import annotations

import ctypes
import os
import platform


def configure_realtime_worker_thread() -> list[str]:
    system = platform.system()
    if system == "Windows":
        return _configure_windows_mmcss()
    if system == "Linux":
        return _configure_linux_realtime()
    return [f"no realtime worker policy implemented for {system}"]


def _configure_windows_mmcss() -> list[str]:
    warnings: list[str] = []
    try:
        avrt = ctypes.WinDLL("avrt")
        task_index = ctypes.c_ulong(0)
        handle = avrt.AvSetMmThreadCharacteristicsW(
            ctypes.c_wchar_p("Pro Audio"),
            ctypes.byref(task_index),
        )
        if not handle:
            warnings.append("MMCSS AvSetMmThreadCharacteristicsW('Pro Audio') failed")
        else:
            # AVRT_PRIORITY_HIGH = 2
            if not avrt.AvSetMmThreadPriority(handle, 2):
                warnings.append("MMCSS AvSetMmThreadPriority(HIGH) failed")
    except Exception as exc:
        warnings.append(f"MMCSS setup failed: {exc}")

    return warnings


def _configure_linux_realtime() -> list[str]:
    warnings: list[str] = []

    try:
        libc = ctypes.CDLL("libc.so.6", use_errno=True)
        # MCL_CURRENT | MCL_FUTURE
        if libc.mlockall(1 | 2) != 0:
            errno = ctypes.get_errno()
            warnings.append(
                f"mlockall failed with errno {errno}; check memlock/realtime limits"
            )
    except Exception as exc:
        warnings.append(f"mlockall unavailable: {exc}")

    try:
        os.sched_setscheduler(
            0,
            os.SCHED_FIFO,
            os.sched_param(10),
        )
    except PermissionError:
        warnings.append(
            "SCHED_FIFO worker priority unavailable; grant CAP_SYS_NICE, configure rtkit, "
            "or raise realtime limits"
        )
    except AttributeError:
        warnings.append("SCHED_FIFO unavailable on this Python/Linux build")
    except OSError as exc:
        warnings.append(f"SCHED_FIFO setup failed: {exc}")

    return warnings
