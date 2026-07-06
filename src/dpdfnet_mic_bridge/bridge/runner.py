"""Live bridge stream lifecycle using the native Rust/PortAudio engine."""

from __future__ import annotations

import argparse
import platform
import sys
import time

import dpdfnet

from .. import _native_audio
from ..audio.stderr import silence_native_stderr
from ..realtime_env.runtime import RealtimeRuntimeManager
from ..realtime_env.types import RuntimeTuningRequest
from .config import build_run_config
from .device_selection import list_devices_handoff
from .summary import print_block_tuning_next_step, print_run_summary
from .worker import EnhancementWorker, measure_model_realtime_capacity


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


def _native_failure_from_stats(stats: dict, *, realtime_mode: str) -> str | None:
    fatal_error = str(stats.get("fatal_error", "none"))
    if fatal_error and fatal_error != "none":
        return f"Native audio callback reported fatal error: {fatal_error}."

    state = str(stats.get("state", ""))
    if state == "failed":
        return "Native audio engine entered failed state."

    callback_rt = str(stats.get("callback_realtime_status", "not_requested"))
    if realtime_mode == "strict" and callback_rt == "failed":
        return (
            "Native callback realtime setup failed "
            f"(error_code={stats.get('callback_realtime_error_code', 0)})."
        )

    return None


