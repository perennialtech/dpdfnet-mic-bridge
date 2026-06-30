"""Implementation wrapper for the `run` command."""

from __future__ import annotations

import argparse

from ..bridge.runner import run_bridge
from ..dependencies import require_dpdfnet, require_sounddevice


def run_command(args: argparse.Namespace) -> int:
    sd_mod = require_sounddevice()
    dpdfnet_mod = require_dpdfnet()
    return run_bridge(
        args,
        sd_mod=sd_mod,
        dpdfnet_mod=dpdfnet_mod,
    )
