"""Lazy optional dependency loading."""

from __future__ import annotations

import importlib


def _require(package_name: str):
    try:
        return importlib.import_module(package_name)
    except ImportError as exc:
        raise RuntimeError(
            f"The '{package_name}' package is not installed.\n"
            "Install it with:\n"
            f"  pip install {package_name}"
        ) from exc


def require_sounddevice():
    return _require("sounddevice")


def require_dpdfnet():
    return _require("dpdfnet")
