"""Linux desktop audio path, independent of PortAudio's compiled backends."""

from __future__ import annotations

import threading

import numpy as np

from ..audio.pulse import PulseTransport, select_microphone
from ..audio.virtual_mic import managed_linux_virtual_mic
from ..models.loading import load_enhancer
from ..models.registry import model_native_sample_rate
from ..realtime_env.runtime import RealtimeRuntimeManager
from ..realtime_env.types import RuntimeTuningRequest
from .worker import measure_model_realtime_capacity


def run_pulse_bridge(
    args, stop: threading.Event, on_ready=None, on_endpoints=None
) -> int:
    if args.input is not None and getattr(args, "source", None) is not None:
        raise ValueError("Choose either --input or --source, not both.")
    microphone = select_microphone(args.input, getattr(args, "source", None))
    rate = model_native_sample_rate(args.model)
    block_size = rate // 50
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
        capacity = measure_model_realtime_capacity(
            enhancer, sample_rate=rate, block_size=block_size
        )
        if not capacity["ok"]:
            raise RuntimeError(
                f"This CPU cannot process the model in real time (RTF {capacity['rtf']:.2f}). Close other CPU-heavy applications and retry."
            )
        enhancer.reset()
        if stop.is_set():
            return 0
        with managed_linux_virtual_mic(
            enabled=True, on_created=on_endpoints
        ) as endpoint:
            with PulseTransport(
                microphone["name"], endpoint.render_name, rate, stop
            ) as audio:
                # Receiving the first block proves that capture connected; writing
                # it checks playback before the window reports successful startup.
                ready = False
                while not stop.is_set():
                    raw = audio.read(block_size * 4)
                    if raw is None:
                        break
                    block = np.frombuffer(raw, dtype="<f4").copy()
                    np.nan_to_num(block, copy=False, nan=0.0, posinf=0.0, neginf=0.0)
                    np.clip(block, -1, 1, out=block)
                    enhanced = enhancer.process(block, sample_rate=rate)
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
