import os

from dpdfnet_mic_bridge.realtime_env.backend_latency import \
    probe_backend_latency_context


def test_pipewire_latency_env_is_restored(monkeypatch):
    monkeypatch.setenv("PIPEWIRE_LATENCY", "old")

    with probe_backend_latency_context(
        hostapi_name="PipeWire",
        sample_rate=48000,
        block_size=960,
        block_ms=20.0,
        mode="auto",
    ):
        assert os.environ["PIPEWIRE_LATENCY"] == "960/48000"

    assert os.environ["PIPEWIRE_LATENCY"] == "old"


def test_pulse_latency_env_is_restored_when_absent(monkeypatch):
    monkeypatch.delenv("PULSE_LATENCY_MSEC", raising=False)
    monkeypatch.delenv("PIPEWIRE_LATENCY", raising=False)

    with probe_backend_latency_context(
        hostapi_name="PulseAudio",
        sample_rate=48000,
        block_size=960,
        block_ms=20.1,
        mode="auto",
    ):
        assert os.environ["PULSE_LATENCY_MSEC"] == "21"
        assert os.environ["PIPEWIRE_LATENCY"] == "960/48000"

    assert "PULSE_LATENCY_MSEC" not in os.environ
    assert "PIPEWIRE_LATENCY" not in os.environ


def test_latency_context_off_is_noop(monkeypatch):
    monkeypatch.delenv("PIPEWIRE_LATENCY", raising=False)

    with probe_backend_latency_context(
        hostapi_name="PipeWire",
        sample_rate=48000,
        block_size=960,
        block_ms=20.0,
        mode="off",
    ):
        assert "PIPEWIRE_LATENCY" not in os.environ
