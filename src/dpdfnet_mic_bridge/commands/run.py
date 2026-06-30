"""Implementation wrapper for the `run` command."""

from __future__ import annotations

import argparse

from ..bridge.runner import run_bridge
from ..dependencies import require_dpdfnet, require_native_audio


def run_command(args: argparse.Namespace) -> int:
    native_audio_mod = require_native_audio()
    dpdfnet_mod = require_dpdfnet()
    return run_bridge(
        args,
        native_audio_mod=native_audio_mod,
        dpdfnet_mod=dpdfnet_mod,
    )
