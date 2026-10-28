"""Bounded, transition-only monitoring of real-time inference health."""

from __future__ import annotations

from collections import deque
import threading
import time
from typing import Callable

# Audio-time windows measure repeated misses; wall time catches a call that
# never returns (and therefore produces no new audio-time samples).
WINDOW_AUDIO_SECONDS = 5.0
STALL_SECONDS = 5.0
RECOVERY_RTF = 0.95
DEADLINE_MISS_FRACTION = 0.25
RECOVERY_MISS_FRACTION = 0.10
MIN_OVERLOAD_SAMPLES = 5


class InferenceHealth:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._samples: deque[tuple[float, float, bool]] = deque()
        self._inference_seconds = 0.0
        self._audio_seconds = 0.0
        self._misses = 0
        self._degraded = False
        self._slow_degraded = False
        self._output_degraded = False
        self._previous_output_faults: int | None = None
        self._first_output_fault_at: float | None = None
        self._last_output_fault_at: float | None = None
        self._last_rtf = 1.0
        self._inference_started: float | None = None

    def begin_inference(self) -> None:
        with self._lock:
            self._inference_started = time.monotonic()

    def finish_inference(
        self, inference_seconds: float, audio_seconds: float
    ) -> tuple[str, float] | None:
        with self._lock:
            self._inference_started = None
            return self._observe_locked(inference_seconds, audio_seconds)

    def observe(
        self, inference_seconds: float, audio_seconds: float
    ) -> tuple[str, float] | None:
        with self._lock:
            return self._observe_locked(inference_seconds, audio_seconds)

    def _observe_locked(
        self, inference_seconds: float, audio_seconds: float
    ) -> tuple[str, float] | None:
        if audio_seconds <= 0:
            raise ValueError("audio_seconds must be positive")
        missed = inference_seconds > audio_seconds
        self._samples.append((inference_seconds, audio_seconds, missed))
        self._inference_seconds += inference_seconds
        self._audio_seconds += audio_seconds
        self._misses += int(missed)

        while (
            self._samples
            and self._audio_seconds - self._samples[0][1] >= WINDOW_AUDIO_SECONDS
        ):
            old_inference, old_audio, old_missed = self._samples.popleft()
            self._inference_seconds -= old_inference
            self._audio_seconds -= old_audio
            self._misses -= int(old_missed)

        rtf = self._inference_seconds / self._audio_seconds
        miss_fraction = self._misses / len(self._samples)
        # Multiple slow blocks can consume five seconds of wall time before
        # five seconds of audio completes. One cold-start spike is not repeated.
        overloaded = (
            len(self._samples) >= MIN_OVERLOAD_SAMPLES
            and self._inference_seconds >= WINDOW_AUDIO_SECONDS
            and rtf > 1.0
        ) or (
            self._audio_seconds >= WINDOW_AUDIO_SECONDS
            and miss_fraction >= DEADLINE_MISS_FRACTION
        )
        self._last_rtf = rtf
        if overloaded:
            self._slow_degraded = True
        elif (
            self._slow_degraded
            and self._audio_seconds >= WINDOW_AUDIO_SECONDS
            and rtf < RECOVERY_RTF
            and miss_fraction < RECOVERY_MISS_FRACTION
        ):
            self._slow_degraded = False
        return self._transition_locked(rtf)

    def _transition_locked(self, rtf: float) -> tuple[str, float] | None:
        degraded = self._slow_degraded or self._output_degraded
        if degraded == self._degraded:
            return None
        self._degraded = degraded
        return ("degraded" if degraded else "healthy"), rtf

    def observe_output_faults(
        self, cumulative_faults: int, *, now: float | None = None
    ) -> tuple[str, float] | None:
        """Native output starvation/drop counters; ignores startup and short bursts."""
        if now is None:
            now = time.monotonic()
        with self._lock:
            previous = self._previous_output_faults
            self._previous_output_faults = cumulative_faults
            if previous is None or cumulative_faults < previous:
                # First sample is the baseline, including startup priming.
                self._first_output_fault_at = None
                self._last_output_fault_at = None
                return None
            if cumulative_faults > previous:
                if (
                    self._last_output_fault_at is None
                    or now - self._last_output_fault_at > 1.0
                ):
                    self._first_output_fault_at = now
                self._last_output_fault_at = now
                if (
                    self._first_output_fault_at is not None
                    and now - self._first_output_fault_at >= WINDOW_AUDIO_SECONDS
                ):
                    self._output_degraded = True
            elif (
                self._last_output_fault_at is not None
                and now - self._last_output_fault_at >= WINDOW_AUDIO_SECONDS
            ):
                self._output_degraded = False
                self._first_output_fault_at = None
                self._last_output_fault_at = None
            return self._transition_locked(self._last_rtf)

    def check_stall(self) -> tuple[str, float] | None:
        """Called outside inference so an indefinitely blocked call is seen."""
        with self._lock:
            started = self._inference_started
            if (
                started is None
                or self._slow_degraded
                or time.monotonic() - started < STALL_SECONDS
            ):
                return None
            self._slow_degraded = True
            return self._transition_locked(
                max(1.0, STALL_SECONDS / max(self._audio_seconds, 0.02))
            )


class HealthWatchdog:
    """Small daemon thread, only active while a monitored stream is running."""

    def __init__(
        self,
        health: InferenceHealth | None,
        on_health: Callable[[str, float], None] | None,
    ) -> None:
        self._health = health
        self._on_health = on_health
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._health is None or self._on_health is None:
            return
        self._thread = threading.Thread(
            target=self._run, name="inference-health-watchdog", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)

    def __enter__(self) -> HealthWatchdog:
        self.start()
        return self

    def __exit__(self, *_: object) -> None:
        self.stop()

    def _run(self) -> None:
        assert self._health is not None and self._on_health is not None
        while not self._stop.wait(0.1):
            transition = self._health.check_stall()
            if transition is not None and not self._stop.is_set():
                self._on_health(*transition)
