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


def test_repeated_deadline_misses_detected_even_when_average_rtf_is_low():
    monitor = InferenceHealth()
    events = [
        monitor.observe(duration, 0.02)
        for _ in range(500)
        for duration in (0.030, 0.005, 0.005)
    ]
    assert [event[0] for event in events if event] == ["degraded"]
    assert events[-1] is None


def test_single_completed_cold_start_is_not_repeated_overload():
    monitor = InferenceHealth()
    assert monitor.observe(5.1, 0.02) is None


def test_stalled_native_enhancer_is_detected_before_process_returns(monkeypatch):
    import time
    from dpdfnet_mic_bridge.bridge import health, worker as worker_module

    monkeypatch.setattr(health, "STALL_SECONDS", 0.06)
    monkeypatch.setattr(
        worker_module,
        "configure_realtime_worker_thread",
        lambda *_: SimpleNamespace(ok=True, warning_lines=[]),
    )
    monkeypatch.setattr(worker_module, "close_thread_realtime_context", lambda: None)
    entered = threading.Event()
    release = threading.Event()
    reported = threading.Event()
    events = []

    class Engine:
        def target_output_queue_frames(self):
            return 0

        def read_input_into(self, block, timeout_ms):
            block.fill(0)
            return True

        def output_queue_depth(self):
            return 0

        def write_output(self, block):
            return True

    class Enhancer:
        def process(self, block, sample_rate):
            entered.set()
            release.wait(timeout=3)
            return block.copy()

    def on_health(state, rtf):
        events.append(state)
        reported.set()

    native = EnhancementWorker(
        Engine(), Enhancer(), sample_rate=48000, block_size=960, on_health=on_health
    )
    try:
        native.start()
        assert entered.wait(1)
        assert reported.wait(1), "Watchdog must report while process() remains blocked"
        assert events == ["degraded"]
        time.sleep(0.2)
        assert events == ["degraded"], "Stall must produce only one notification"
    finally:
        release.set()
        assert native.stop(timeout=2)


def test_sustained_native_output_starvation_and_recovery():
    monitor = InferenceHealth()
    transitions = []
    assert monitor.observe_output_faults(100, now=0) is None  # Baseline
    for i in range(1, 66):
        event = monitor.observe_output_faults(100 + i, now=i * 0.1)
        if event:
            transitions.append(event[0])
    assert transitions == ["degraded"]
    for i in range(66, 130):
        event = monitor.observe_output_faults(165, now=i * 0.1)
        if event:
            transitions.append(event[0])
    assert transitions == ["degraded", "healthy"]


def test_isolated_native_output_faults_do_not_notify():
    monitor = InferenceHealth()
    monitor.observe_output_faults(0, now=0)
    assert [monitor.observe_output_faults(i, now=i * 2.0) for i in range(1, 8)] == [
        None
    ] * 7


def test_stalled_inference_cannot_recover_while_output_faults_clear(monkeypatch):
    from dpdfnet_mic_bridge.bridge import health

    monitor = InferenceHealth()
    assert monitor.observe_output_faults(0, now=0) is None
    transitions = [monitor.observe_output_faults(i, now=i * 0.1) for i in range(1, 66)]
    assert [event[0] for event in transitions if event] == ["degraded"]

    monkeypatch.setattr(health.time, "monotonic", lambda: 10.0)
    monitor.begin_inference()
    monkeypatch.setattr(health.time, "monotonic", lambda: 16.0)
    assert monitor.check_stall() is None
    # Output recovers, but inference remains blocked. Neither a false healthy
    # event nor a second degraded notification should be generated.
    assert monitor.observe_output_faults(65, now=16.0) is None
    assert monitor.check_stall() is None


def test_stalled_pulse_inference_is_detected_before_process_returns(monkeypatch):
    from dpdfnet_mic_bridge.bridge import health, pulse_runner

    monkeypatch.setattr(health, "STALL_SECONDS", 0.06)
    entered = threading.Event()
    release = threading.Event()
    reported = threading.Event()
    stop = threading.Event()
    events = []
    errors = []

    class Context:
        def __init__(self, value):
            self.value = value

        def __enter__(self):
            return self.value

        def __exit__(self, *_):
            pass

    class Realtime:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def apply_process_tuning(self):
            pass

        def lock_memory_after_model_load(self):
            pass

        def strict_raise_if_needed(self, *_):
            pass

    audio = np.zeros(960, dtype="<f4").tobytes()

    class Transport:
        count = 0

        def read(self, size):
            self.count += 1
            if self.count == 1:
                return audio
            stop.set()
            return None

        def write(self, data):
            pass

    class Enhancer:
        def process(self, block, sample_rate):
            entered.set()
            release.wait(timeout=3)
            return block

    monkeypatch.setattr(pulse_runner, "select_microphone", lambda *_: {"name": "test"})
    monkeypatch.setattr(pulse_runner, "model_native_sample_rate", lambda _: 48000)
    monkeypatch.setattr(pulse_runner, "load_enhancer", lambda *_, **__: Enhancer())
    monkeypatch.setattr(pulse_runner, "RealtimeRuntimeManager", lambda *_: Realtime())
    monkeypatch.setattr(
        pulse_runner,
        "managed_linux_virtual_mic",
        lambda **_: Context(SimpleNamespace(render_name="virtual")),
    )
    monkeypatch.setattr(pulse_runner, "PulseTransport", lambda *_: Context(Transport()))

    def on_health(state, rtf):
        events.append(state)
        reported.set()

    def run():
        try:
            pulse_runner.run_pulse_bridge(
                SimpleNamespace(
                    input=None,
                    source="test",
                    model="test",
                    realtime="off",
                    verbose=False,
                ),
                stop,
                on_health=on_health,
            )
        except Exception as exc:
            errors.append(exc)

    thread = threading.Thread(target=run, daemon=True)
    try:
        thread.start()
        assert entered.wait(1)
        assert reported.wait(1), "Pulse watchdog must report during blocked process()"
        assert events == ["degraded"]
    finally:
        release.set()
        thread.join(timeout=2)
    assert not thread.is_alive()
    assert not errors


def test_concurrent_health_callbacks_follow_state_transitions():
    """A slow recovery callback must not overtake a new overload transition."""
    entered = threading.Event()
    release = threading.Event()
    events = []

    def on_health(state, rtf):
        if state == "healthy":
            entered.set()
            assert release.wait(2)
        events.append(state)

    monitor = InferenceHealth(on_health)
    monitor.observe_output_faults(0, now=0)
    for index in range(1, 66):
        monitor.observe_output_faults(index, now=index * 0.1)
    assert events == ["degraded"]

    recovering = threading.Thread(
        target=lambda: monitor.observe_output_faults(65, now=12)
    )
    recovering.start()
    try:
        assert entered.wait(1)
        overloading = threading.Thread(
            target=lambda: [monitor.observe(0.2, 0.02) for _ in range(30)]
        )
        overloading.start()
        assert events == ["degraded"]
    finally:
        release.set()
        recovering.join(timeout=2)
    overloading.join(timeout=2)
    assert not recovering.is_alive()
    assert not overloading.is_alive()
    assert events == ["degraded", "healthy", "degraded"]
