"""Locate private PulseAudio clients without launching an audio server."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import sys


def client_path(name: str) -> str:
    if name not in {"pactl", "parec", "pacat"}:
        raise ValueError(f"Unknown audio client: {name}")
    if getattr(sys, "frozen", False):
        path = Path(sys._MEIPASS) / "pulse" / "bin" / name
        if not path.is_file():
            raise RuntimeError(
                f"The package is missing its {name} audio client. Reinstall the application."
            )
        return str(path)
    path = shutil.which(name)
    if path is None:
        raise RuntimeError(
            f"Source builds need the PulseAudio client utility '{name}'. Install pulseaudio-utils."
        )
    return path


def client_environment() -> dict[str, str]:
    env = dict(os.environ)
    env["LC_ALL"] = "C"
    if getattr(sys, "frozen", False):
        # Audio helpers use their own dependency tree, not PyInstaller's Qt and
        # Python libraries. Keep any pre-existing host library search path.
        private = Path(sys._MEIPASS) / "pulse" / "lib"
        original = env.get("LD_LIBRARY_PATH_ORIG", "")
        env["LD_LIBRARY_PATH"] = str(private) + (f":{original}" if original else "")
    return env
