"""
Internal package for the mic bridge CLI.

Entry points:
- `python mic_bridge.py ...`
- `python -m mic_bridge_app ...`

No arguments start the live bridge.
Use `--help` for help.
"""

from .app import main

__all__ = ["main"]
