"""
Internal implementation package for the mic bridge command-line tool.

Public entry points:
- `python mic_bridge.py ...`
- `python -m mic_bridge_app ...`

When no subcommand is provided, the launcher shows top-level help.

To run the live bridge explicitly, use the `run` subcommand.

Before changing behavior, read `ARCHITECTURE.md`.
"""

from .app import main

__all__ = ["main"]
