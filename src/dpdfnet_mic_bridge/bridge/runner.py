"""Live bridge stream lifecycle using the native Rust/PortAudio engine."""

from __future__ import annotations

import argparse
import platform
import sys
import time

from ..models.enhancer import build_stream_enhancer
from .config import build_run_config
from .device_selection import list_devices_handoff
from .summary import print_block_tuning_next_step, print_run_summary
from .worker import EnhancementWorker


def _eprint(*args: object) -> None:
    print(*args, file=sys.stderr)


def _first_line(text: str | None) -> str | None:
    if text is None:
        return None

    stripped = text.strip()
    if not stripped:
        return None

    line = stripped.splitlines()[0]
    if len(line) > 180:
        return line[:177] + "..."
    return line


def _native_failure_from_stats(stats: dict) -> str | None:
    fatal_error = str(stats.get("fatal_error", "none"))
    if fatal_error and fatal_error != "none":
        return f"Native audio callback reported fatal error: {fatal_error}."

    state = str(stats.get("state", ""))
    if state == "failed":
        return "Native audio engine entered failed state."

    return None


def _native_stats_warning_lines(stats: dict) -> list[str]:
    warning_keys = (
        ("input_ring_dropped_blocks", "native_input_ring_dropped_blocks"),
        ("input_ring_dropped_frames", "native_input_ring_dropped_frames"),
        ("output_ring_dropped_blocks", "native_output_ring_dropped_blocks"),
        ("output_ring_dropped_frames", "native_output_ring_dropped_frames"),
        ("output_underrun_blocks", "native_output_underrun_blocks"),
        ("output_underrun_frames", "native_output_underrun_frames"),
        ("portaudio_input_underflow", "portaudio_input_underflow"),
        ("portaudio_input_overflow", "portaudio_input_overflow"),
        ("portaudio_output_underflow", "portaudio_output_underflow"),
        ("portaudio_output_overflow", "portaudio_output_overflow"),
        ("portaudio_priming_output", "portaudio_priming_output"),
        ("oversized_callback_count", "native_oversized_callbacks"),
        ("null_callback_count", "native_null_callbacks"),
    )

    lines: list[str] = []
    for key, label in warning_keys:
        try:
            value = int(stats.get(key, 0) or 0)
        except (TypeError, ValueError):
            continue

        if value:
            lines.append(f"  - {label}: {value}")

    fatal_error = str(stats.get("fatal_error", "none"))
    if fatal_error and fatal_error != "none":
        lines.append(f"  - native_fatal_error: {fatal_error}")

    return lines


def run_bridge(
    args: argparse.Namespace,
    *,
    native_audio_mod,
    dpdfnet_mod,
) -> int:
    if platform.system() not in ("Windows", "Linux"):
        _eprint(
            "[note] This script is primarily intended for Windows 10/11 and Linux PipeWire systems. It may still work elsewhere, but that is not the supported target."
        )

    config = build_run_config(args)
    print_run_summary(config)

    print("Loading model...")
    model_start = time.perf_counter()
    enhancer = build_stream_enhancer(
        dpdfnet_mod,
        model_name=config.model_name,
        verbose=bool(args.verbose),
    )
    model_elapsed = time.perf_counter() - model_start
    print(f"Model ready ({model_elapsed:.2f}s).")

    engine_config = native_audio_mod.AudioEngineConfig(
        config.sample_rate,
        config.block_size,
        config.input_device_id,
        config.output_device_id,
        config.input_channels,
        config.output_channels,
        config.latency,
    )
    engine = native_audio_mod.AudioEngine(engine_config)

    enhancement_worker = EnhancementWorker(
        engine,
        enhancer,
        sample_rate=config.sample_rate,
        block_size=config.block_size,
    )

    print("Starting native audio...")

    stream_error: Exception | None = None
    interrupted = False
    engine_started = False
    worker_started = False
    native_stats: dict = {}

    try:
        engine.start()
        engine_started = True

        enhancement_worker.start()
        worker_started = True

        print("Streaming. Ctrl+C stops.")
        print()

        while True:
            worker_failure = enhancement_worker.failure
            if worker_failure is not None:
                stream_error = RuntimeError(
                    "The enhancement worker stopped unexpectedly."
                )
                try:
                    engine.stop()
                finally:
                    break

            native_stats = dict(engine.stats())
            native_failure = _native_failure_from_stats(native_stats)
            if native_failure is not None:
                stream_error = RuntimeError(native_failure)
                enhancement_worker.stop()
                break

            if not engine.is_active():
                native_stats = dict(engine.stats())
                native_failure = _native_failure_from_stats(native_stats)
                stream_error = RuntimeError(
                    native_failure or "The native audio stream stopped unexpectedly."
                )
                enhancement_worker.stop()
                break

            time.sleep(0.1)

    except KeyboardInterrupt:
        interrupted = True
    except Exception as exc:
        stream_error = exc
    finally:
        if engine_started:
            try:
                engine.stop()
            except Exception as exc:
                if stream_error is None and not interrupted:
                    stream_error = exc

        if worker_started:
            enhancement_worker.stop()

        try:
            native_stats = dict(engine.stats())
        except Exception:
            native_stats = {}

    if interrupted:
        print()
        print("Stopping...")
    elif (worker_failure := enhancement_worker.failure) is not None:
        lines = ["The enhancement worker stopped unexpectedly."]
        detail = _first_line(str(worker_failure))
        if detail is not None:
            lines.append(f"Details: {detail}")
        lines.append(
            "If this happens during streaming, retry with a larger block size."
        )
        raise RuntimeError("\n".join(lines)) from worker_failure
    elif stream_error is not None:
        detail = _first_line(str(stream_error)) or str(stream_error)

        lines = [
            "Failed to open or run the native audio stream.",
            f"Details: {detail}",
        ]
        native_detail = _native_failure_from_stats(native_stats)
        if native_detail is not None and native_detail != detail:
            lines.append(f"Native state: {native_detail}")
        lines.append(list_devices_handoff("retry with fresh IDs if needed."))
        raise RuntimeError("\n".join(lines)) from stream_error

    native_warning_lines = _native_stats_warning_lines(native_stats)
    worker_warning_lines = enhancement_worker.warning_lines()
    warning_lines = [*native_warning_lines, *worker_warning_lines]

    print()
    if warning_lines:
        compact = ", ".join(
            line.strip().removeprefix("- ").replace(": ", "=") for line in warning_lines
        )
        print(f"Audio warnings: {compact}")
        print_block_tuning_next_step(config)
    else:
        print("Stopped cleanly.")
    print()

    return 0
