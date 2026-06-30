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


def require_native_audio():
    try:
        return importlib.import_module("dpdfnet_mic_bridge._native_audio")
    except ImportError as exc:
        raise RuntimeError(
            "The native Rust/PortAudio audio engine is not installed or could not be loaded.\n"
            "Install Rust and PortAudio development files, then rebuild with:\n"
            "  uv sync\n"
            "On Linux this usually requires pkg-config and portaudio development headers."
        ) from exc


def require_dpdfnet():
    return _require("dpdfnet")
