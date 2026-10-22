# packaging/launcher.py
from __future__ import annotations

import os
import ctypes
import io
import pathlib
import sys

error_output = None
if sys.platform == "win32" and sys.stderr is None:
    error_output = io.StringIO()
    sys.stderr = error_output


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
    dll_directories = []
    base = pathlib.Path(getattr(sys, "_MEIPASS", pathlib.Path(sys.executable).parent))
    candidates = [
        pathlib.Path(sys.executable).parent,
        base,
        base / "_internal",
    ]

    for candidate in candidates:
        if candidate.exists():
            dll_directories.append(os.add_dll_directory(str(candidate)))

from dpdfnet_mic_bridge.app import (  # noqa: E402
    main,
)

exit_code = main()
if exit_code and error_output is not None and error_output.getvalue().strip():
    message_box = ctypes.WinDLL("user32", use_last_error=True).MessageBoxW
    message_box.argtypes = [
        ctypes.c_void_p,
        ctypes.c_wchar_p,
        ctypes.c_wchar_p,
        ctypes.c_uint,
    ]
    message_box.restype = ctypes.c_int
    message_box(
        None, error_output.getvalue()[:4000], "DPDFNet Mic Bridge could not start", 0x10
    )
raise SystemExit(exit_code)
