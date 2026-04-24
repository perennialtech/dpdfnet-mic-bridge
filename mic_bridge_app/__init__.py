"""
Internal package for the mic bridge CLI.

Entry points:
- `python mic_bridge.py ...`
- `python -m mic_bridge_app ...`

No subcommand shows top-level help.
Use `run` to start the live bridge.
"""

from .app import main

__all__ = ["main"]
