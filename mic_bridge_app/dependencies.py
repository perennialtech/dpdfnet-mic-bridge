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


def _require(module, package_name: str):
    if module is None:
        raise RuntimeError(
            f"The '{package_name}' package is not installed.\n"
            "Install it with:\n"
            f"  pip install {package_name}"
        )
    return module


def require_sounddevice():
    return _require(sounddevice_module, "sounddevice")


def require_dpdfnet():
    return _require(dpdfnet_module, "dpdfnet")
