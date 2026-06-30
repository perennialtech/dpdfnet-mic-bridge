"""Application entrypoint and top-level CLI dispatch."""

from __future__ import annotations

import sys
from typing import Optional

from .cli.argv import normalize_argv
from .cli.parser import build_parser
from .commands.devices import devices_command
from .commands.run import run_command
from .process_priority import boost_process_priority


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


def main(argv: Optional[list[str]] = None) -> int:
    boost_process_priority()

    parser = build_parser()

    try:
        args = parser.parse_args(normalize_argv(argv))

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
