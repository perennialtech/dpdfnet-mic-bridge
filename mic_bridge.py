#!/usr/bin/env python3
"""
Version 0 DPDFNet microphone bridge for Windows 10/11.

What this file does
-------------------
This script turns a physical microphone into a "cleaned" virtual microphone by
running DPDFNet in real time and sending the enhanced audio into a virtual cable
device such as VB-CABLE.

Target signal flow:

    physical mic -> this script -> VB-CABLE playback endpoint ("CABLE Input")
    chat/game/OBS app -> VB-CABLE recording endpoint ("CABLE Output")

Use case:
- Discord / Zoom / games / OBS / VOIP
- command-line-only
- Windows 10/11
- no plugin hosting
- no Equalizer APO
- no GUI
- no AGC
- no monitoring path
- no hotplug handling

Important implementation facts
------------------------------
1) We intentionally use `dpdfnet.StreamEnhancer`, not the raw ONNX inference
   code from the research repo.
   Reason:
   - DPDFNet's ONNX model is a spectral-frame + state model, not a simple
     waveform-in / waveform-out black box.
   - The package-level `StreamEnhancer` already wraps the causal STFT,
     overlap-add ISTFT, buffering, and state handling needed for real-time use.

2) Version 0 intentionally avoids live resampling.
   The stream sample rate MUST equal the model's native sample rate.
   For the recommended model `dpdfnet2_48khz_hr`, that means 48,000 Hz.

3) We use a single full-duplex `sounddevice.Stream`.
   Reason:
   - simpler timing model
   - fewer moving parts
   - no manual drift handling between separate input/output streams

4) The output FIFO in this file is important.
   Do not remove it just because the common case "looks" one-in / one-out.
   `StreamEnhancer.process()` can legally return fewer or more samples than one
   callback block, especially during startup or if non-hop-sized blocks are used.
   The FIFO prevents dropped/duplicated audio.

5) Device selection is numeric IDs only in Version 0.
   Reason:
   - avoids fuzzy name matching bugs
   - easier to debug
   - clearer for future scripting / automation

6) Same-host-API enforcement is deliberate.
   On Windows, mixed host APIs for a duplex stream create avoidable pain.
   The user should choose both devices from the same host API, ideally WASAPI.

Recommended first run
---------------------
1) Install VB-CABLE.
2) Set your physical mic and VB-CABLE to 48 kHz in Windows sound settings.
3) Install dependencies:

       pip install dpdfnet sounddevice

4) Download the recommended model once:

       dpdfnet download dpdfnet2_48khz_hr

5) List devices:

       python mic_bridge.py list-devices

6) Start the bridge, example:

       python mic_bridge.py run --input 5 --output 12 --model dpdfnet2_48khz_hr

   where:
   - --input  is your real microphone device ID
   - --output is the VB-CABLE playback endpoint device ID, typically "CABLE Input"

7) In Discord / OBS / your game:
   choose the VB-CABLE recording endpoint, typically "CABLE Output", as the mic.

Explicit non-goals for Version 0
--------------------------------
- GUI
- AGC
- equalizer APO
- VST/plugin wrapping
- model hot-switching while the stream is open
- device hotplug recovery
- background service / Windows startup
- packaging into an .exe
- native C++ rewrite

Future-maintainer / future-AI handoff notes
-------------------------------------------
If a future session wants to extend this file, these are the main architectural
facts that should not be rediscovered from scratch:

- DPDFNet live inference here is intentionally based on the *packaged* public API.
  This is not laziness, it is the correct abstraction boundary for Version 0.

- If someone later proposes "just call ONNX directly in the callback", remember:
  the repo showed that the ONNX model is not end-to-end waveform processing.
  You would need to own the surrounding causal STFT / ISTFT / state / buffering
  stack as well.

- If someone later proposes removing the output FIFO, don't. That change would
  be unsafe unless you also enforce exact hop-sized callback behavior and prove
  there is never any sample-count mismatch across startup and runtime.

- If someone later wants cross-sample-rate live support, do not casually add
  chunk-by-chunk resampling inside the callback without benchmark and glitch
  testing. Version 0 avoids that on purpose.

- If someone later wants separate input/output streams instead of one duplex
  stream, that is a separate design, not a trivial refactor. It reintroduces
  queueing, drift, and clock-domain concerns.

- If someone later adds GUI / AGC / monitoring / tray behavior, that should be
  layered on top of the same core bridge architecture, not mixed directly into
  the real-time callback.

Backwards compatibility
-----------------------
Backwards compatibility is explicitly not a concern for this file.
Prioritize clarity and correctness over preserving old interfaces.
"""

