"""Application entrypoint and top-level CLI dispatch."""

from __future__ import annotations

import sys
from typing import Optional

from .cli.argv import normalize_argv
from .cli.parser import build_parser


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
    try:
        raw = list(sys.argv[1:] if argv is None else argv)
        if not raw or raw == ["desktop"]:
            from .desktop import desktop_main

            return desktop_main()
        if raw[0].startswith("_desktop-"):
            from .desktop_process import internal_command

            return internal_command(raw)
        parser = build_parser()
        args = parser.parse_args(normalize_argv(argv))

        if args.command == "devices":
            from .commands.devices import devices_command

            return devices_command(args)
        if args.command == "run":
            from .commands.run import run_command

            return run_command(args)
        if args.command == "realtime":
            from .realtime_env.cli import realtime_command

            return realtime_command(args)
        if args.command == "self-test":
            from .self_test import run_self_test

            return run_self_test()
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
