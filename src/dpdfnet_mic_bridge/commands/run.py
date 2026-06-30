"""Implementation wrapper for the `run` command."""

from __future__ import annotations

import argparse

from ..bridge.runner import run_bridge


def run_command(args: argparse.Namespace) -> int:
    return run_bridge(args)
