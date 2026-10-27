from types import SimpleNamespace
import threading

import numpy as np

from dpdfnet_mic_bridge.bridge.health import InferenceHealth
from dpdfnet_mic_bridge.bridge.worker import EnhancementWorker


def test_overload_notifies_once_until_sustained_recovery():
    monitor = InferenceHealth()
    transitions = []
    for _ in range(320):
        transition = monitor.observe(0.030, 0.020)
        if transition:
            transitions.append(transition[0])
    assert transitions == ["degraded"]

    for _ in range(320):
        transition = monitor.observe(0.005, 0.020)
        if transition:
            transitions.append(transition[0])
    assert transitions == ["degraded", "healthy"]

    for _ in range(320):
        transition = monitor.observe(0.030, 0.020)
        if transition:
            transitions.append(transition[0])
    assert transitions == ["degraded", "healthy", "degraded"]


def test_severe_overload_does_not_wait_for_five_seconds_of_processed_audio():
    monitor = InferenceHealth()
    events = [monitor.observe(0.2, 0.02) for _ in range(30)]
    assert [event[0] for event in events if event] == ["degraded"]


def test_occasional_spikes_do_not_trigger_overload():
    monitor = InferenceHealth()
    transitions = [
        monitor.observe(0.035 if i % 10 == 0 else 0.007, 0.020) for i in range(1000)
    ]
    assert not any(transitions)


def test_worker_reports_health_without_changing_existing_deadline_metrics():
    events = []
    worker = EnhancementWorker(
        engine=None,
        enhancer=None,
        sample_rate=48000,
        block_size=960,
        on_health=lambda state, rtf: events.append((state, rtf)),
    )
    for _ in range(320):
        worker._record_process_time(0.030)
    assert worker.deadline_misses == 320
    assert len(events) == 1
    assert events[0][0] == "degraded"
    assert events[0][1] > 1


def test_default_linux_route_emits_health_transitions(monkeypatch):
    from dpdfnet_mic_bridge.bridge import pulse_runner

    class Realtime:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def apply_process_tuning(self):
            return None

        def lock_memory_after_model_load(self):
            return None

        def strict_raise_if_needed(self, *_):
            pass

    class Context:
        def __init__(self, value):
            self.value = value

        def __enter__(self):
            return self.value

        def __exit__(self, *_):
            pass

    stop = threading.Event()
    audio = np.zeros(960, dtype="<f4").tobytes()

    class Transport:
        count = 0

        def read(self, size):
            assert size == len(audio)
            self.count += 1
            if self.count > 640:
                stop.set()
                return None
            return audio

        def write(self, data):
            assert len(data) == len(audio)

    transport = Transport()
    durations = [0.030] * 320 + [0.005] * 320
    calls = iter(value for duration in durations for value in (0, duration))
    monkeypatch.setattr(
        pulse_runner, "time", SimpleNamespace(perf_counter=lambda: next(calls))
    )
    monkeypatch.setattr(pulse_runner, "select_microphone", lambda *_: {"name": "test"})
    monkeypatch.setattr(pulse_runner, "model_native_sample_rate", lambda _: 48000)
    monkeypatch.setattr(
        pulse_runner,
        "load_enhancer",
        lambda *_, **__: SimpleNamespace(process=lambda block, **__: block),
    )
    monkeypatch.setattr(pulse_runner, "RealtimeRuntimeManager", lambda *_: Realtime())
    monkeypatch.setattr(
        pulse_runner,
        "managed_linux_virtual_mic",
        lambda **_: Context(SimpleNamespace(render_name="virtual")),
    )
    monkeypatch.setattr(pulse_runner, "PulseTransport", lambda *_: Context(transport))
    transitions = []
    result = pulse_runner.run_pulse_bridge(
        SimpleNamespace(
            input=None, source="test", model="test", realtime="off", verbose=False
        ),
        stop,
        on_health=lambda state, rtf: transitions.append(state),
    )
    assert result == 0
    assert transitions == ["degraded", "healthy"]
