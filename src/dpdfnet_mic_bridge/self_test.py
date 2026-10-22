"""Offline inference smoke test, without opening audio devices."""

from __future__ import annotations

import socket
from unittest.mock import patch


def run_self_test() -> int:
    def deny_network(*args, **kwargs):
        raise RuntimeError("The offline self-test attempted to use the network")

    # Block Python download paths as well as testing with an empty user cache.
    # CI additionally runs the Linux executable in a network namespace.
    with (
        patch.object(socket.socket, "connect", deny_network),
        patch.object(socket, "create_connection", deny_network),
    ):
        import numpy as np

        from .models.loading import load_enhancer

        enhancer = load_enhancer()
        rate = 48000
        samples = (0.02 * np.sin(2 * np.pi * 440 * np.arange(rate) / rate)).astype(
            np.float32
        )
        outputs = [
            enhancer.process(block, sample_rate=rate) for block in np.split(samples, 50)
        ]
        output = np.concatenate(outputs)
        if output.size < rate // 2 or not np.isfinite(output).all():
            raise RuntimeError("Offline inference returned invalid audio")
        print(
            f"Offline inference passed: {output.size} finite samples, CPU, bundled default model."
        )
    return 0
