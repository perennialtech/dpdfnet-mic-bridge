"""DPDFNet microphone bridge, with no audio initialization at import time."""

import os
from pathlib import Path
import sys

_dll_directories = []
if sys.platform == "win32" and not getattr(sys, "frozen", False):
    portaudio = os.environ.get("PORTAUDIO_DLL")
    if portaudio and hasattr(os, "add_dll_directory"):
        _dll_directories.append(
            os.add_dll_directory(str(Path(portaudio).resolve().parent))
        )


def main(argv=None):
    from .app import main as entrypoint

    return entrypoint(argv)


__all__ = ["main"]
