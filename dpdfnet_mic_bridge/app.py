"""CLI dispatch."""

from __future__ import annotations

import sys
from typing import Optional

from .cli import build_parser
from .listing import devices_command
from .runtime import run_command

_HELP_FLAGS = frozenset({"-h", "--help"})
_DEVICE_COMMAND_HINT_FLAGS = frozenset({"--all-apis", "--hostapi"})


def _looks_like_device_command_flag(arg: str) -> bool:
    if arg in _DEVICE_COMMAND_HINT_FLAGS:
        return True
    return any(arg.startswith(f"{flag}=") for flag in _DEVICE_COMMAND_HINT_FLAGS)


def eprint(*args: object) -> None:
    print(*args, file=sys.stderr)


def _print_exception_message(exc: BaseException) -> None:
    text = str(exc).strip()
    if not text:
        eprint("error: command failed.")
        return

    lines = text.splitlines()
    eprint(f"error: {lines[0]}")
    for line in lines[1:]:
        eprint(line)


def _normalize_argv(argv: Optional[list[str]]) -> list[str]:
    raw_args = list(sys.argv[1:] if argv is None else argv)

    if not raw_args:
        return ["run"]

    first = raw_args[0]
    if first in {"devices", "run"} or first in _HELP_FLAGS:
        return raw_args

    if any(_looks_like_device_command_flag(arg) for arg in raw_args):
        return ["devices", *raw_args]

    if first.startswith("-"):
        return ["run", *raw_args]

    return raw_args


def main(argv: Optional[list[str]] = None) -> int:
    import platform

    if platform.system() == "Windows":
        import ctypes

        # 0x00000080 is HIGH_PRIORITY_CLASS
        kernel32 = ctypes.windll.kernel32
        kernel32.SetPriorityClass(kernel32.GetCurrentProcess(), 0x00000080)
    elif platform.system() == "Linux":
        import os

        try:
            # -10 is high priority. Lower is higher priority.
            os.nice(-10)
        except PermissionError:
            # Fails silently if you forget 'sudo' or lack permissions
            pass

    parser = build_parser()

    try:
        args = parser.parse_args(_normalize_argv(argv))

        if args.command == "devices":
            return devices_command(args)
        if args.command == "run":
            return run_command(args)
    except KeyboardInterrupt:
        text = str(sys.exc_info()[1]).strip("\n")
        if text:
            for line in text.splitlines():
                eprint(line)
        else:
            eprint("Interrupted.")
        return 130
    except Exception as exc:
        _print_exception_message(exc)
        return 2

    eprint("Internal error: unhandled command.")
    return 2
