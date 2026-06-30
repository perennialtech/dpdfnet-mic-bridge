"""Background DPDFNet enhancement worker for the native audio engine."""

from __future__ import annotations

import threading
from typing import Any

import numpy as np


class EnhancementWorker:
    """
    Pulls complete mono input blocks from the native engine, runs DPDFNet, and
    pushes enhanced mono samples back to the native engine.

    The native Rust/PortAudio callback owns all real-time audio I/O and ring
    buffers. Python never runs in the PortAudio callback. This worker is allowed
    to block on model inference; when it falls behind the native output callback
    zero-fills and reports underruns through native stats.
    """

    def __init__(
        self,
        engine: Any,
        enhancer: Any,
        *,
        sample_rate: int,
        block_size: int,
        read_timeout_ms: int = 100,
    ) -> None:
        self._engine = engine
        self._enhancer = enhancer
        self._sample_rate = sample_rate
        self._block_size = block_size
        self._read_timeout_ms = read_timeout_ms

        self._stop = threading.Event()
        self._failed = threading.Event()
        self._failure: BaseException | None = None
        self._thread: threading.Thread | None = None

        self.input_blocks = 0
        self.input_frames = 0
        self.read_timeouts = 0
        self.empty_output_blocks = 0
        self.output_blocks = 0
        self.output_frames = 0
        self.output_write_failures = 0

    @property
    def failure(self) -> BaseException | None:
        if not self._failed.is_set():
            return None
        return self._failure

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("EnhancementWorker.start() called more than once.")

        self._thread = threading.Thread(
            target=self._run,
            name="dpdfnet-enhancer",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=2.0)

    def warning_lines(self) -> list[str]:
        pairs = (
            ("worker_empty_outputs", self.empty_output_blocks),
            ("worker_output_write_failures", self.output_write_failures),
        )
        return [f"  - {name}: {count}" for name, count in pairs if count]

    def _run(self) -> None:
        try:
            while not self._stop.is_set():
                samples = self._engine.read_input(self._read_timeout_ms)

                if samples is None:
                    if self._stop.is_set():
                        return
                    if not self._engine.is_active():
                        return
                    self.read_timeouts += 1
                    continue

                input_block = np.asarray(samples, dtype=np.float32).reshape(-1)
                if input_block.size == 0:
                    continue

                self.input_blocks += 1
                self.input_frames += int(input_block.size)

                enhanced = self._enhancer.process(
                    input_block,
                    sample_rate=self._sample_rate,
                )
                output_block = np.asarray(enhanced, dtype=np.float32).reshape(-1)

                if output_block.size == 0:
                    self.empty_output_blocks += 1
                    continue

                if not output_block.flags.c_contiguous:
                    output_block = np.ascontiguousarray(output_block)

                self.output_blocks += 1
                self.output_frames += int(output_block.size)
                self._write_output(output_block)

        except Exception as exc:
            if not self._stop.is_set():
                self._failure = exc
                self._failed.set()

    def _write_output(self, samples: np.ndarray) -> None:
        total = int(samples.size)
        if total == 0:
            return

        chunk_size = max(1, self._block_size)

        for start in range(0, total, chunk_size):
            if self._stop.is_set():
                return

            end = min(start + chunk_size, total)
            chunk = samples[start:end]
            if not chunk.flags.c_contiguous:
                chunk = np.ascontiguousarray(chunk)

            if self._engine.write_output(chunk):
                continue

            if self._stop.is_set() or not self._engine.is_active():
                return

            self.output_write_failures += 1
