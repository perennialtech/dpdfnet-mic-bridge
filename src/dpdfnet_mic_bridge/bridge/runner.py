"""Live bridge stream lifecycle."""

from __future__ import annotations

import argparse
import platform
import sys
import threading
import time

import numpy as np

from ..models.enhancer import build_stream_enhancer
from .callback_state import CallbackStatusCounter
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


def run_bridge(
    args: argparse.Namespace,
    *,
    sd_mod,
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

    enhancement_worker = EnhancementWorker(
        enhancer,
        sample_rate=config.sample_rate,
    )
    status_counter = CallbackStatusCounter()
    fatal_callback_error: list[BaseException] = []
    callback_failed = threading.Event()

    def audio_callback(indata, outdata, frames, _time_info, status) -> None:
        try:
            status_counter.observe(status)

            if indata.shape[1] == 1:
                mono_in = indata[:, 0]
            else:
                mono_in = np.mean(indata, axis=1, dtype=np.float32)

            enhancement_worker.submit_input(mono_in)
            mono_out = enhancement_worker.pop_output(frames)

            outdata[:, :] = mono_out[:, None]

        except Exception as exc:
            if not fatal_callback_error:
                fatal_callback_error.append(exc)
            callback_failed.set()
            outdata.fill(0.0)
            raise sd_mod.CallbackAbort from exc

    print("Starting audio...")

    stream_error: Exception | None = None
    interrupted = False

    enhancement_worker.start()
    try:
        with sd_mod.Stream(
            samplerate=config.sample_rate,
            blocksize=config.block_size,
            device=(config.input_device_id, config.output_device_id),
            channels=(config.input_channels, config.output_channels),
            dtype="float32",
            latency=config.latency,
            callback=audio_callback,
        ) as stream:
            print("Streaming. Ctrl+C stops.")
            print()

            while True:
                if callback_failed.is_set():
                    stream_error = RuntimeError(
                        "The audio callback aborted unexpectedly."
                    )
                    break

                worker_failure = enhancement_worker.failure
                if worker_failure is not None:
                    stream_error = RuntimeError(
                        "The enhancement worker stopped unexpectedly."
                    )
                    break

                if not stream.active:
                    stream_error = RuntimeError(
                        "The audio stream stopped unexpectedly."
                    )
                    break

                time.sleep(0.1)

    except KeyboardInterrupt:
        interrupted = True
    except Exception as exc:
        stream_error = exc
    finally:
        enhancement_worker.stop()

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
        if fatal_callback_error:
            lines = ["The audio callback aborted unexpectedly."]
            detail = _first_line(str(fatal_callback_error[0]))
            if detail is not None:
                lines.append(f"Details: {detail}")
            lines.append(
                list_devices_handoff(
                    "retry, or restart with the block-size command printed above if timing warnings appeared."
                )
            )
            raise RuntimeError("\n".join(lines)) from fatal_callback_error[0]

        detail = _first_line(str(stream_error)) or str(stream_error)

        lines = ["Failed to open or run the audio stream.", f"Details: {detail}"]
        lines.append(list_devices_handoff("retry with fresh IDs if needed."))
        raise RuntimeError("\n".join(lines)) from stream_error

    nonzero_status_lines = status_counter.nonzero_lines()
    worker_warning_lines = enhancement_worker.warning_lines()
    warning_lines = [*nonzero_status_lines, *worker_warning_lines]
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
