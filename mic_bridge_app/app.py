"""
Top-level CLI dispatch.

Keep this file intentionally small. It owns parser construction, light argv
normalization for help and subcommands, command dispatch, and top-level error
formatting. Command implementations live in `listing.py` and `runtime.py`.
"""

from __future__ import annotations

import sys
from typing import Optional

from .cli import build_parser
from .listing import list_devices_command
from .runtime import run_command

_COMMAND_NAMES = frozenset({"list-devices", "run"})
_HELP_FLAGS = frozenset({"-h", "--help"})


def eprint(*args: object) -> None:
    print(*args, file=sys.stderr)


def _print_exception_message(exc: BaseException) -> None:
    text = str(exc).strip("\n")
    if not text:
        eprint("Error.")
        return

    lines = text.splitlines()
    eprint(f"Error: {lines[0]}")
    for line in lines[1:]:
        eprint(line)


def _normalize_argv(argv: Optional[list[str]]) -> list[str]:
    raw_args = list(sys.argv[1:] if argv is None else argv)

    if not raw_args:
        return ["--help"]

    first = raw_args[0]
    if first in _COMMAND_NAMES or first in _HELP_FLAGS:
        return raw_args

    return ["run", *raw_args]


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()

    try:
        args = parser.parse_args(_normalize_argv(argv))

        if args.command == "list-devices":
            return list_devices_command(args)
        if args.command == "run":
            return run_command(args)
    except KeyboardInterrupt:
        eprint()
        eprint("Interrupted.")
        eprint()
        return 130
    except Exception as exc:
        eprint()
        _print_exception_message(exc)
        eprint()
        return 2

    eprint("Internal error: unhandled command.")
    return 2