from __future__ import annotations

import argparse
from collections import deque
from dataclasses import dataclass
import platform
import sys
import time
from typing import Iterable, Optional

import numpy as np

try:
    import sounddevice as sd
except ImportError:
    sd = None  # type: ignore[assignment]

try:
    import dpdfnet
except ImportError:
    dpdfnet = None  # type: ignore[assignment]


MODEL_SAMPLE_RATES: dict[str, int] = {
    "baseline": 16000,
    "dpdfnet2": 16000,
    "dpdfnet4": 16000,
    "dpdfnet8": 16000,
    "dpdfnet2_48khz_hr": 48000,
    "dpdfnet8_48khz_hr": 48000,
}

DEFAULT_MODEL = "dpdfnet2_48khz_hr"
DEFAULT_BLOCK_MS = 10.0
DEFAULT_LATENCY = "low"

CALLBACK_STATUS_FLAG_NAMES: tuple[str, ...] = (
    "input_underflow",
    "input_overflow",
    "output_underflow",
    "output_overflow",
    "priming_output",
)


def _eprint(*args: object) -> None:
    print(*args, file=sys.stderr)


def _require_sounddevice():
    if sd is None:
        raise RuntimeError(
            "The 'sounddevice' package is not installed.\n"
            "Install it with:\n"
            "  pip install sounddevice"
        )
    return sd


def _require_dpdfnet():
    if dpdfnet is None:
        raise RuntimeError(
            "The 'dpdfnet' package is not installed.\n"
            "Install it with:\n"
            "  pip install dpdfnet"
        )
    return dpdfnet


def _model_native_sample_rate(model_name: str) -> int:
    try:
        return MODEL_SAMPLE_RATES[model_name]
    except KeyError as exc:
        supported = ", ".join(sorted(MODEL_SAMPLE_RATES))
        raise ValueError(
            f"Unsupported model '{model_name}'. Supported models: {supported}"
        ) from exc


def _format_samplerate(value: float) -> str:
    rounded = int(round(value))
    if abs(value - rounded) < 1e-6:
        return str(rounded)
    return f"{value:.1f}"


def _io_flags_for_device(info: dict) -> str:
    has_in = int(info["max_input_channels"]) > 0
    has_out = int(info["max_output_channels"]) > 0
    if has_in and has_out:
        return "IO"
    if has_in:
        return "I"
    if has_out:
        return "O"
    return "-"


def _device_default_marker(
    device_index: int, default_input: Optional[int], default_output: Optional[int]
) -> str:
    markers: list[str] = []
    if default_input is not None and device_index == default_input:
        markers.append("IN")
    if default_output is not None and device_index == default_output:
        markers.append("OUT")
    return ",".join(markers) if markers else "-"


def _get_hostapi_names(sd_mod) -> list[str]:
    hostapis = sd_mod.query_hostapis()
    return [str(item["name"]) for item in hostapis]


def _get_device_info(device_id: int) -> dict:
    sd_mod = _require_sounddevice()
    try:
        info = sd_mod.query_devices(device_id)
    except Exception as exc:
        raise ValueError(f"Invalid device ID {device_id}: {exc}") from exc
    return dict(info)


def _hostapi_name_for_device(info: dict, hostapi_names: list[str]) -> str:
    index = int(info["hostapi"])
    if 0 <= index < len(hostapi_names):
        return hostapi_names[index]
    return f"<unknown:{index}>"


def _iter_channel_candidates(max_channels: int) -> Iterable[int]:
    """
    Prefer mono first, then stereo, then native max as a last resort.

    Why:
    - mono is ideal for a speech denoiser
    - stereo often works if mono-open fails on quirky devices
    - max-channels is a final fallback for devices with odd constraints

    We keep the candidate set tiny on purpose.
    """
    seen: set[int] = set()
    for candidate in (1, 2, max_channels):
        if 1 <= candidate <= max_channels and candidate not in seen:
            seen.add(candidate)
            yield candidate


