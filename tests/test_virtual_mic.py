from types import SimpleNamespace

import pytest

from dpdfnet_mic_bridge.audio import virtual_mic as mic


@pytest.fixture
def linux(monkeypatch):
    monkeypatch.setattr(mic.platform, "system", lambda: "Linux")
    monkeypatch.setattr(mic, "client_path", lambda name: name)


def test_capture_failure_rolls_back_sink(linux, monkeypatch):
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        if "module-remap-source" in command:
            raise RuntimeError("capture failed")
        return SimpleNamespace(stdout="41\n")

    unloaded = []
    monkeypatch.setattr(
        mic, "cleanup_owned_endpoint", lambda sink, ids: unloaded.extend(ids)
    )
    monkeypatch.setattr(mic, "_run_checked", run)
    monkeypatch.setattr(
        mic, "_unload_virtual_mic_module", lambda pactl, ids: unloaded.extend(ids)
    )
    with pytest.raises(RuntimeError, match="capture failed"):
        with mic.managed_linux_virtual_mic(enabled=True):
            pytest.fail("Monitor-only setup must not succeed")
    assert unloaded == ["41"]


def test_owned_endpoints_are_unique_and_cleaned(linux, monkeypatch):
    names, unloaded = [], []
    monkeypatch.setattr(
        mic,
        "_load_virtual_mic_module",
        lambda pactl, name, notify, sample_rate: names.append(name) or ["41", "42"],
    )
    monkeypatch.setattr(mic, "_wait_for_virtual_sink", lambda name: None)
    monkeypatch.setattr(
        mic, "cleanup_owned_endpoint", lambda sink, ids: unloaded.append(ids)
    )
    monkeypatch.setattr(
        mic, "_unload_virtual_mic_module", lambda pactl, ids: unloaded.append(ids)
    )
    for _ in range(2):
        with mic.managed_linux_virtual_mic(enabled=True) as endpoint:
            assert endpoint.capture_name == endpoint.render_name + "_capture"
            assert endpoint.module_ids == ("41", "42")
    assert len(set(names)) == 2
    assert unloaded == [["41", "42"], ["41", "42"]]


def test_endpoint_visibility_failure_cleans_both_modules(linux, monkeypatch):
    monkeypatch.setattr(mic, "_load_virtual_mic_module", lambda *args: ["41", "42"])
    monkeypatch.setattr(
        mic,
        "_wait_for_virtual_sink",
        lambda name: (_ for _ in ()).throw(RuntimeError("invisible")),
    )
    unloaded = []
    monkeypatch.setattr(
        mic, "cleanup_owned_endpoint", lambda sink, ids: unloaded.extend(ids)
    )
    monkeypatch.setattr(
        mic, "_unload_virtual_mic_module", lambda pactl, ids: unloaded.extend(ids)
    )
    with pytest.raises(RuntimeError, match="invisible"):
        with mic.managed_linux_virtual_mic(enabled=True):
            pass
    assert unloaded == ["41", "42"]


def test_recovery_never_unloads_reused_unrelated_ids(linux, monkeypatch):
    sink = "dpdfnet_mic_bridge_" + "a" * 32
    monkeypatch.setattr(
        mic,
        "pulse_list",
        lambda kind: [
            {"index": 41, "name": "module-null-sink", "argument": f"sink_name={sink}"},
            {
                "index": 42,
                "name": "module-null-sink",
                "argument": "sink_name=user_audio",
            },
        ],
    )
    unloaded = []
    monkeypatch.setattr(
        mic, "_unload_virtual_mic_module", lambda pactl, ids: unloaded.extend(ids)
    )
    mic.cleanup_owned_endpoint(sink, ["41", "42"])
    assert unloaded == ["41"]


def test_verifies_sink_and_capture_source(linux, monkeypatch):
    calls = []

    def listing(kind):
        calls.append(kind)
        return [{"name": "owned" if kind == "sinks" else "owned_capture"}]

    monkeypatch.setattr(mic, "pulse_list", listing)
    mic._wait_for_virtual_sink("owned")
    assert calls == ["sinks", "sources"]


def test_module_ids_use_portable_short_listing(linux, monkeypatch):
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(
            stdout="1\tlibpipewire-module-rt\t{\n nice.level = -11\n}\n536870918\tmodule-null-sink\tsink_name=owned\t\n"
        )

    monkeypatch.setattr(mic, "_run_checked", run)
    assert mic.pulse_list("modules") == [
        {"index": 1, "name": "libpipewire-module-rt", "argument": "{"},
        {"index": 536870918, "name": "module-null-sink", "argument": "sink_name=owned"},
    ]
    assert calls == [("pactl", "list", "short", "modules")]


def test_virtual_sink_preserves_float_output_at_model_rate(monkeypatch):
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(stdout=f"{40 + len(calls)}\n")

    monkeypatch.setattr(mic, "_run_checked", run)
    assert mic._load_virtual_mic_module("pactl", "owned", sample_rate=16000) == [
        "41",
        "42",
    ]
    assert "format=float32le" in calls[0]
    assert "rate=16000" in calls[0]
