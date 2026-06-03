"""Lazy dependency loading."""

from __future__ import annotations

import importlib


def _require(package_name: str):
    try:
        return importlib.import_module(package_name)
    except ImportError as exc:
        raise RuntimeError(
            f"The '{package_name}' package is not installed.\n"
            "Install the project dependencies with:\n"
            "  uv sync"
        ) from exc


def require_sounddevice():
    return _require("sounddevice")


def require_dpdfnet():
    return _require("dpdfnet")
