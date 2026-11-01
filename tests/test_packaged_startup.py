import subprocess
import sys

import pytest

from dpdfnet_mic_bridge.app import main
from dpdfnet_mic_bridge.cli.parser import build_parser


def test_import_and_help_without_resource_module():
    code = """
import sys, platform, shutil, subprocess
sys.platform = 'win32'
platform.system = lambda: 'Windows'
sys.modules['resource'] = None
import dpdfnet_mic_bridge
import dpdfnet_mic_bridge.app
import dpdfnet_mic_bridge.realtime_env.cli
import dpdfnet_mic_bridge.realtime_env.runtime
import dpdfnet_mic_bridge.realtime_env.backend_latency
assert 'dpdfnet_mic_bridge.realtime_env.linux' not in sys.modules
"""
    subprocess.run([sys.executable, "-c", code], check=True)


def test_desktop_is_default(monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setitem(
        sys.modules,
        "dpdfnet_mic_bridge.desktop",
        SimpleNamespace(desktop_main=lambda: 17),
    )
    assert main([]) == 17


def test_realtime_is_off_for_ordinary_launch():
    assert build_parser().parse_args(["run"]).realtime == "off"


def test_removed_self_test_command_is_rejected(capsys):
    parser = build_parser()
    assert "self-test" not in parser.format_help()
    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["self-test"])
    assert exc.value.code == 2
    assert "invalid choice" in capsys.readouterr().err


def test_live_runner_imports_before_device_setup():
    from dpdfnet_mic_bridge.bridge.runner import run_bridge

    assert callable(run_bridge)


def test_desktop_child_uses_utf8_for_redirected_output(monkeypatch):
    import io
    from dpdfnet_mic_bridge import desktop_process

    output = io.BytesIO()
    stream = io.TextIOWrapper(output, encoding="cp1252")
    monkeypatch.setattr(sys, "stdout", stream)
    monkeypatch.setattr(
        desktop_process, "desktop_inventory", lambda: {"microphones": []}
    )
    assert desktop_process.internal_command(["_desktop-inventory"]) == 0
    stream.write("麦克风")
    stream.flush()
    assert stream.encoding == "utf-8"
    assert "麦克风".encode() in output.getvalue()


def test_desktop_worker_forwards_health_events_without_audio_thread_io(
    monkeypatch, capsys
):
    import io
    import json
    from dpdfnet_mic_bridge import desktop_process
    from dpdfnet_mic_bridge.bridge import runner

    monkeypatch.setattr(desktop_process.platform, "system", lambda: "Linux")
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))

    def fake_run(args, stop, ready, endpoints, health):
        health("degraded", 1.4, 1)
        health("healthy", 0.7, 2)
        return 0

    monkeypatch.setattr(runner, "run_bridge", fake_run)
    assert (
        desktop_process.internal_command(
            ["_desktop-worker", "--device", "test-microphone"]
        )
        == 0
    )
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert events == [
        {"event": "inference_health", "state": "degraded", "rtf": 1.4, "sequence": 1},
        {"event": "inference_health", "state": "healthy", "rtf": 0.7, "sequence": 2},
    ]


def test_desktop_worker_serializes_concurrent_json_events(monkeypatch, capsys):
    import io
    import json
    import threading
    from dpdfnet_mic_bridge import desktop_process
    from dpdfnet_mic_bridge.bridge import runner

    monkeypatch.setattr(desktop_process.platform, "system", lambda: "Linux")
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))

    def fake_run(args, stop, ready, endpoints, health):
        def signal_ready():
            for _ in range(100):
                ready()

        thread = threading.Thread(target=signal_ready)
        thread.start()
        for _ in range(100):
            health("degraded", 1.4, 1)
        thread.join()
        return 0

    monkeypatch.setattr(runner, "run_bridge", fake_run)
    assert (
        desktop_process.internal_command(
            ["_desktop-worker", "--device", "test-microphone"]
        )
        == 0
    )
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert len(events) == 200
    assert sum(event["event"] == "ready" for event in events) == 100
    assert sum(event["event"] == "inference_health" for event in events) == 100


def test_desktop_worker_diagnostics_cannot_corrupt_health_events(monkeypatch, capsys):
    import io
    import json
    from dpdfnet_mic_bridge import desktop_process
    from dpdfnet_mic_bridge.bridge import runner

    monkeypatch.setattr(desktop_process.platform, "system", lambda: "Linux")
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))

    def fake_run(args, stop, ready, endpoints, health):
        print("DIAG", end="", flush=True)
        health("degraded", 1.4, 1)
        ready()
        return 0

    monkeypatch.setattr(runner, "run_bridge", fake_run)
    assert (
        desktop_process.internal_command(
            ["_desktop-worker", "--device", "test-microphone"]
        )
        == 0
    )
    captured = capsys.readouterr()
    assert captured.err == "DIAG"
    events = [json.loads(line) for line in captured.out.splitlines()]
    assert sorted(events, key=lambda event: event["event"]) == [
        {"event": "inference_health", "state": "degraded", "rtf": 1.4, "sequence": 1},
        {"event": "ready"},
    ]


