"""
Top-level CLI dispatch.

Keep this file intentionally small. It owns parser construction, command
dispatch, and top-level error formatting. Command implementations live in
`listing.py` and `runtime.py`.
"""

from __future__ import annotations

import sys
from typing import Optional

from .cli import build_parser
from .listing import list_devices_command
from .runtime import run_command


def eprint(*args: object) -> None:
    print(*args, file=sys.stderr)


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

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
