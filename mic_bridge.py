#!/usr/bin/env python3
"""
Thin entry point for the DPDFNet microphone bridge.

The implementation lives in `mic_bridge_app/`.
Running without arguments starts the live bridge.
Use `--help` for help.
"""

from mic_bridge_app.app import main

if __name__ == "__main__":
    raise SystemExit(main())
