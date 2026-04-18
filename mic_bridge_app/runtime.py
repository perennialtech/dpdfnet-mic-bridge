"""
Implementation of the `run` command.

This module owns:
- resolving a concrete RunConfig
- printing the run summary
- constructing the StreamEnhancer
- opening the duplex sounddevice stream
- the real-time callback
- shutdown reporting

Keep heavy architectural rationale in `ARCHITECTURE.md`. Keep parser setup in
`cli.py`. Keep host-API and probing policy in `audio_system.py`.

Refactor guardrail:
The callback path should stay direct and easy to audit. Avoid adding small
layers that obscure audio flow unless they clearly improve correctness.
"""

from __future__ import annotations

import argparse
import platform
import sys
import time

import numpy as np

from .audio_system import (
    format_jack_block_ms_hints,
    get_device_info,
    get_hostapi_names,
    hostapi_filter_key_for_name,
    hostapi_name_for_device,
    infer_device_tags,
    is_power_of_two,
    jack_block_ms_for_frames,
    negotiate_stream_channels,
    recommended_linux_jack_block_frames,
    resolve_effective_block_size,
    same_hostapi_guidance,
)
from .constants import DEFAULT_BLOCK_MS
from .dependencies import require_dpdfnet, require_sounddevice
from .domain import AudioFifo, CallbackStatusCounter, RunConfig
from .modeling import resolve_stream_sample_rate


def _eprint(*args: object) -> None:
    print(*args, file=sys.stderr)


def _build_run_config(args: argparse.Namespace) -> RunConfig:
    """
    Resolve CLI args into a concrete run configuration.

    Version 0 rules enforced here:
    - stream sample rate must equal model native sample rate
    - input and output devices must belong to the same host API
    - channel counts are negotiated conservatively, but input/output use
      different preference orders for practical reasons
    - Linux/JACK block sizing auto-adjusts when `--block-ms` is omitted
    """
    sd_mod = require_sounddevice()

    sample_rate = resolve_stream_sample_rate(args.model, args.samplerate, context="run")

    hostapi_names = get_hostapi_names(sd_mod)

    input_info = get_device_info(args.input)
    output_info = get_device_info(args.output)

    input_hostapi = hostapi_name_for_device(input_info, hostapi_names)
    output_hostapi = hostapi_name_for_device(output_info, hostapi_names)

    if int(input_info["hostapi"]) != int(output_info["hostapi"]):
        raise ValueError(
            "Input and output devices must use the same host API in Version 0.\n"
            f"Input : id={args.input}, hostapi={input_hostapi}, name={input_info['name']}\n"
            f"Output: id={args.output}, hostapi={output_hostapi}, name={output_info['name']}\n"
            f"{same_hostapi_guidance()}"
        )

    requested_block_ms = None if args.block_ms is None else float(args.block_ms)
    block_size, effective_block_ms, block_policy = resolve_effective_block_size(
        sample_rate=sample_rate,
        requested_block_ms=requested_block_ms,
        hostapi_name=input_hostapi,
    )

    input_channels = negotiate_stream_channels(
        device_id=args.input,
        direction="input",
        samplerate=sample_rate,
        dtype="float32",
    )
    output_channels = negotiate_stream_channels(
        device_id=args.output,
        direction="output",
        samplerate=sample_rate,
        dtype="float32",
    )

    input_name = str(input_info["name"])
    output_name = str(output_info["name"])

    return RunConfig(
        model_name=args.model,
        sample_rate=sample_rate,
        block_ms=effective_block_ms,
        requested_block_ms=requested_block_ms,
        block_policy=block_policy,
        block_size=block_size,
        input_device_id=int(args.input),
        input_device_name=input_name,
        input_hostapi_name=input_hostapi,
        input_channels=input_channels,
        input_tags=infer_device_tags(input_name),
        output_device_id=int(args.output),
        output_device_name=output_name,
        output_hostapi_name=output_hostapi,
        output_channels=output_channels,
        output_tags=infer_device_tags(output_name),
        latency=str(args.latency),
    )