def _native_stats_warning_lines(stats: dict) -> list[str]:
    warning_keys = (
        ("input_ring_dropped_blocks", "native_input_ring_dropped_blocks"),
        ("input_ring_dropped_frames", "native_input_ring_dropped_frames"),
        ("output_ring_dropped_blocks", "native_output_ring_dropped_blocks"),
        ("output_ring_dropped_frames", "native_output_ring_dropped_frames"),
        ("output_underrun_blocks", "native_output_underrun_blocks"),
        ("output_underrun_frames", "native_output_underrun_frames"),
        ("input_stale_dropped_blocks", "native_input_stale_dropped_blocks"),
        ("input_stale_dropped_frames", "native_input_stale_dropped_frames"),
        ("output_stale_dropped_blocks", "native_output_stale_dropped_blocks"),
        ("output_stale_dropped_frames", "native_output_stale_dropped_frames"),
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

    callback_rt = str(stats.get("callback_realtime_status", "not_requested"))
    if callback_rt in {"failed", "unsupported"}:
        lines.append(
            "  - native_callback_realtime: "
            f"{callback_rt} error_code={stats.get('callback_realtime_error_code', 0)}"
        )
    elif callback_rt == "pending":
        lines.append("  - native_callback_realtime: pending")

    return lines


def run_bridge(args: argparse.Namespace) -> int:
    if platform.system() not in ("Windows", "Linux"):
        _eprint(
            "[note] This script is primarily intended for Windows 10/11 and Linux PipeWire systems. It may still work elsewhere, but that is not the supported target."
        )

    realtime_request = RuntimeTuningRequest(mode=args.realtime)

    with RealtimeRuntimeManager(realtime_request) as realtime:
        process_result = realtime.apply_process_tuning()
        realtime.strict_raise_if_needed(process_result, "process realtime setup")

        config = build_run_config(args, realtime_manager=realtime)
        route_result = realtime.apply_route_tuning(config)
        realtime.strict_raise_if_needed(route_result, "route realtime setup")

        print_run_summary(config, route_result, realtime.warning_lines())

        print("Loading model...")
        model_start = time.perf_counter()
        try:
            enhancer = dpdfnet.StreamEnhancer(
                model=config.model_name, verbose=bool(args.verbose)
            )
        except Exception as exc:
            detail = _first_line(str(exc)) or type(exc).__name__
            raise RuntimeError(
                f"Could not load DPDFNet model '{config.model_name}'.\n"
                "If the model has not been downloaded yet, run:\n"
                f"  dpdfnet download {config.model_name}\n"
                f"Details: {detail}"
            ) from exc
        model_elapsed = time.perf_counter() - model_start
        print(f"Model ready ({model_elapsed:.2f}s).")

        memory_result = realtime.lock_memory_after_model_load()
        realtime.strict_raise_if_needed(memory_result, "memory locking")

        capacity = measure_model_realtime_capacity(
            enhancer,
            sample_rate=config.sample_rate,
            block_size=config.block_size,
            jitter_blocks=2,
        )
        if not bool(capacity["ok"]):
            raise RuntimeError(
                "DPDFNet inference is too slow for this route.\n"
                f"Average: {float(capacity['avg_seconds']) * 1000.0:.2f} ms, "
                f"max: {float(capacity['max_seconds']) * 1000.0:.2f} ms, "
                f"block deadline: {float(capacity['block_seconds']) * 1000.0:.2f} ms, "
                f"jitter budget: {float(capacity['budget_seconds']) * 1000.0:.2f} ms, "
                f"RTF: {float(capacity['rtf']):.2f}, "
                f"deadline misses: {int(capacity['deadline_misses'])}.\n"
                "Use a faster model, a faster CPU/GPU path, or reduce other system load."
            )

        print(
            "Model timing "
            f"avg={float(capacity['avg_seconds']) * 1000.0:.2f}ms "
            f"max={float(capacity['max_seconds']) * 1000.0:.2f}ms "
            f"rtf={float(capacity['rtf']):.2f}"
        )

        engine_config = _native_audio.AudioEngineConfig(
            config.sample_rate,
            config.block_size,
            config.input_device_id,
            config.output_device_id,
            config.input_channels,
            config.output_channels,
            config.latency,
            realtime.native_callback_enabled(),
            realtime.linux_callback_priority(),
        )
        with silence_native_stderr():
            engine = _native_audio.AudioEngine(engine_config)

        enhancement_worker = EnhancementWorker(
            engine,
            enhancer,
            sample_rate=config.sample_rate,
            block_size=config.block_size,
            realtime_request=realtime.worker_request(),
        )

        print("Starting native audio...")

        stream_error: Exception | None = None
        interrupted = False
        engine_started = False
        worker_started = False
        native_stats: dict = {}

        try:
            with silence_native_stderr():
                engine.start()
            engine_started = True

            enhancement_worker.start()
            worker_started = True

            print("Streaming. Ctrl+C stops.")
            print()

            next_metrics_print = 0.0

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
                native_failure = _native_failure_from_stats(
                    native_stats,
                    realtime_mode=args.realtime,
                )
                if native_failure is not None:
                    stream_error = RuntimeError(native_failure)
                    enhancement_worker.stop()
                    break

                if not engine.is_active():
                    native_stats = dict(engine.stats())
                    native_failure = _native_failure_from_stats(
                        native_stats,
                        realtime_mode=args.realtime,
                    )
                    stream_error = RuntimeError(
                        native_failure
                        or "The native audio stream stopped unexpectedly."
                    )
                    enhancement_worker.stop()
                    break

                now = time.perf_counter()
                if now >= next_metrics_print:
                    print("\r" + enhancement_worker.metrics_line(), end="", flush=True)
                    next_metrics_print = now + 0.25

                time.sleep(0.1)

        except KeyboardInterrupt:
            interrupted = True
        except Exception as exc:
            stream_error = exc
        finally:
            worker_stopped = True
            if worker_started:
                worker_stopped = enhancement_worker.stop(timeout=2.0)

            if engine_started:
                try:
                    engine.stop()
                except Exception as exc:
                    if stream_error is None and not interrupted:
                        stream_error = exc

            try:
                native_stats = dict(engine.stats())
            except Exception:
                native_stats = {}

            if not worker_stopped and stream_error is None and not interrupted:
                stream_error = RuntimeError(
                    "The enhancement worker did not stop within 2 seconds. "
                    "Inference is stuck; the worker thread is daemonized so process exit is the hard termination boundary."
                )

        print()
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
            native_detail = _native_failure_from_stats(
                native_stats,
                realtime_mode=args.realtime,
            )
            if native_detail is not None and native_detail != detail:
                lines.append(f"Native state: {native_detail}")
            lines.append(list_devices_handoff("retry with fresh IDs if needed."))
            raise RuntimeError("\n".join(lines)) from stream_error

        native_warning_lines = _native_stats_warning_lines(native_stats)
        worker_warning_lines = enhancement_worker.warning_lines()
        realtime_warning_lines = [f"  - {line}" for line in realtime.warning_lines()]
        warning_lines = [
            *native_warning_lines,
            *worker_warning_lines,
            *realtime_warning_lines,
        ]

        print()
        if warning_lines:
            compact = ", ".join(
                line.strip().removeprefix("- ").replace(": ", "=")
                for line in warning_lines
            )
            print(f"Audio warnings: {compact}")
            print_block_tuning_next_step(config)
        else:
            print("Stopped cleanly.")
        print()

        return 0
