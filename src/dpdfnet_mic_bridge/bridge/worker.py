"""Background DPDFNet enhancement worker."""

from __future__ import annotations

import queue
import threading

import numpy as np

from .callback_state import AudioFifo


class EnhancementWorker:
    """
    Runs DPDFNet away from PortAudio's real-time callback.

    The callback is allowed to copy one input block, enqueue it without waiting,
    and drain already-produced output. When the worker falls behind we prefer
    bounded, audible damage (dropped stale input or zero-filled output) over
    blocking the real-time thread.
    """

    def __init__(
        self,
        enhancer,
        *,
        sample_rate: int,
        max_queued_blocks: int = 2,
        max_output_blocks: int = 4,
    ) -> None:
        self._enhancer = enhancer
        self._sample_rate = sample_rate
        self._input_queue: queue.Queue[np.ndarray | None] = queue.Queue(
            maxsize=max_queued_blocks
        )
        self._output_fifo = AudioFifo()
        self._output_lock = threading.Lock()
        self._max_output_frames = 0
        self._output_started = False

        self._stop = threading.Event()
        self._failed = threading.Event()
        self._failure: BaseException | None = None
        self._thread: threading.Thread | None = None

        self.input_drop_blocks = 0
        self.output_underrun_blocks = 0
        self.output_drop_blocks = 0
        self.output_lock_misses = 0

        self._max_output_blocks = max_output_blocks

    @property
    def failure(self) -> BaseException | None:
        if not self._failed.is_set():
            return None
        return self._failure

    def start(self) -> None:
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

    def submit_input(self, samples: np.ndarray) -> None:
        if self._stop.is_set() or self._failed.is_set():
            return

        chunk = np.array(samples, dtype=np.float32, copy=True).reshape(-1)
        if chunk.size == 0:
            return

        if self._max_output_frames == 0:
            self._max_output_frames = int(chunk.size) * self._max_output_blocks

        try:
            self._input_queue.put_nowait(chunk)
            return
        except queue.Full:
            pass

        try:
            self._input_queue.get_nowait()
            self.input_drop_blocks += 1
        except queue.Empty:
            pass

        try:
            self._input_queue.put_nowait(chunk)
        except queue.Full:
            self.input_drop_blocks += 1

    def pop_output(self, frames: int) -> np.ndarray:
        silence = np.zeros(frames, dtype=np.float32)
        if frames <= 0:
            return silence

        if not self._output_lock.acquire(blocking=False):
            if self._output_started:
                self.output_lock_misses += 1
            return silence

        try:
            available = self._output_fifo.available
            if available < frames and self._output_started:
                self.output_underrun_blocks += 1
            return self._output_fifo.pop(frames)
        finally:
            self._output_lock.release()

    def warning_lines(self) -> list[str]:
        pairs = (
            ("worker_input_drops", self.input_drop_blocks),
            ("worker_output_underruns", self.output_underrun_blocks),
            ("worker_output_drops", self.output_drop_blocks),
            ("callback_output_lock_misses", self.output_lock_misses),
        )
        return [f"  - {name}: {count}" for name, count in pairs if count]

    def _run(self) -> None:
        try:
            while not self._stop.is_set():
                try:
                    samples = self._input_queue.get(timeout=0.1)
                except queue.Empty:
                    continue

                if samples is None:
                    return

                enhanced = self._enhancer.process(
                    samples,
                    sample_rate=self._sample_rate,
                )
                enhanced = np.asarray(enhanced, dtype=np.float32).reshape(-1)
                if enhanced.size == 0:
                    continue

                with self._output_lock:
                    if (
                        self._max_output_frames > 0
                        and self._output_fifo.available + int(enhanced.size)
                        > self._max_output_frames
                    ):
                        self._output_fifo.clear()
                        self.output_drop_blocks += 1

                    self._output_fifo.push(enhanced)
                    self._output_started = True

        except Exception as exc:
            self._failure = exc
            self._failed.set()
