"""Implementation wrapper for the `run` command."""

from __future__ import annotations

import argparse

import dpdfnet

from .. import _native_audio as native_audio_mod
from ..bridge.runner import run_bridge


def run_command(args: argparse.Namespace) -> int:
    return run_bridge(
        args,
        native_audio_mod=native_audio_mod,
        dpdfnet_mod=dpdfnet,
    )