def _negotiate_stream_channels(
    *,
    device_id: int,
    direction: str,
    samplerate: int,
    dtype: str = "float32",
) -> int:
    """
    Pick a channel count that the selected device can open.

    For input:
    - we will downmix all opened channels to mono before enhancement

    For output:
    - we will duplicate the enhanced mono signal across all opened channels

    This is more robust than assuming every device likes 1 channel.
    """
    sd_mod = _require_sounddevice()
    info = _get_device_info(device_id)

    if direction == "input":
        max_channels = int(info["max_input_channels"])
        check = sd_mod.check_input_settings
    elif direction == "output":
        max_channels = int(info["max_output_channels"])
        check = sd_mod.check_output_settings
    else:
        raise ValueError(f"Unsupported direction '{direction}'")

    if max_channels < 1:
        raise RuntimeError(
            f"Selected {direction} device {device_id} exposes no {direction} channels."
        )

    failures: list[str] = []
    for channels in _iter_channel_candidates(max_channels):
        try:
            check(
                device=device_id,
                channels=channels,
                samplerate=samplerate,
                dtype=dtype,
            )
            return channels
        except Exception as exc:
            failures.append(f"{channels}ch -> {exc}")

    failure_text = "\n".join(f"  - {line}" for line in failures)
    raise RuntimeError(
        f"Could not open {direction} device {device_id} at {samplerate} Hz, dtype={dtype}.\n"
        f"Tried channel counts:\n{failure_text}"
    )


@dataclass(frozen=True)
class RunConfig:
    model_name: str
    sample_rate: int
    block_ms: float
    block_size: int
    input_device_id: int
    input_device_name: str
    input_hostapi_name: str
    input_channels: int
    output_device_id: int
    output_device_name: str
    output_hostapi_name: str
    output_channels: int
    latency: str


class AudioFifo:
    """
    Tiny float32 mono FIFO for callback-safe sample staging.

    Why this exists:
    `dpdfnet.StreamEnhancer.process()` may not always return exactly the same
    number of samples as the callback asked for. The FIFO lets us accept any
    returned chunk size without dropping or duplicating samples.
    """

    def __init__(self) -> None:
        self._chunks: deque[np.ndarray] = deque()
        self._size = 0

    @property
    def available(self) -> int:
        return self._size

    def push(self, samples: np.ndarray) -> None:
        x = np.asarray(samples, dtype=np.float32).reshape(-1)
        if x.size == 0:
            return
        self._chunks.append(x)
        self._size += int(x.size)

    def pop(self, count: int) -> np.ndarray:
        """
        Pop exactly `count` samples.

        If the FIFO does not have enough data, the remainder is zero-padded.
        """
        if count <= 0:
            return np.zeros(0, dtype=np.float32)

        out = np.zeros(count, dtype=np.float32)
        write_pos = 0

        while write_pos < count and self._chunks:
            chunk = self._chunks[0]
            take = min(count - write_pos, int(chunk.size))
            out[write_pos : write_pos + take] = chunk[:take]

            if take == chunk.size:
                self._chunks.popleft()
            else:
                self._chunks[0] = chunk[take:]

            self._size -= take
            write_pos += take

        return out


class CallbackStatusCounter:
    """
    Collect callback status events for a shutdown summary.

    We do not print inside the callback because printing in the audio callback
    is a bad real-time habit and can itself cause glitches.
    """

    def __init__(self) -> None:
        self.counts: dict[str, int] = {name: 0 for name in CALLBACK_STATUS_FLAG_NAMES}

    def observe(self, status) -> None:
        if not status:
            return
        for name in CALLBACK_STATUS_FLAG_NAMES:
            if bool(getattr(status, name, False)):
                self.counts[name] += 1

    def nonzero_lines(self) -> list[str]:
        lines: list[str] = []
        for name in CALLBACK_STATUS_FLAG_NAMES:
            count = self.counts.get(name, 0)
            if count:
                lines.append(f"  - {name}: {count}")
        return lines


