"""Best-effort process-priority setup for low-latency audio."""

from __future__ import annotations

import platform


def boost_process_priority() -> None:
    system = platform.system()

    if system == "Windows":
        import ctypes

        # 0x00000080 is HIGH_PRIORITY_CLASS.
        kernel32 = ctypes.windll.kernel32
        kernel32.SetPriorityClass(kernel32.GetCurrentProcess(), 0x00000080)

    elif system == "Linux":
        import os

        try:
            # -10 is high priority. Lower is higher priority.
            os.nice(-10)
        except PermissionError:
            # Fails silently without sufficient privileges.
            pass
