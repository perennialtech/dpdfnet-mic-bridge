"""
Optional dependency loading helpers.

The bridge intentionally fails with clear runtime errors if optional packages are
missing. That keeps imports friendly for tasks like static inspection, while
still giving users a specific installation message when they actually run a
command that needs the dependency.
"""

from __future__ import annotations

try:
    import sounddevice as sounddevice_module
except ImportError:
    sounddevice_module = None  # type: ignore[assignment]

try:
    import dpdfnet as dpdfnet_module
except ImportError:
    dpdfnet_module = None  # type: ignore[assignment]


def require_sounddevice():
    if sounddevice_module is None:
        raise RuntimeError(
            "The 'sounddevice' package is not installed.\n"
            "Install it with:\n"
            "  pip install sounddevice"
        )
    return sounddevice_module


def require_dpdfnet():
    if dpdfnet_module is None:
        raise RuntimeError(
            "The 'dpdfnet' package is not installed.\n"
            "Install it with:\n"
            "  pip install dpdfnet"
        )
    return dpdfnet_module
