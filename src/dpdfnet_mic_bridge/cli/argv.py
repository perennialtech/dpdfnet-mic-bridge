"""Argument normalization before argparse dispatch."""

from __future__ import annotations

import sys
from typing import Optional

_HELP_FLAGS = frozenset({"-h", "--help"})
_DEVICE_COMMAND_HINT_FLAGS = frozenset({"--all-apis", "--hostapi"})


def _looks_like_device_command_flag(arg: str) -> bool:
    if arg in _DEVICE_COMMAND_HINT_FLAGS:
        return True
    return any(arg.startswith(f"{flag}=") for flag in _DEVICE_COMMAND_HINT_FLAGS)


def normalize_argv(argv: Optional[list[str]]) -> list[str]:
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
