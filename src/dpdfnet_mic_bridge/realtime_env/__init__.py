"""OS-level runtime tuning for low-latency audio."""

from .errors import PersistentSetupError, RealtimeSetupError
from .types import (DEFAULT_LINUX_CALLBACK_RT_PRIORITY,
                    DEFAULT_LINUX_MEMLOCK_KB, DEFAULT_LINUX_NICE,
                    DEFAULT_LINUX_RTPRIO_LIMIT,
                    DEFAULT_LINUX_WORKER_RT_PRIORITY,
                    DEFAULT_WINDOWS_MMCSS_TASK, DEFAULT_WINDOWS_TIMER_MS,
                    REALTIME_MODE_CHOICES, AppliedAction, CheckResult,
                    PersistentApplyRequest, PersistentApplyResult,
                    RealtimeMode, RuntimeTuningRequest, RuntimeTuningResult,
                    ThreadTuningResult)

__all__ = [
    "AppliedAction",
    "CheckResult",
    "DEFAULT_LINUX_CALLBACK_RT_PRIORITY",
    "DEFAULT_LINUX_MEMLOCK_KB",
    "DEFAULT_LINUX_NICE",
    "DEFAULT_LINUX_RTPRIO_LIMIT",
    "DEFAULT_LINUX_WORKER_RT_PRIORITY",
    "DEFAULT_WINDOWS_MMCSS_TASK",
    "DEFAULT_WINDOWS_TIMER_MS",
    "PersistentApplyRequest",
    "PersistentApplyResult",
    "PersistentSetupError",
    "REALTIME_MODE_CHOICES",
    "RealtimeMode",
    "RealtimeSetupError",
    "RuntimeTuningRequest",
    "RuntimeTuningResult",
    "ThreadTuningResult",
]
