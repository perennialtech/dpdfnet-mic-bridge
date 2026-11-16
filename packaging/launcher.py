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
