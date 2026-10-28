"""Linux desktop audio path, independent of PortAudio's compiled backends."""

from __future__ import annotations

import threading
import time
from typing import Callable

import numpy as np

from ..audio.pulse import PulseTransport, select_microphone
from ..audio.virtual_mic import managed_linux_virtual_mic
from ..models.loading import load_enhancer
from ..models.registry import model_native_sample_rate
from ..realtime_env.runtime import RealtimeRuntimeManager
from ..realtime_env.types import RuntimeTuningRequest
from .health import HealthWatchdog, InferenceHealth


def run_pulse_bridge(
    args,
    stop: threading.Event,
    on_ready=None,
    on_endpoints=None,
    on_health: Callable[[str, float], None] | None = None,
) -> int:
    if args.input is not None and getattr(args, "source", None) is not None:
        raise ValueError("Choose either --input or --source, not both.")
    microphone = select_microphone(args.input, getattr(args, "source", None))
    rate = model_native_sample_rate(args.model)
    block_size = rate // 50
    health = InferenceHealth() if on_health is not None else None
    print(f"Input: {microphone.get('description', microphone['name'])}", flush=True)
    print("Loading bundled model...", flush=True)
    with RealtimeRuntimeManager(RuntimeTuningRequest(mode=args.realtime)) as realtime:
        realtime.strict_raise_if_needed(
            realtime.apply_process_tuning(), "process tuning"
        )
        enhancer = load_enhancer(args.model, verbose=bool(args.verbose))
        realtime.strict_raise_if_needed(
            realtime.lock_memory_after_model_load(), "memory locking"
        )
        if stop.is_set():
            return 0
        with managed_linux_virtual_mic(
            enabled=True, on_created=on_endpoints, sample_rate=rate
        ) as endpoint:
            with PulseTransport(
                microphone["name"], endpoint.render_name, rate, stop
            ) as audio:
                # Receiving the first block proves that capture connected; writing
                # it checks playback before the window reports successful startup.
                with HealthWatchdog(health, on_health):
                    return _stream_blocks(
                        audio,
                        enhancer,
                        rate,
                        block_size,
                        stop,
                        on_ready,
                        on_health,
                        health,
                    )


def _stream_blocks(
    audio, enhancer, rate, block_size, stop, on_ready, on_health, health
) -> int:
    ready = False
    while not stop.is_set():
        raw = audio.read(block_size * 4)
        if raw is None:
            break
        block = np.frombuffer(raw, dtype="<f4").copy()
        np.nan_to_num(block, copy=False, nan=0.0, posinf=0.0, neginf=0.0)
        np.clip(block, -1, 1, out=block)
        if health is not None:
            health.begin_inference()
        start = time.perf_counter()
        enhanced = enhancer.process(block, sample_rate=rate)
        elapsed = time.perf_counter() - start
        if health is not None and on_health is not None:
            transition = health.finish_inference(elapsed, block_size / rate)
            if transition is not None:
                on_health(*transition)
        if not enhanced.size:
            continue
        np.nan_to_num(enhanced, copy=False, nan=0.0, posinf=0.0, neginf=0.0)
        audio.write(np.clip(enhanced, -1, 1).astype("<f4").tobytes())
        if not ready:
            print(
                "Streaming. Select DPDFNet_Mic in your calling app.",
                flush=True,
            )
            if on_ready:
                on_ready()
            ready = True
    print("Stopped cleanly.", flush=True)
    return 0