def _print_run_summary(config: RunConfig) -> None:
    system = platform.system()
    input_tags = set(config.input_tags)
    output_tags = set(config.output_tags)
    input_hostapi_key = hostapi_filter_key_for_name(config.input_hostapi_name)

    if config.block_policy == "explicit":
        block_line = (
            f"{config.block_size} frames "
            f"({config.block_ms:.3f} ms effective, "
            f"{config.requested_block_ms:.3f} ms requested)"
        )
    elif config.block_policy == "auto-linux-jack":
        block_line = (
            f"{config.block_size} frames "
            f"({config.block_ms:.3f} ms auto-selected for Linux/JACK)"
        )
    else:
        block_line = f"{config.block_size} frames ({config.block_ms:.3f} ms default)"

    print()
    print("Run configuration")
    print("-----------------")
    print(f"Model                : {config.model_name}")
    print(f"Model/native SR      : {config.sample_rate} Hz")
    print(f"Block size           : {block_line}")
    print(f"Latency hint         : {config.latency}")
    print(
        f"Input device         : [{config.input_device_id}] {config.input_device_name}"
    )
    print(f"Input host API       : {config.input_hostapi_name}")
    print(f"Input channels open  : {config.input_channels}")
    print(
        f"Output device        : [{config.output_device_id}] {config.output_device_name}"
    )
    print(f"Output host API      : {config.output_hostapi_name}")
    print(f"Output channels open : {config.output_channels}")
    print()
    print("Notes:")
    print(
        "  - First enhanced output arrives after roughly one full model window (~20 ms)."
    )
    if config.input_channels > 1:
        print("  - Opened input will be downmixed to mono before enhancement.")
    if config.output_channels > 1:
        print("  - Enhanced mono will be duplicated across all opened output channels.")
    else:
        print("  - Enhanced mono will be written to a single opened output channel.")

    print("  - This script writes enhanced audio to your chosen output device.")

    if system == "Windows":
        print(
            "  - If that output device is VB-CABLE 'CABLE Input', then your apps should use"
        )
        print("    VB-CABLE 'CABLE Output' as the microphone.")
        if input_hostapi_key != "wasapi":
            print(
                "  - Windows note: WASAPI is the recommended host API starting point."
            )

    elif system == "Linux":
        print(
            "  - On Linux/PipeWire, if that output device is your virtual mic target,"
        )
        print("    apps should first try selecting it directly as the microphone.")
        print(
            "  - If a given app does not list it directly, try the matching monitor/source."
        )
        print(
            "  - Linux note: sounddevice reaches PipeWire through PortAudio host APIs,"
        )
        print("    usually a real PipeWire backend if available, otherwise PulseAudio,")
        print("    JACK, or ALSA.")
        if input_hostapi_key not in {"pipewire", "pulse", "jack", "alsa"}:
            print(
                "  - Linux note: a PipeWire-compatible PortAudio host API is recommended if available."
            )
        if "virtual-sink" not in output_tags and "dpdfnet" not in output_tags:
            print(
                "  - Linux note: for a virtual microphone flow, you usually want --output to be"
            )
            print("    a virtual mic target rather than speakers/headphones.")

        if "dpdfnet" in output_tags and config.output_channels == 1:
            print(
                "  - Linux note: a mono virtual mic target is fine here and avoids left/right routing surprises."
            )

        if input_hostapi_key == "jack":
            auto_frames = recommended_linux_jack_block_frames(config.sample_rate)
            auto_ms = jack_block_ms_for_frames(auto_frames, config.sample_rate)
            if config.block_policy == "auto-linux-jack":
                print(
                    "  - Linux/JACK note: because --block-ms was omitted, the script auto-selected"
                )
                print(
                    f"    {config.block_ms:.3f} ms ({config.block_size} frames), an exact JACK-friendly block size."
                )
            elif not is_power_of_two(config.block_size):
                print(
                    "  - Linux/JACK note: this block size is not an exact power-of-two frame count."
                )
                print(f"    If you hear stutter/crackle, try --block-ms {auto_ms:.3f}")
                print(
                    f"    or another exact JACK-friendly value such as {format_jack_block_ms_hints(config.sample_rate)} ms."
                )

    if (
        config.block_policy == "default-10ms"
        and abs(config.block_ms - DEFAULT_BLOCK_MS) > 1e-6
    ):
        print(
            "  - Note: block size is rounded to whole frames, so the effective block duration may differ slightly."
        )

    if "vb-cable-recording" in input_tags or "vb-cable-playback" in input_tags:
        print(
            "  - Warning: selected input looks like a VB-CABLE endpoint, not a physical microphone."
        )
    if "vb-cable-recording" in output_tags:
        print(
            "  - Warning: selected output looks like a recording endpoint. For VB-CABLE,"
            " you usually want 'CABLE Input' as --output."
        )
    if "monitor" in input_tags:
        print(
            "  - Warning: selected input looks like a monitor/source, not a physical microphone."
        )
    if system == "Linux" and "monitor" in output_tags:
        print(
            "  - Warning: selected output looks like a monitor/source, not a sink playback device."
        )
    print()


