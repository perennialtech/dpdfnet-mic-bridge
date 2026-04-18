"""
Internal implementation package for the mic bridge command-line tool.

Public entry points:
- `python mic_bridge.py ...`
- `python -m mic_bridge_app ...`

Before changing behavior, read `ARCHITECTURE.md`.
"""

from .app import main

__all__ = ["main"]
