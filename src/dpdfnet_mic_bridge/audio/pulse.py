"""PulseAudio-protocol transport through the native parec/pacat clients."""

from __future__ import annotations

from contextlib import ExitStack
import os
import select
import subprocess
import threading
import time

from .clients import client_environment, client_path
from .virtual_mic import _run_checked, pulse_list


def microphones() -> list[dict]:
    return [
        item
        for item in pulse_list("sources")
        if not str(item["name"]).endswith(".monitor")
        and item.get("monitor_of_sink") in (None, "n/a", 4294967295)
        and not str(item["name"]).startswith("dpdfnet_mic_bridge_")
    ]


def select_microphone(device_id: int | None, source: str | None = None) -> dict:
    items = microphones()
    if source is not None:
        matches = [item for item in items if item["name"] == source]
    elif device_id is not None:
        matches = [item for item in items if int(item["index"]) == device_id]
    else:
        default = _run_checked(
            (client_path("pactl"), "get-default-source"),
            operation="Could not find the default microphone",
        ).stdout.strip()
        matches = [item for item in items if item["name"] == default]
        if not matches and len(items) == 1:
            matches = items
    if len(matches) != 1:
        raise RuntimeError(
            "Choose a physical microphone in the window, or run `dpdfnet-mic-bridge devices` and use --input."
        )
    return matches[0]


def _stop_client(process: subprocess.Popen) -> None:
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)
    for stream in (process.stdin, process.stdout):
        if stream is not None:
            stream.close()


class PulseTransport:
    """Bounded, interruptible raw PCM pipes; no ALSA device enumeration."""

    def __init__(self, source: str, sink: str, rate: int, stop: threading.Event):
        self.source, self.sink, self.rate, self.stop = source, sink, rate, stop
        self._stack = ExitStack()

    def __enter__(self):
        common = [
            "--raw",
            "--format=float32le",
            f"--rate={self.rate}",
            "--channels=1",
            "--latency-msec=40",
            "--client-name=DPDFNet Mic Bridge",
        ]
        try:
            self.record = subprocess.Popen(
                [client_path("parec"), *common, f"--device={self.source}"],
                stdout=subprocess.PIPE,
                stdin=subprocess.DEVNULL,
                env=client_environment(),
                bufsize=0,
            )
            self._stack.callback(_stop_client, self.record)
            self.play = subprocess.Popen(
                [client_path("pacat"), "--playback", *common, f"--device={self.sink}"],
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                env=client_environment(),
                bufsize=0,
            )
            self._stack.callback(_stop_client, self.play)
            os.set_blocking(self.record.stdout.fileno(), False)
            os.set_blocking(self.play.stdin.fileno(), False)
            return self
        except BaseException:
            self._stack.close()
            raise

    def _check_clients(self):
        if self.record.poll() is not None or self.play.poll() is not None:
            raise RuntimeError(
                "A PulseAudio client disconnected. Check the microphone and desktop audio session."
            )

    def read(self, count: int) -> bytes | None:
        data = bytearray()
        deadline = time.monotonic() + 5
        while len(data) < count and not self.stop.is_set():
            self._check_clients()
            if time.monotonic() >= deadline:
                raise RuntimeError(
                    "The microphone did not deliver audio for 5 seconds."
                )
            if select.select([self.record.stdout], [], [], 0.1)[0]:
                chunk = os.read(self.record.stdout.fileno(), count - len(data))
                if not chunk:
                    raise RuntimeError("The microphone audio stream closed.")
                data.extend(chunk)
        return bytes(data) if len(data) == count else None

    def write(self, data: bytes) -> None:
        remaining = memoryview(data)
        deadline = time.monotonic() + 3
        while remaining and not self.stop.is_set():
            self._check_clients()
            if time.monotonic() >= deadline:
                raise RuntimeError("The virtual microphone stopped accepting audio.")
            if select.select([], [self.play.stdin], [], 0.1)[1]:
                try:
                    count = os.write(self.play.stdin.fileno(), remaining)
                except BlockingIOError:
                    continue
                remaining = remaining[count:]

    def __exit__(self, *exc):
        self._stack.close()
