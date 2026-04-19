"""
Top-level CLI dispatch.

Keep this file intentionally small. It owns parser construction, convenience
defaulting to the `run` workflow when no subcommand is provided, command
dispatch, and top-level error formatting. Command implementations live in
`listing.py` and `runtime.py`.
"""

from __future__ import annotations

import sys
from typing import Optional

from .cli import build_parser
from .listing import list_devices_command
from .runtime import run_command

_COMMAND_NAMES = frozenset({"list-devices", "run"})


def eprint(*args: object) -> None:
    print(*args, file=sys.stderr)


def _normalize_argv(argv: Optional[list[str]]) -> list[str]:
    raw_args = list(sys.argv[1:] if argv is None else argv)

    if not raw_args:
        return ["run"]

    first = raw_args[0]
    if first in _COMMAND_NAMES or first in {"-h", "--help"}:
        return raw_args

    return ["run", *raw_args]


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(_normalize_argv(argv))

    try:
        if args.command == "list-devices":
            return list_devices_command(args)
        if args.command == "run":
            return run_command(args)
    except Exception as exc:
        eprint()
        eprint(f"Error: {exc}")
        eprint()
        return 2

    eprint("Internal error: unhandled command.")
    return 2