def _build_run_config(args: argparse.Namespace) -> RunConfig:
    """
    Resolve CLI args into a concrete run configuration.

    Version 0 rules enforced here:
    - stream sample rate must equal model native sample rate
    - input and output devices must belong to the same host API
    - channel counts are negotiated conservatively
    """
    sd_mod = _require_sounddevice()

    if args.block_ms <= 0:
        raise ValueError("--block-ms must be > 0.")

    model_sr = _model_native_sample_rate(args.model)
    sample_rate = model_sr if args.samplerate is None else int(args.samplerate)

    if sample_rate != model_sr:
        raise ValueError(
            f"Version 0 does not do live resampling.\n"
            f"Model '{args.model}' expects {model_sr} Hz, but --samplerate={sample_rate} was requested.\n"
            f"Use --samplerate {model_sr} or omit --samplerate."
        )

    block_size = int(round(sample_rate * (float(args.block_ms) / 1000.0)))
    if block_size <= 0:
        raise ValueError(
            f"--block-ms={args.block_ms} produced invalid block_size={block_size}."
        )

    hostapi_names = _get_hostapi_names(sd_mod)

    input_info = _get_device_info(args.input)
    output_info = _get_device_info(args.output)

    input_hostapi = _hostapi_name_for_device(input_info, hostapi_names)
    output_hostapi = _hostapi_name_for_device(output_info, hostapi_names)

    if int(input_info["hostapi"]) != int(output_info["hostapi"]):
        raise ValueError(
            "Input and output devices must use the same host API in Version 0.\n"
            f"Input : id={args.input}, hostapi={input_hostapi}, name={input_info['name']}\n"
            f"Output: id={args.output}, hostapi={output_hostapi}, name={output_info['name']}\n"
            "Choose two devices from the same host API, ideally WASAPI on Windows."
        )

    input_channels = _negotiate_stream_channels(
        device_id=args.input,
        direction="input",
        samplerate=sample_rate,
        dtype="float32",
    )
    output_channels = _negotiate_stream_channels(
        device_id=args.output,
        direction="output",
        samplerate=sample_rate,
        dtype="float32",
    )

    return RunConfig(
        model_name=args.model,
        sample_rate=sample_rate,
        block_ms=float(args.block_ms),
        block_size=block_size,
        input_device_id=int(args.input),
        input_device_name=str(input_info["name"]),
        input_hostapi_name=input_hostapi,
        input_channels=input_channels,
        output_device_id=int(args.output),
        output_device_name=str(output_info["name"]),
        output_hostapi_name=output_hostapi,
        output_channels=output_channels,
        latency=str(args.latency),
    )


def list_devices_command(_args: argparse.Namespace) -> int:
    sd_mod = _require_sounddevice()

    if platform.system() != "Windows":
        _eprint(
            "[note] This script is designed for Windows 10/11, though device listing may still work elsewhere."
        )

    devices = sd_mod.query_devices()
    hostapi_names = _get_hostapi_names(sd_mod)

    default_input: Optional[int]
    default_output: Optional[int]
    try:
        default_pair = sd_mod.default.device
        default_input = None if default_pair[0] in (None, -1) else int(default_pair[0])
        default_output = None if default_pair[1] in (None, -1) else int(default_pair[1])
    except Exception:
        default_input = None
        default_output = None

    headers = [
        "ID",
        "Default",
        "I/O",
        "Host API",
        "In Ch",
        "Out Ch",
        "Default SR",
        "Name",
    ]

    rows: list[list[str]] = []
    for device_id, info in enumerate(devices):
        info = dict(info)
        rows.append(
            [
                str(device_id),
                _device_default_marker(device_id, default_input, default_output),
                _io_flags_for_device(info),
                _hostapi_name_for_device(info, hostapi_names),
                str(int(info["max_input_channels"])),
                str(int(info["max_output_channels"])),
                _format_samplerate(float(info["default_samplerate"])),
                str(info["name"]),
            ]
        )

    col_widths = [
        max(len(headers[i]), *(len(row[i]) for row in rows))
        for i in range(len(headers))
    ]

    def fmt_row(values: list[str]) -> str:
        return "  ".join(values[i].ljust(col_widths[i]) for i in range(len(values)))

    print()
    print(fmt_row(headers))
    print(fmt_row(["-" * width for width in col_widths]))
    for row in rows:
        print(fmt_row(row))
    print()

    print("Windows usage notes:")
    print(
        "  - Prefer choosing input and output devices from the same Host API, ideally WASAPI."
    )
    print("  - For VB-CABLE:")
    print(
        "      * this script should OUTPUT to the playback endpoint, usually named 'CABLE Input'"
    )
    print(
        "      * your apps should READ from the recording endpoint, usually named 'CABLE Output'"
    )
    print("  - Start with the 48 kHz model: dpdfnet2_48khz_hr")
    print()

    return 0


