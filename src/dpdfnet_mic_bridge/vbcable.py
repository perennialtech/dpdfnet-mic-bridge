"""Windows-only helpers for the separately licensed, optional VB-CABLE driver."""

from __future__ import annotations

import ctypes
import hashlib
import sys
from ctypes import wintypes
from pathlib import Path

SETUP_SHA256 = "734c35dfa6d98f48782a451633ceb471166ec70d60482fd89a1123d0ee3c4f41"
SERVICE = r"SYSTEM\CurrentControlSet\Services\VBAudioVACMME"
SEE_MASK_NOCLOSEPROCESS = 0x00000040
WAIT_OBJECT_0 = 0
WAIT_TIMEOUT = 0x00000102


class SHELLEXECUTEINFOW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("fMask", wintypes.ULONG),
        ("hwnd", wintypes.HWND),
        ("lpVerb", wintypes.LPCWSTR),
        ("lpFile", wintypes.LPCWSTR),
        ("lpParameters", wintypes.LPCWSTR),
        ("lpDirectory", wintypes.LPCWSTR),
        ("nShow", ctypes.c_int),
        ("hInstApp", wintypes.HINSTANCE),
        ("lpIDList", ctypes.c_void_p),
        ("lpClass", wintypes.LPCWSTR),
        ("hkeyClass", wintypes.HKEY),
        ("dwHotKey", wintypes.DWORD),
        ("hIcon", wintypes.HANDLE),
        ("hProcess", wintypes.HANDLE),
    ]


def _winapi(dll, name, argtypes, restype):
    function = getattr(ctypes.WinDLL(dll, use_last_error=True), name)
    function.argtypes = argtypes
    function.restype = restype
    return function


class SetupProcess:
    """Own the handle to the elevated vendor GUI, without controlling its exit."""

    def __init__(self, handle):
        self.handle = handle
        self.untrackable = handle is None

    def running(self) -> bool:
        if self.handle is None:
            # ShellExecuteEx can succeed without a process handle. In that
            # case we cannot prove exit, so never launch a concurrent setup.
            return self.untrackable
        wait = _winapi(
            "kernel32",
            "WaitForSingleObject",
            [wintypes.HANDLE, wintypes.DWORD],
            wintypes.DWORD,
        )
        result = wait(self.handle, 0)
        if result == WAIT_TIMEOUT:
            return True
        if result != WAIT_OBJECT_0:
            raise OSError(
                f"Could not check VB-CABLE setup (error {ctypes.get_last_error()})."
            )
        self.close()
        return False

    def close(self) -> None:
        if self.handle is not None:
            close = _winapi("kernel32", "CloseHandle", [wintypes.HANDLE], wintypes.BOOL)
            if not close(self.handle):
                raise OSError(
                    f"Could not close VB-CABLE setup handle (error {ctypes.get_last_error()})."
                )
            self.handle = None


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


def launch_setup() -> SetupProcess:
    """Launch the genuine vendor GUI elevated and return its process handle."""
    if driver_installed():
        raise RuntimeError("VB-CABLE is already installed; restart Windows instead.")
    setup = bundled_setup()
    if setup is None:
        raise RuntimeError("The bundled VB-CABLE installer is missing or modified.")
    info = SHELLEXECUTEINFOW(
        cbSize=ctypes.sizeof(SHELLEXECUTEINFOW),
        fMask=SEE_MASK_NOCLOSEPROCESS,
        lpVerb="runas",
        lpFile=str(setup),
        lpDirectory=str(setup.parent),
        nShow=1,
    )
    launch = _winapi(
        "shell32", "ShellExecuteExW", [ctypes.POINTER(SHELLEXECUTEINFOW)], wintypes.BOOL
    )
    if not launch(ctypes.byref(info)):
        raise OSError(
            f"Windows could not launch VB-CABLE setup (error {ctypes.get_last_error()})."
        )
    return SetupProcess(info.hProcess)