def test_desktop_worker_ready_event_includes_selected_capture_name(monkeypatch, capsys):
    import io
    import json
    from dpdfnet_mic_bridge import desktop_process
    from dpdfnet_mic_bridge.bridge import runner

    monkeypatch.setattr(desktop_process.platform, "system", lambda: "Linux")
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))

    def fake_run(args, stop, ready, endpoints, health):
        ready("CABLE-A Output (Voice Chat)")
        return 0

    monkeypatch.setattr(runner, "run_bridge", fake_run)
    assert (
        desktop_process.internal_command(
            ["_desktop-worker", "--device", "test-microphone"]
        )
        == 0
    )
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert events == [{"event": "ready", "microphone": "CABLE-A Output (Voice Chat)"}]


def test_windows_desktop_discovers_actual_cable_a_endpoints(monkeypatch):
    from dpdfnet_mic_bridge import desktop_process
    from dpdfnet_mic_bridge.audio import inventory as inventory_module
    from dpdfnet_mic_bridge.audio.types import AudioInventory

    monkeypatch.setattr(desktop_process.platform, "system", lambda: "Windows")
    monkeypatch.setattr(
        inventory_module,
        "query_audio_inventory",
        lambda: AudioInventory(
            hostapi_names=["Windows WASAPI"],
            devices=[
                {
                    "id": 0,
                    "name": "Microphone (USB)",
                    "stable_id": "mic-0",
                    "hostapi": 0,
                    "max_input_channels": 1,
                    "max_output_channels": 0,
                },
                {
                    "id": 1,
                    "name": "CABLE-A Input (VB-Audio Cable A)",
                    "hostapi": 0,
                    "max_input_channels": 0,
                    "max_output_channels": 2,
                },
                {
                    "id": 2,
                    "name": "CABLE-A Output (VB-Audio Cable A)",
                    "hostapi": 0,
                    "max_input_channels": 2,
                    "max_output_channels": 0,
                },
            ],
        ),
    )
    assert desktop_process.desktop_inventory() == {
        "microphones": [{"key": "mic-0", "name": "Microphone (USB)"}],
        "driver_ready": True,
    }


def test_windows_desktop_not_ready_for_unpaired_cable_families(monkeypatch):
    from dpdfnet_mic_bridge import desktop_process
    from dpdfnet_mic_bridge.audio import inventory as inventory_module
    from dpdfnet_mic_bridge.audio.types import AudioInventory

    monkeypatch.setattr(desktop_process.platform, "system", lambda: "Windows")
    monkeypatch.setattr(
        inventory_module,
        "query_audio_inventory",
        lambda: AudioInventory(
            hostapi_names=["Windows WASAPI"],
            devices=[
                {
                    "id": 0,
                    "name": "Microphone (USB)",
                    "stable_id": "mic-0",
                    "hostapi": 0,
                    "max_input_channels": 1,
                    "max_output_channels": 0,
                },
                {
                    "id": 1,
                    "name": "CABLE-A Input (VB-Audio Cable A)",
                    "hostapi": 0,
                    "max_input_channels": 0,
                    "max_output_channels": 2,
                },
                {
                    "id": 2,
                    "name": "CABLE-B Output (VB-Audio Cable B)",
                    "hostapi": 0,
                    "max_input_channels": 2,
                    "max_output_channels": 0,
                },
            ],
        ),
    )
    assert desktop_process.desktop_inventory()["driver_ready"] is False


def test_windows_desktop_recognizes_fully_renamed_cable_from_driver(monkeypatch):
    from dpdfnet_mic_bridge import desktop_process
    from dpdfnet_mic_bridge.audio import inventory as inventory_module
    from dpdfnet_mic_bridge.audio.types import AudioInventory

    monkeypatch.setattr(desktop_process.platform, "system", lambda: "Windows")
    monkeypatch.setattr(
        inventory_module,
        "query_audio_inventory",
        lambda: AudioInventory(
            hostapi_names=["Windows WASAPI"],
            devices=[
                {
                    "id": 0,
                    "name": "Microphone (USB)",
                    "stable_id": "mic-0",
                    "hostapi": 0,
                    "max_input_channels": 1,
                    "max_output_channels": 0,
                },
                {
                    "id": 1,
                    "name": "Studio Playback",
                    "hostapi": 0,
                    "max_input_channels": 0,
                    "max_output_channels": 2,
                    "device_description": "VB-Audio Virtual Cable",
                    "container_id": "same",
                },
                {
                    "id": 2,
                    "name": "Conference Capture",
                    "hostapi": 0,
                    "max_input_channels": 2,
                    "max_output_channels": 0,
                    "device_description": "VB-Audio Virtual Cable",
                    "container_id": "same",
                },
            ],
        ),
    )
    assert desktop_process.desktop_inventory() == {
        "microphones": [{"key": "mic-0", "name": "Microphone (USB)"}],
        "driver_ready": True,
    }