def _print_run_summary(config: RunConfig) -> None:
    print()
    print("Starting DPDFNet microphone bridge")
    print("---------------------------------")
    print(f"Model                : {config.model_name}")
    print(f"Model/native SR      : {config.sample_rate} Hz")
    print(
        f"Block size           : {config.block_size} samples ({config.block_ms:.3f} ms requested)"
    )
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
    print("  - This script writes enhanced audio to your chosen output device.")
    print(
        "  - If that output device is VB-CABLE 'CABLE Input', then your apps should use"
    )
    print("    VB-CABLE 'CABLE Output' as the microphone.")
    if abs(config.block_ms - 10.0) > 1e-6:
        print(
            "  - 10 ms blocks are the recommended starting point for DPDFNet streaming."
        )
    print()
    print("Press Ctrl+C to stop.")
    print()


def run_command(args: argparse.Namespace) -> int:
    sd_mod = _require_sounddevice()
    dpdfnet_mod = _require_dpdfnet()

    if platform.system() != "Windows":
        _eprint(
            "[note] This script targets Windows 10/11. It may still work elsewhere, but that is not the supported target."
        )

    config = _build_run_config(args)
    _print_run_summary(config)

    # Model construction happens once, before the stream opens.
    # This is where auto-download may happen if the model is not cached yet.
    enhancer = dpdfnet_mod.StreamEnhancer(
        model=config.model_name,
        verbose=bool(args.verbose),
    )

    output_fifo = AudioFifo()
    status_counter = CallbackStatusCounter()
    fatal_callback_errors: list[BaseException] = []

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

            # `outdata` is always shape [frames, channels].
            # Duplicate enhanced mono across all opened output channels.
            outdata.fill(0.0)
            outdata[:, :] = mono_out[:, None]

        except Exception as exc:
            fatal_callback_errors.append(exc)
            outdata.fill(0.0)
            raise sd_mod.CallbackAbort from exc

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
            while True:
                time.sleep(0.5)

    except KeyboardInterrupt:
        print()
        print("Stopping bridge...")
    except Exception as exc:
        if fatal_callback_errors:
            raise RuntimeError(
                "The audio callback aborted due to an internal error."
            ) from fatal_callback_errors[0]
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Command-line DPDFNet microphone bridge for Windows 10/11.\n"
            "Use 'list-devices' first, then 'run' with numeric device IDs."
        ),
        formatter_class=argparse.RawTextHelpFormatter,
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser(
        "list-devices",
        help="List audio devices with Windows-friendly host API info.",
    )

    run_parser = subparsers.add_parser(
        "run",
        help="Run the live microphone -> DPDFNet -> output-device bridge.",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    run_parser.add_argument(
        "--input",
        type=int,
        required=True,
        help="Numeric input device ID, typically your physical microphone.",
    )
    run_parser.add_argument(
        "--output",
        type=int,
        required=True,
        help=(
            "Numeric output device ID.\n"
            "For VB-CABLE this should usually be the playback endpoint named 'CABLE Input'."
        ),
    )
    run_parser.add_argument(
        "--model",
        type=str,
        choices=sorted(MODEL_SAMPLE_RATES),
        default=DEFAULT_MODEL,
        help=f"DPDFNet model to use. Default: {DEFAULT_MODEL}",
    )
    run_parser.add_argument(
        "--samplerate",
        type=int,
        default=None,
        help=(
            "Stream sample rate in Hz. In Version 0 this must match the model's native "
            "sample rate exactly. If omitted, the model's native rate is used."
        ),
    )
    run_parser.add_argument(
        "--block-ms",
        type=float,
        default=DEFAULT_BLOCK_MS,
        help=(
            "Callback block size in milliseconds. Default: 10.0\n"
            "10 ms is the recommended first setting."
        ),
    )
    run_parser.add_argument(
        "--latency",
        type=str,
        choices=("low", "high"),
        default=DEFAULT_LATENCY,
        help=f"sounddevice latency hint. Default: {DEFAULT_LATENCY}",
    )
    run_parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose DPDFNet model resolution / download logging.",
    )

    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "list-devices":
            return list_devices_command(args)
        if args.command == "run":
            return run_command(args)
    except Exception as exc:
        _eprint()
        _eprint(f"Error: {exc}")
        _eprint()
        return 2

    _eprint("Internal error: unhandled command.")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
