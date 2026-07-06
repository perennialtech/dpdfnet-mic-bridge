# packaging/launcher.py
from __future__ import annotations

import os
import pathlib
import sys


def _cache_root() -> pathlib.Path:
    if sys.platform == "win32":
        root = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        if root:
            return pathlib.Path(root) / "dpdfnet-mic-bridge"

    if sys.platform == "darwin":
        return pathlib.Path.home() / "Library" / "Caches" / "dpdfnet-mic-bridge"

    root = os.environ.get("XDG_CACHE_HOME", str(pathlib.Path.home() / ".cache"))
    return pathlib.Path(root) / "dpdfnet-mic-bridge"


cache_root = _cache_root()
numba_cache = cache_root / "numba"
numba_cache.mkdir(parents=True, exist_ok=True)

os.environ.setdefault("NUMBA_CACHE_DIR", str(numba_cache))

if sys.platform == "win32" and hasattr(os, "add_dll_directory"):
    base = pathlib.Path(getattr(sys, "_MEIPASS", pathlib.Path(sys.executable).parent))
    candidates = [
        pathlib.Path(sys.executable).parent,
        base,
        base / "_internal",
    ]

    for candidate in candidates:
        if candidate.exists():
            os.add_dll_directory(str(candidate))

from dpdfnet_mic_bridge.app import main

raise SystemExit(main())
