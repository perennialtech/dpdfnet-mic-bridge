"""Windows-only helpers for the separately licensed, optional VB-CABLE driver."""

from __future__ import annotations

import ctypes
import hashlib
import sys
from pathlib import Path

SETUP_SHA256 = "734c35dfa6d98f48782a451633ceb471166ec70d60482fd89a1123d0ee3c4f41"
SERVICE = r"SYSTEM\CurrentControlSet\Services\VBAudioVACMME"


def driver_installed() -> bool:
    """Check the vendor's kernel service; absent audio endpoints can be temporary."""
    if sys.platform != "win32":
        return False
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, SERVICE):
            return True
    except FileNotFoundError:
        return False


def bundled_setup() -> Path | None:
    """Only offer the original setup program shipped inside the frozen installer."""
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        return None
    path = Path(sys.executable).parent / "vendor/vbcable/VBCABLE_Setup_x64.exe"
    if not path.is_file():
        return None
    if hashlib.sha256(path.read_bytes()).hexdigest() != SETUP_SHA256:
        return None
    return path


def launch_setup() -> None:
    """Launch the genuine vendor GUI with an explicit Windows UAC prompt."""
    if driver_installed():
        raise RuntimeError("VB-CABLE is already installed; restart Windows instead.")
    setup = bundled_setup()
    if setup is None:
        raise RuntimeError("The bundled VB-CABLE installer is missing or modified.")
    result = ctypes.windll.shell32.ShellExecuteW(
        None, "runas", str(setup), None, str(setup.parent), 1
    )
    if result <= 32:
        raise OSError(f"Windows could not launch VB-CABLE setup (error {result}).")
