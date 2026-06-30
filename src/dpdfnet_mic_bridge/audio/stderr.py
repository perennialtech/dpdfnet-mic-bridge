"""Stderr manipulation for noisy native libraries."""

from __future__ import annotations

import os
import sys
from contextlib import contextmanager


@contextmanager
def silence_native_stderr():
    """
    Hide noisy native diagnostics.

    ALSA/PortAudio can write directly to fd 2 while native check calls try
    devices that are expected to fail or during PortAudio initialization. The
    Python exception is still captured and shown in the normal UI; the raw native
    spam is not useful.
    """
    try:
        stderr_fd = sys.stderr.fileno()
        saved_fd = os.dup(stderr_fd)
        devnull_fd = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull_fd, stderr_fd)
    except (AttributeError, OSError, ValueError):
        yield
        return

    try:
        yield
    finally:
        try:
            os.dup2(saved_fd, stderr_fd)
        finally:
            os.close(saved_fd)
            os.close(devnull_fd)
