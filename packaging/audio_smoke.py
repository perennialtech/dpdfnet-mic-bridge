"""Exercise Linux routing with synthetic endpoints, never a physical microphone."""

from __future__ import annotations

import argparse
from contextlib import ExitStack
import json
import os
from pathlib import Path
import select
import subprocess
import threading
import time
import uuid

import numpy as np


def pactl(*arguments):
    return subprocess.check_output(["pactl", *arguments], text=True).strip()


def terminate(process):
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("executable", type=Path)
    args = parser.parse_args()
    name = "mic_bridge_test_" + uuid.uuid4().hex
    endpoint = None
    with ExitStack() as cleanup:
        for module, arguments in [
            ("module-null-sink", [f"sink_name={name}", "channels=1", "rate=48000"]),
            (
                "module-remap-source",
                [f"master={name}.monitor", f"source_name={name}_input", "channels=1"],
            ),
        ]:
            module_id = pactl("load-module", module, *arguments)
            if not module_id.isdecimal():
                raise RuntimeError("Synthetic source creation failed")
            cleanup.callback(lambda value=module_id: pactl("unload-module", value))
        feed = subprocess.Popen(
            [
                "pacat",
                "--playback",
                "--raw",
                "--format=float32le",
                "--rate=48000",
                "--channels=1",
                f"--device={name}",
            ],
            stdin=subprocess.PIPE,
        )
        cleanup.callback(terminate, feed)
        stopped = threading.Event()
        cleanup.callback(stopped.set)
        samples = (
            (0.05 * np.sin(2 * np.pi * 440 * np.arange(4800) / 48000))
            .astype("<f4")
            .tobytes()
        )

        def generate():
            try:
                while not stopped.is_set():
                    feed.stdin.write(samples)
                    feed.stdin.flush()
            except (BrokenPipeError, OSError):
                pass

        threading.Thread(target=generate, daemon=True).start()
        child = subprocess.Popen(
            [
                str(args.executable.resolve()),
                "_desktop-worker",
                "--device",
                name + "_input",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        cleanup.callback(terminate, child)
        os.set_blocking(child.stdout.fileno(), False)
        buffer = b""
        deadline = time.monotonic() + 90
        ready = False
        try:
            while time.monotonic() < deadline and not ready:
                if select.select([child.stdout], [], [], 0.1)[0]:
                    chunk = os.read(child.stdout.fileno(), 65536)
                    if not chunk:
                        raise RuntimeError("Bridge exited before streaming")
                    buffer += chunk
                    while b"\n" in buffer:
                        line, buffer = buffer.split(b"\n", 1)
                        print(line.decode(errors="replace"), flush=True)
                        try:
                            message = json.loads(line)
                        except ValueError:
                            continue
                        if message.get("event") == "endpoints":
                            endpoint = message
                        if message.get("event") == "ready":
                            ready = True
                if child.poll() is not None and not ready:
                    raise RuntimeError("Bridge startup failed")
            if not ready or endpoint is None:
                raise RuntimeError("Bridge did not become ready within 90 seconds")
            sources = json.loads(pactl("--format=json", "list", "sources"))
            capture_endpoint = next(
                item
                for item in sources
                if item["name"] == endpoint["sink"] + "_capture"
            )
            if capture_endpoint.get("description") != "DPDFNet_Mic":
                raise RuntimeError(
                    "The app-facing microphone name does not match the desktop instructions"
                )
            capture = subprocess.Popen(
                [
                    "parec",
                    "--raw",
                    "--format=float32le",
                    "--rate=48000",
                    "--channels=1",
                    f"--device={endpoint['sink']}_capture",
                ],
                stdout=subprocess.PIPE,
            )
            cleanup.callback(terminate, capture)
            os.set_blocking(capture.stdout.fileno(), False)
            audio = bytearray()
            deadline = time.monotonic() + 10
            while len(audio) < 48000 * 4 and time.monotonic() < deadline:
                if select.select([capture.stdout], [], [], 0.1)[0]:
                    chunk = os.read(capture.stdout.fileno(), 16384)
                    if not chunk:
                        break
                    audio.extend(chunk)
            output = np.frombuffer(audio[: len(audio) // 4 * 4], dtype="<f4")
            if (
                output.size < 24000
                or not np.isfinite(output).all()
                or not np.any(output)
            ):
                raise RuntimeError(
                    "Virtual capture endpoint did not deliver finite, non-silent synthetic audio"
                )
            child.stdin.write(b"stop\n")
            child.stdin.flush()
            child.wait(timeout=15)
            if child.returncode != 0:
                raise RuntimeError(f"Bridge shutdown failed: {child.returncode}")
            remaining = json.loads(pactl("--format=json", "list", "sources"))
            if any(item["name"].startswith(endpoint["sink"]) for item in remaining):
                raise RuntimeError(
                    "Bridge left an owned endpoint after normal shutdown"
                )
            print(
                f"Synthetic audio routing passed: {output.size} samples; owned endpoints removed."
            )
        finally:
            terminate(child)
            if endpoint:
                # The production recovery path validates module ownership too.
                subprocess.run(
                    [
                        str(args.executable.resolve()),
                        "_desktop-cleanup",
                        endpoint["sink"],
                        json.dumps(endpoint["modules"]),
                    ],
                    check=True,
                )


if __name__ == "__main__":
    main()
