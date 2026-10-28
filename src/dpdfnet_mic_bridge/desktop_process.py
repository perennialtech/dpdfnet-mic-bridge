"""Child-process commands shared by source and frozen desktop launches."""

from __future__ import annotations

import argparse
from contextlib import redirect_stdout
import json
from pathlib import Path
import platform
import queue
import signal
import sys
import threading


def child_command(arguments: list[str]) -> tuple[str, list[str]]:
    if getattr(sys, "frozen", False):
        executable = Path(sys.executable)
        if sys.platform == "win32":
            executable = executable.with_name("dpdfnet-mic-bridge-cli.exe")
        return str(executable), arguments
    return sys.executable, ["-u", "-m", "dpdfnet_mic_bridge", *arguments]


def desktop_inventory() -> dict:
    if platform.system() == "Linux":
        from .audio.pulse import microphones

        return {
            "microphones": [
                {"key": item["name"], "name": item.get("description", item["name"])}
                for item in microphones()
            ],
            "driver_ready": True,
        }
    if platform.system() != "Windows":
        raise RuntimeError("The desktop application supports Windows and Linux.")
    from .audio.inventory import hostapi_name_for_device, query_audio_inventory
    from .audio.hostapi import hostapi_filter_key_for_name

    inventory = query_audio_inventory()
    devices = [
        item
        for item in inventory.devices
        if hostapi_filter_key_for_name(
            hostapi_name_for_device(item, inventory.hostapi_names)
        )
        == "wasapi"
    ]
    render = any(
        "cable input" in item["name"].casefold() and item["max_output_channels"] > 0
        for item in devices
    )
    capture = any(
        "cable output" in item["name"].casefold() and item["max_input_channels"] > 0
        for item in devices
    )
    inputs = [
        item
        for item in devices
        if item["max_input_channels"] > 0
        and not any(
            word in item["name"].casefold() for word in ("cable", "loopback", "monitor")
        )
    ]
    return {
        "microphones": [
            {"key": item["stable_id"], "name": item["name"]} for item in inputs
        ],
        "driver_ready": render and capture,
    }


def internal_command(argv: list[str]) -> int:
    # Frozen interpreters need an explicit encoding too: redirected Windows
    # streams can otherwise reject non-English microphone names.
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    if argv == ["_desktop-ui-test"]:
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QApplication
        from .desktop import BridgeWindow

        app = QApplication([])
        BridgeWindow.discover = lambda self: None
        window = BridgeWindow()
        window.show()
        QTimer.singleShot(100, app.quit)
        result = app.exec()
        print("Desktop window smoke test passed.", flush=True)
        return result
    if argv == ["_desktop-inventory"]:
        print(json.dumps(desktop_inventory()), flush=True)
        return 0
    if argv[0] == "_desktop-cleanup":
        from .audio.virtual_mic import cleanup_owned_endpoint

        parser = argparse.ArgumentParser()
        parser.add_argument("sink")
        parser.add_argument("modules")
        args = parser.parse_args(argv[1:])
        cleanup_owned_endpoint(args.sink, json.loads(args.modules))
        return 0
    if argv[0] != "_desktop-worker":
        raise ValueError("Unknown desktop child command")
    parser = argparse.ArgumentParser()
    parser.add_argument("--device", required=True)
    selected = parser.parse_args(argv[1:])
    from .cli.parser import build_parser

    args = build_parser().parse_args(["run", "--realtime", "off"])
    if platform.system() == "Linux":
        args.source = selected.device
    else:
        from .audio.inventory import query_audio_inventory

        matches = [
            item
            for item in query_audio_inventory().devices
            if item["stable_id"] == selected.device
        ]
        if len(matches) != 1:
            raise RuntimeError(
                "The selected microphone changed. Refresh the microphone list and retry."
            )
        args.input = matches[0]["id"]

    stop = threading.Event()

    def read_control():
        # EOF also stops the engine if the desktop process crashes or closes.
        for line in sys.stdin:
            if line.strip() == "stop":
                break
        stop.set()

    threading.Thread(target=read_control, name="desktop-control", daemon=True).start()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())

    # Reserve stdout for JSON events. Worker progress and diagnostics go to
    # stderr, which the desktop reads separately.
    event_stream = sys.stdout
    event_output_lock = threading.Lock()

    def emit_event(message: dict):
        with event_output_lock:
            event_stream.write(json.dumps(message) + "\n")
            event_stream.flush()

    def ready():
        emit_event({"event": "ready"})

    def endpoints(endpoint):
        emit_event(
            {
                "event": "endpoints",
                "sink": endpoint.render_name,
                "modules": endpoint.module_ids,
            }
        )

    # Audio workers only enqueue transitions; a separate thread writes IPC so
    # notifications cannot block microphone capture or inference.
    health_events: queue.SimpleQueue[tuple[str, float, int] | None] = (
        queue.SimpleQueue()
    )

    def forward_health():
        while (transition := health_events.get()) is not None:
            state, rtf, sequence = transition
            emit_event(
                {
                    "event": "inference_health",
                    "state": state,
                    "rtf": rtf,
                    "sequence": sequence,
                }
            )

    sender = threading.Thread(target=forward_health, name="desktop-health", daemon=True)
    sender.start()

    from .bridge.runner import run_bridge

    try:
        with redirect_stdout(sys.stderr):
            return run_bridge(
                args,
                stop,
                ready,
                endpoints,
                lambda state, rtf, sequence: health_events.put((state, rtf, sequence)),
            )
    finally:
        health_events.put(None)
        sender.join(timeout=1)
