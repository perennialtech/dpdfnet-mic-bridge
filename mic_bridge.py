#!/usr/bin/env python3
"""
Thin entry point for the DPDFNet microphone bridge.

The implementation lives in `mic_bridge_app/`.
Running without arguments shows top-level help.
Use the explicit `run` subcommand to start the live bridge.

Read `README.md` for usage and `ARCHITECTURE.md` before changing
behavior.
"""

from mic_bridge_app.app import main

if __name__ == "__main__":
    raise SystemExit(main())
