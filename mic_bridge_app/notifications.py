"""
Best-effort user-attention helpers for important live-run events.

This module owns *how* attention cues are delivered, not *when* they should be
emitted. Runtime code decides which live events deserve a cue; this module keeps
delivery centralized and low-risk.

Design goals:
- short, human-facing messages
- best-effort desktop/beep behavior without new hard dependencies
- notification failures must never hide the real terminal handoff
- safe to call from the main thread during a live run
- never call this from inside the audio callback
"""

from __future__ import annotations

import platform
import shutil
import subprocess
import sys


def _eprint(*args: object) -> None:
    print(*args, file=sys.stderr)


class RuntimeUserNotifier:
    """
    Emit concise attention cues for live-run events.

    Delivery strategy:
    - always print a short terminal notice on stderr
    - ring the terminal bell when attached to a TTY
    - on Linux, try `notify-send` if available
    - on Windows, try `winsound.MessageBeep()` if available

    All platform-specific cues are best-effort and intentionally ignored on
    failure. The terminal handoff remains the source of truth.
    """

    def __init__(self, *, app_name: str = "Mic bridge") -> None:
        self._app_name = app_name
        self._system = platform.system()
        self._notify_send_path = (
            shutil.which("notify-send") if self._system == "Linux" else None
        )

    def attention(
        self,
        *,
        title: str,
        body: str,
        urgency: str = "normal",
    ) -> None:
        self._print_attention(title=title, body=body)
        self._ring_terminal_bell()
        self._emit_platform_attention(title=title, body=body, urgency=urgency)

    def _print_attention(self, *, title: str, body: str) -> None:
        _eprint()
        _eprint(f"[attention] {title}")
        for line in body.strip().splitlines():
            _eprint(f"  {line}")
        _eprint()

    def _ring_terminal_bell(self) -> None:
        stream = sys.stderr
        try:
            is_tty = bool(getattr(stream, "isatty", lambda: False)())
        except Exception:
            is_tty = False

        if not is_tty:
            return

        try:
            print("\a", end="", file=stream, flush=True)
        except Exception:
            return

    def _emit_platform_attention(
        self,
        *,
        title: str,
        body: str,
        urgency: str,
    ) -> None:
        if self._system == "Linux":
            self._linux_notify_send(title=title, body=body, urgency=urgency)
            return

        if self._system == "Windows":
            self._windows_message_beep()

    def _linux_notify_send(self, *, title: str, body: str, urgency: str) -> None:
        if self._notify_send_path is None:
            return

        normalized_urgency = (
            urgency if urgency in {"low", "normal", "critical"} else "normal"
        )

        try:
            subprocess.Popen(
                [
                    self._notify_send_path,
                    "-a",
                    self._app_name,
                    "-u",
                    normalized_urgency,
                    title,
                    body,
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception:
            return

    def _windows_message_beep(self) -> None:
        try:
            import winsound
        except Exception:
            return

        try:
            winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
        except Exception:
            return
