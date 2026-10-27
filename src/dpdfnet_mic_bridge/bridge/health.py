"""Detect sustained inference throughput below real-time audio production."""

from __future__ import annotations

from collections import deque

# The window spans processed audio, not wall time: blocked or paused capture
# must not count as evidence that inference has recovered.
WINDOW_AUDIO_SECONDS = 5.0
RECOVERY_RTF = 0.95


class InferenceHealth:
    def __init__(self) -> None:
        self._samples: deque[tuple[float, float]] = deque()
        self._inference_seconds = 0.0
        self._audio_seconds = 0.0
        self._degraded = False

    def observe(
        self, inference_seconds: float, audio_seconds: float
    ) -> tuple[str, float] | None:
        self._samples.append((inference_seconds, audio_seconds))
        self._inference_seconds += inference_seconds
        self._audio_seconds += audio_seconds

        while (
            self._samples
            and self._audio_seconds - self._samples[0][1] >= WINDOW_AUDIO_SECONDS
        ):
            old_inference, old_audio = self._samples.popleft()
            self._inference_seconds -= old_inference
            self._audio_seconds -= old_audio

        rtf = self._inference_seconds / self._audio_seconds
        # Heavy overload consumes observation time faster than audio is
        # produced. Detect it without waiting for a full window of audio.
        if (
            not self._degraded
            and self._inference_seconds >= WINDOW_AUDIO_SECONDS
            and rtf > 1.0
        ):
            self._degraded = True
            return "degraded", rtf
        if (
            self._degraded
            and self._audio_seconds >= WINDOW_AUDIO_SECONDS
            and rtf < RECOVERY_RTF
        ):
            self._degraded = False
            return "healthy", rtf
        return None
