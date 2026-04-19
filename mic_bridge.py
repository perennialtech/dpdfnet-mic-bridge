#!/usr/bin/env python3
"""
Thin command-line entry point for the Version 0 DPDFNet microphone bridge.

Implementation details are kept in `mic_bridge_app/` so CLI parsing, device
probing, runtime audio behavior, and architectural rationale can stay
separated.

Running without a subcommand defaults to the `run` workflow, which tries to
auto-select a safe device pair and hands off cleanly to the user if it cannot.

Read `README.md` for user guidance and `ARCHITECTURE.md` before changing code
structure or runtime behavior.
"""

from mic_bridge_app.app import main

if __name__ == "__main__":
    raise SystemExit(main())