def run_command(args: argparse.Namespace) -> int:
    sd_mod = require_sounddevice()
    dpdfnet_mod = require_dpdfnet()

    if platform.system() not in ("Windows", "Linux"):
        _eprint(
            "[note] This script is primarily intended for Windows 10/11 and Linux PipeWire systems. It may still work elsewhere, but that is not the supported target."
        )

    config = _build_run_config(args)
    _print_run_summary(config)

    print("Preparing DPDFNet model...")
    model_start = time.perf_counter()
    enhancer = dpdfnet_mod.StreamEnhancer(
        model=config.model_name,
        verbose=bool(args.verbose),
    )
    model_elapsed = time.perf_counter() - model_start
    print(f"Model ready in {model_elapsed:.2f} s.")

    output_fifo = AudioFifo()
    status_counter = CallbackStatusCounter()
    fatal_callback_error: list[BaseException] = []

    def audio_callback(indata, outdata, frames, time_info, status) -> None:
        """
        Real-time audio callback.

        Key design constraints:
        - no printing here
        - no model loading here
        - no disk I/O here
        - no device queries here
        - keep logic straightforward

        Safe flow:
        1) downmix input to mono
        2) feed one chunk into StreamEnhancer
        3) push returned samples into FIFO
        4) pop exactly `frames` samples for playback
        5) duplicate mono across all output channels if needed
        """
        del time_info  # unused in Version 0

        try:
            status_counter.observe(status)

            if indata.ndim != 2:
                raise RuntimeError(
                    f"Expected 2D input buffer, got shape {indata.shape}"
                )

            if indata.shape[1] == 1:
                mono_in = np.asarray(indata[:, 0], dtype=np.float32)
            else:
                mono_in = np.mean(indata, axis=1, dtype=np.float32)

            enhanced = enhancer.process(mono_in, sample_rate=config.sample_rate)
            output_fifo.push(enhanced)

            mono_out = output_fifo.pop(frames)

            outdata.fill(0.0)
            outdata[:, :] = mono_out[:, None]

        except Exception as exc:
            if not fatal_callback_error:
                fatal_callback_error.append(exc)
            outdata.fill(0.0)
            raise sd_mod.CallbackAbort from exc

    print("Opening duplex audio stream...")

    try:
        with sd_mod.Stream(
            samplerate=config.sample_rate,
            blocksize=config.block_size,
            device=(config.input_device_id, config.output_device_id),
            channels=(config.input_channels, config.output_channels),
            dtype="float32",
            latency=config.latency,
            callback=audio_callback,
        ):
            print("Streaming.")
            print("Press Ctrl+C to stop.")
            print()
            while True:
                time.sleep(0.5)

    except KeyboardInterrupt:
        print()
        print("Stopping bridge...")
    except Exception as exc:
        if fatal_callback_error:
            raise RuntimeError(
                "The audio callback aborted due to an internal error."
            ) from fatal_callback_error[0]
        raise RuntimeError(f"Failed to open or run the audio stream: {exc}") from exc

    nonzero_status_lines = status_counter.nonzero_lines()
    if nonzero_status_lines:
        print()
        print("Callback status summary:")
        for line in nonzero_status_lines:
            print(line)
        print()
    else:
        print()
        print("No callback underflow/overflow flags were observed.")
        print()

    return 0
