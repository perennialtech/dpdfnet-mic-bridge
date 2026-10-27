"""Background DPDFNet enhancement worker for the native audio engine."""

from __future__ import annotations

import collections
import math
import threading
import time
from typing import Any, Callable

import numpy as np

from ..realtime_env.errors import RealtimeSetupError
from ..realtime_env.threading import (
    close_thread_realtime_context,
    configure_realtime_worker_thread,
)
from ..realtime_env.types import RuntimeTuningRequest, ThreadTuningResult
from .health import InferenceHealth


class EnhancementWorker:
    """
    Pull complete mono input blocks from the native engine, run DPDFNet, and
    push enhanced mono samples back to the native engine.
    """

    def __init__(
        self,
        engine: Any,
        enhancer: Any,
        *,
        sample_rate: int,
        block_size: int,
        realtime_request: RuntimeTuningRequest | None = None,
        read_timeout_ms: int = 100,
        on_health: Callable[[str, float], None] | None = None,
    ) -> None:
        self._engine = engine
        self._enhancer = enhancer
        self._sample_rate = sample_rate
        self._block_size = block_size
        self._read_timeout_ms = read_timeout_ms
        self._block_seconds = float(block_size) / float(sample_rate)
        self._realtime_request = realtime_request or RuntimeTuningRequest(mode="off")
        self._health = InferenceHealth() if on_health is not None else None
        self._on_health = on_health

        self._input_buffer = np.empty(block_size, dtype=np.float32)
        self._silence_buffer = np.zeros(block_size, dtype=np.float32)
        self._output_scratch = np.empty(block_size + 1, dtype=np.float32)

        self._stop = threading.Event()
        self._failed = threading.Event()
        self._failure: BaseException | None = None
        self._thread: threading.Thread | None = None
        self._thread_tuning_result: ThreadTuningResult | None = None

        self.input_blocks = 0
        self.input_frames = 0
        self.read_timeouts = 0
        self.empty_output_blocks = 0
        self.output_blocks = 0
        self.output_frames = 0
        self.output_write_failures = 0
        self.deadline_misses = 0

        self.process_blocks = 0
        self.process_total_seconds = 0.0
        self.process_max_seconds = 0.0
        self.process_last_seconds = 0.0
        self._rolling_times = collections.deque(maxlen=100)

    @property
    def failure(self) -> BaseException | None:
        if not self._failed.is_set():
            return None
        return self._failure

    @property
    def is_stuck(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive() and self._stop.is_set()

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("EnhancementWorker.start() called more than once.")

        self._thread = threading.Thread(
            target=self._run,
            name="dpdfnet-enhancer",
            daemon=True,
        )
        self._thread.start()

    def stop(self, *, timeout: float = 2.0) -> bool:
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=timeout)
            return not thread.is_alive()
        return True

    def warning_lines(self) -> list[str]:
        pairs = (
            ("worker_empty_outputs", self.empty_output_blocks),
            ("worker_output_write_failures", self.output_write_failures),
            ("worker_deadline_misses", self.deadline_misses),
        )
        lines = [f"  - {name}: {count}" for name, count in pairs if count]
        if self._thread_tuning_result is not None:
            lines.extend(
                f"  - realtime_worker: {line}"
                for line in self._thread_tuning_result.warning_lines
            )
        return lines

    def metrics_snapshot(self) -> dict[str, float | int]:
        all_time_avg = (
            self.process_total_seconds / self.process_blocks
            if self.process_blocks
            else 0.0
        )
        if self._rolling_times:
            rolling_avg = sum(self._rolling_times) / len(self._rolling_times)
            rolling_max = max(self._rolling_times)
        else:
            rolling_avg = 0.0
            rolling_max = 0.0

        rtf = (
            rolling_avg / self._block_seconds if self._block_seconds > 0.0 else math.inf
        )
        return {
            "blocks": self.process_blocks,
            "last_ms": self.process_last_seconds * 1000.0,
            "avg_ms": rolling_avg * 1000.0,
            "max_ms": rolling_max * 1000.0,
            "all_avg_ms": all_time_avg * 1000.0,
            "all_max_ms": self.process_max_seconds * 1000.0,
            "deadline_ms": self._block_seconds * 1000.0,
            "deadline_misses": self.deadline_misses,
            "rtf": rtf,
        }

    def metrics_line(self) -> str:
        metrics = self.metrics_snapshot()
        return (
            "DPDFNet "
            f"block={metrics['last_ms']:.2f}ms "
            f"avg={metrics['avg_ms']:.2f}ms "
            f"max={metrics['max_ms']:.2f}ms "
            f"all_avg={metrics['all_avg_ms']:.2f}ms "
            f"all_max={metrics['all_max_ms']:.2f}ms "
            f"deadline={metrics['deadline_ms']:.2f}ms "
            f"miss={metrics['deadline_misses']} "
            f"rtf={metrics['rtf']:.2f}"
        )

    def _run(self) -> None:
        try:
            self._thread_tuning_result = configure_realtime_worker_thread(
                self._realtime_request
            )
            if (
                self._realtime_request.mode == "strict"
                and not self._thread_tuning_result.ok
            ):
                detail = "\n".join(
                    f"  - {line}" for line in self._thread_tuning_result.warning_lines
                )
                raise RealtimeSetupError(
                    "worker thread realtime setup failed in strict realtime mode."
                    + (f"\n{detail}" if detail else "")
                )

            self._prime_target_latency()

            while not self._stop.is_set():
                have_input = self._engine.read_input_into(
                    self._input_buffer,
                    self._read_timeout_ms,
                )

                if not have_input:
                    if self._stop.is_set():
                        return
                    if not self._engine.is_active():
                        return
                    self.read_timeouts += 1
                    continue

                self._sanitize_input_in_place(self._input_buffer)

                self.input_blocks += 1
                self.input_frames += int(self._input_buffer.size)

                start = time.perf_counter()
                enhanced = self._enhancer.process(
                    self._input_buffer,
                    sample_rate=self._sample_rate,
                )
                elapsed = time.perf_counter() - start
                self._record_process_time(elapsed)

                output_block = np.asarray(enhanced, dtype=np.float32).reshape(-1)
                if output_block.size == 0:
                    self.empty_output_blocks += 1
                    continue

                output_block = self._copy_and_correct_output(output_block)
                self.output_blocks += 1
                self.output_frames += int(output_block.size)
                self._write_output(output_block)

        except Exception as exc:
            if not self._stop.is_set():
                self._failure = exc
                self._failed.set()
        finally:
            close_thread_realtime_context()

    def _prime_target_latency(self) -> None:
        target_frames = int(self._engine.target_output_queue_frames())
        remaining = target_frames
        while remaining > 0 and not self._stop.is_set():
            count = min(remaining, self._block_size)
            if not self._engine.write_output(self._silence_buffer[:count]):
                break
            remaining -= count

    def _sanitize_input_in_place(self, block: np.ndarray) -> None:
        np.nan_to_num(block, copy=False, nan=0.0, posinf=0.0, neginf=0.0)
        np.clip(block, -1.0, 1.0, out=block)

    def _record_process_time(self, elapsed: float) -> None:
        self.process_blocks += 1
        self.process_total_seconds += elapsed
        self.process_last_seconds = elapsed
        self.process_max_seconds = max(self.process_max_seconds, elapsed)
        self._rolling_times.append(elapsed)
        if elapsed > self._block_seconds:
            self.deadline_misses += 1
        if self._health is not None:
            transition = self._health.observe(elapsed, self._block_seconds)
            if transition is not None and self._on_health is not None:
                self._on_health(*transition)

    def _copy_and_correct_output(self, output_block: np.ndarray) -> np.ndarray:
        if not output_block.flags.c_contiguous:
            output_block = np.ascontiguousarray(output_block)

        np.nan_to_num(output_block, copy=False, nan=0.0, posinf=0.0, neginf=0.0)
        np.clip(output_block, -1.0, 1.0, out=output_block)

        queue_depth = int(self._engine.output_queue_depth())
        target_depth = int(self._engine.target_output_queue_frames())

        required_scratch = int(output_block.size) + 1
        if self._output_scratch.size < required_scratch:
            self._output_scratch = np.empty(
                max(required_scratch, self._output_scratch.size * 2), dtype=np.float32
            )

        if output_block.size > 1 and queue_depth > target_depth + self._block_size:
            corrected_size = int(output_block.size) - 1
            self._output_scratch[:corrected_size] = output_block[1:]
            return self._output_scratch[:corrected_size]

        if output_block.size > 0 and queue_depth + output_block.size < target_depth:
            corrected_size = int(output_block.size) + 1
            self._output_scratch[: output_block.size] = output_block
            self._output_scratch[output_block.size : corrected_size] = output_block[-1]
            return self._output_scratch[:corrected_size]

        return output_block

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
