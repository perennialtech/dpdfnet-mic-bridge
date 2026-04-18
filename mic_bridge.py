#!/usr/bin/env python3
"""
Version 0 DPDFNet microphone bridge for Windows 10/11 and Linux PipeWire setups.

What this file does
-------------------
This script turns a physical microphone into a "cleaned" virtual microphone by
running DPDFNet in real time and sending the enhanced audio into a virtual audio
endpoint.

Typical signal flows:

Windows:
    physical mic -> this script -> VB-CABLE playback endpoint ("CABLE Input")
    chat/game/OBS app -> VB-CABLE recording endpoint ("CABLE Output")

Linux with PipeWire:
    physical mic -> this script -> PipeWire virtual mic target created via
    `module-null-sink`
    chat/game/OBS app -> that same virtual mic target, often directly as
    "DPDFNet_Mic", or in some app stacks via its matching monitor/source

Supported target environments
-----------------------------
- Windows 10/11, ideally with WASAPI devices
- Linux desktops running PipeWire, via the PortAudio host APIs exposed to
  `sounddevice`
- command-line-only
- no plugin hosting
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
   For the recommended model `dpdfnet8_48khz_hr`, that means 48,000 Hz.

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

5) Live device selection remains numeric IDs only in Version 0.
   Reason:
   - avoids fuzzy name matching bugs
   - easier to debug
   - clearer for future scripting / automation

   The CLI may print best-effort hints, tags, or a likely first-run command,
   but actual device selection remains ID-based.

6) Same-host-API enforcement is deliberate.
   On Windows and Linux via PortAudio, mixed host APIs for a duplex stream
   create avoidable pain.
   The user should choose both devices from the same host API.
   - Windows: ideally WASAPI
   - Linux: ideally a real PipeWire PortAudio backend if present,
     otherwise PulseAudio, then JACK, then ALSA

7) `list-devices` is optimized for first-run usability on Windows and Linux.
   By default it shows recommended host-API candidates, probes whether devices
   can open at the selected model sample rate, and prints a likely first-run
   command when the choice looks obvious.
   - Windows default filter: WASAPI
   - Linux default filter: PipeWire if present, otherwise PulseAudio, then
     JACK, then ALSA
   Use `--all-apis` to show every PortAudio host API instead.

8) Linux PipeWire support here is not native PipeWire graph management.
   Reason:
   - `sounddevice` talks to PortAudio
   - stock PortAudio usually reaches PipeWire through compatibility layers,
     not through a dedicated native PipeWire host API
   - so this script can work well on PipeWire systems, but it does not create
     or manage PipeWire nodes itself

9) Version 0 deliberately does not add a separate `validate` command.
   This is a design decision, not an omission by accident.
   Reason:
   - keeps the CLI surface small
   - keeps preflight lightweight
   - pushes discovery into `list-devices`
   - uses `run` as the quick smoke test

10) On Linux/JACK, the default block sizing is intentionally *not* raw 10.0 ms
    when `--block-ms` is omitted.
    Reason:
    - a plain 10.0 ms block at 48 kHz is 480 frames
    - Python + PortAudio + JACK often behaves better with exact JACK-friendly
      frame counts
    - at 48 kHz, the auto-selected Linux/JACK default becomes 1024 frames,
      which is 21.333 ms
    This change exists because the 10.0 ms / 480-frame default can audibly
    stutter on real systems even when the rest of the pipeline is correct.

11) Output channel negotiation intentionally prefers stereo before mono.
    Reason:
    - the bridge output is always enhanced mono
    - when we open a stereo-ish sink as 2 channels, we can safely duplicate the
      mono signal into both channels
    - opening some Linux virtual outputs as 1 channel can map audio to left-only
    This preference is for *output* only. Input negotiation still prefers mono.

12) The recommended Linux virtual mic target creation command intentionally uses
    `media.class=Audio/Source/Virtual` plus a mono channel layout.
    Reason:
    - many apps, including OBS on some PipeWire desktops, discover that target
      more naturally as a microphone-like source
    - forcing the target to mono avoids left/right mapping surprises
    - the bridge still writes to it as an output device from PortAudio's point
      of view, which is a little counterintuitive but works well in practice

Recommended first run on Windows
--------------------------------
1) Install VB-CABLE.
2) Set your physical mic and VB-CABLE to 48 kHz in Windows sound settings.
3) Install dependencies:

       pip install dpdfnet sounddevice

4) Download the recommended model once:

       dpdfnet download dpdfnet8_48khz_hr

5) List recommended devices:

       python mic_bridge.py list-devices

   By default on Windows, this shows recommended WASAPI candidates for the
   selected model/sample rate. Use `--all-apis` to show every PortAudio device.

6) Start the bridge:

       python mic_bridge.py run --input 18 --output 16

   where:
   - --input  is your real microphone device ID
   - --output is the VB-CABLE playback endpoint device ID, typically
     "CABLE Input"

7) In Discord / OBS / your game:
   choose the VB-CABLE recording endpoint, typically "CABLE Output", as the mic.

Recommended first run on Linux/PipeWire
---------------------------------------
1) Create a virtual mic target.
   The recommended command is:

       pactl load-module module-null-sink media.class=Audio/Source/Virtual sink_name=dpdfnet_mic sink_properties=device.description=DPDFNet_Mic channels=1 channel_map=mono

   Why this exact form:
   - `media.class=Audio/Source/Virtual` helps many apps expose it more like a
     microphone
   - `channels=1 channel_map=mono` keeps it explicitly mono, which is ideal for
     speech and avoids left-only surprises

2) Install dependencies:

       pip install dpdfnet sounddevice

3) Download the recommended model once:

       dpdfnet download dpdfnet8_48khz_hr

4) List recommended devices:

       python mic_bridge.py list-devices

   By default on Linux, this prefers a PipeWire host API if your PortAudio build
   exposes one, otherwise PulseAudio, then JACK, then ALSA. Use `--all-apis` to
   show every PortAudio device.

5) Start the bridge:

       python mic_bridge.py run --input 18 --output 16

   where:
   - --input  is your real microphone device ID
   - --output is the virtual mic target's playback/output device ID

   If the selected host API ends up being JACK and you omit `--block-ms`,
   Version 0 auto-selects a JACK-friendly exact block size. For 48 kHz models
   that is typically 21.333 ms, which has proven more robust than raw 10.0 ms.

6) In Discord / OBS / your app:
   first try choosing `DPDFNet_Mic` directly as the microphone.
   If a given app does not expose it directly, try the matching monitor/source
   instead.

Explicit non-goals for Version 0
--------------------------------
- GUI
- AGC
- plugin hosting
- model hot-switching while the stream is open
- device hotplug recovery
- background service / OS startup integration
- separate `validate` subcommand
- timed runs (`--duration` flag was explicitly removed as a design decision)
- packaging into an .exe
- native C++ rewrite
- native PipeWire node/graph management

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

- Linux/PipeWire support here deliberately stays at the PortAudio layer.
  If someone later wants native PipeWire graph construction, virtual node
  creation, metadata control, or link management, that is a separate feature
  set and likely a separate integration surface.

- The Linux virtual mic target recommendation uses a specific compatibility trick:
  `pactl load-module module-null-sink media.class=Audio/Source/Virtual ...`
  This is not an arbitrary shell one-liner. It exists because it improved
  discoverability in apps like OBS during real testing.

- The `channels=1 channel_map=mono` part of that Linux command is also
  deliberate. It matches speech use well and avoids confusing left/right mapping
  behavior in some application stacks.

- Output channel probing deliberately prefers 2 channels before 1.
  Do not "simplify" that back to mono-first.
  Mono-first is still correct for *input*, but for *output* it can cause
  left-only playback on some Linux virtual devices and sinks.

- Linux/JACK default block sizing is deliberately auto-adjusted when
  `--block-ms` is omitted.
  At 48 kHz the auto choice is 1024 frames = 21.333 ms.
  This was added after real-world stutter reports with the nominal 10.0 ms
  / 480-frame setting. Do not remove that behavior casually.

- If someone later wants a separate `validate` command, treat that as a
  deliberate CLI design expansion. Version 0 intentionally keeps the workflow to
  `list-devices` and `run`, with manual Ctrl+C for the smoke-test path.
  A `--duration` flag was also explicitly removed from `run` to keep the CLI simple.

Backwards compatibility
-----------------------
Backwards compatibility is explicitly not a concern for this file.
Prioritize clarity and correctness over preserving old interfaces.
"""

from __future__ import annotations

import argparse
import math
import platform
import sys
import time
from collections import deque
from dataclasses import dataclass
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

DEFAULT_MODEL = "dpdfnet8_48khz_hr"
DEFAULT_BLOCK_MS = 10.0
DEFAULT_LATENCY = "high"

LINUX_VIRTUAL_MIC_SINK_NAME = "dpdfnet_mic"
LINUX_VIRTUAL_MIC_DESCRIPTION = "DPDFNet_Mic"
# On Linux/JACK, Python + PortAudio tends to behave more reliably when we give
# it a somewhat roomier exact block size instead of raw 10.0 ms.
LINUX_JACK_AUTO_MIN_BLOCK_MS = 20.0
LINUX_JACK_AUTO_MIN_BLOCK_FRAMES = 512
JACK_BLOCK_HINT_FRAMES: tuple[int, ...] = (256, 512, 1024)

LIST_DEVICE_HOSTAPI_CHOICES: tuple[str, ...] = (
    "pipewire",
    "pulse",
    "jack",
    "alsa",
    "wasapi",
    "mme",
    "directsound",
    "wdm-ks",
    "all",
)

HOSTAPI_FILTER_DISPLAY_NAMES: dict[str, str] = {
    "pipewire": "PipeWire",
    "pulse": "PulseAudio",
    "jack": "JACK",
    "alsa": "ALSA",
    "wasapi": "Windows WASAPI",
    "mme": "MME",
    "directsound": "Windows DirectSound",
    "wdm-ks": "Windows WDM-KS",
    "all": "all PortAudio host APIs",
}

HOSTAPI_FILTER_ALIASES: dict[str, tuple[str, ...]] = {
    "pipewire": ("pipewire",),
    "pulse": ("pulseaudio",),
    "jack": ("jack audio connection kit", "jack"),
    "alsa": ("alsa",),
    "wasapi": ("windows wasapi", "wasapi"),
    "mme": ("mme",),
    "directsound": ("windows directsound", "directsound"),
    "wdm-ks": ("windows wdm-ks", "wdm ks"),
}

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


def _resolve_stream_sample_rate(
    model_name: str, samplerate: Optional[int], *, context: str
) -> int:
    model_sr = _model_native_sample_rate(model_name)
    sample_rate = model_sr if samplerate is None else int(samplerate)

    if sample_rate != model_sr:
        raise ValueError(
            f"Version 0 does not do live resampling.\n"
            f"Model '{model_name}' expects {model_sr} Hz, but --samplerate={sample_rate} was requested for {context}.\n"
            f"Use --samplerate {model_sr} or omit --samplerate."
        )

    return sample_rate


def _format_samplerate(value: float) -> str:
    rounded = int(round(value))
    if abs(value - rounded) < 1e-6:
        return str(rounded)
    return f"{value:.1f}"


def _format_tags(tags: tuple[str, ...]) -> str:
    return ", ".join(tags) if tags else "-"


def _normalize_hostapi_name(name: str) -> str:
    return " ".join(name.casefold().replace("_", " ").replace("-", " ").split())


def _get_hostapi_names(sd_mod) -> list[str]:
    hostapis = sd_mod.query_hostapis()
    return [str(item["name"]) for item in hostapis]


def _hostapi_filter_key_for_name(hostapi_name: str) -> Optional[str]:
    normalized = _normalize_hostapi_name(hostapi_name)
    for filter_name, aliases in HOSTAPI_FILTER_ALIASES.items():
        for alias in aliases:
            normalized_alias = _normalize_hostapi_name(alias)
            if normalized == normalized_alias or normalized_alias in normalized:
                return filter_name
    return None


def _available_known_hostapi_filters(hostapi_names: list[str]) -> set[str]:
    available: set[str] = set()
    for hostapi_name in hostapi_names:
        key = _hostapi_filter_key_for_name(hostapi_name)
        if key is not None:
            available.add(key)
    return available


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


def _hostapi_matches_filter(hostapi_name: str, filter_name: str) -> bool:
    if filter_name == "all":
        return True
    return _hostapi_filter_key_for_name(hostapi_name) == filter_name


def _resolve_list_hostapi_filter(
    args: argparse.Namespace, hostapi_names: list[str]
) -> str:
    if args.all_apis:
        if args.hostapi not in (None, "all"):
            raise ValueError("Use either --all-apis or --hostapi, not both.")
        return "all"

    if args.hostapi is not None:
        return str(args.hostapi)

    system = platform.system()
    available = _available_known_hostapi_filters(hostapi_names)

    if system == "Windows":
        return "wasapi" if "wasapi" in available else "all"

    if system == "Linux":
        for candidate in ("pipewire", "pulse", "jack", "alsa"):
            if candidate in available:
                return candidate
        return "all"

    return "all"


def _hostapi_preference_score(hostapi_name: str) -> int:
    normalized = _normalize_hostapi_name(hostapi_name)

    if normalized == "pipewire":
        return 110
    if normalized == "windows wasapi":
        return 100
    if normalized == "pulseaudio":
        return 95
    if normalized in ("jack audio connection kit", "jack"):
        return 80
    if normalized == "windows wdm ks":
        return 80
    if normalized == "alsa":
        return 60
    if normalized == "windows directsound":
        return 40
    if normalized == "mme":
        return 20
    return 0


def _infer_device_tags(name: str) -> tuple[str, ...]:
    text = name.casefold()
    tags: list[str] = []

    is_vb_audio = (
        "vb-audio" in text
        or "vb-cable" in text
        or "cable input" in text
        or "cable output" in text
    )
    if is_vb_audio:
        tags.append("virtual")

    if "cable input" in text:
        tags.append("vb-cable-playback")

    if "cable output" in text:
        tags.append("vb-cable-recording")

    if "monitor of" in text or ".monitor" in text:
        tags.append("virtual")
        tags.append("monitor")

    if "null sink" in text or "virtual sink" in text:
        tags.append("virtual")
        tags.append("virtual-sink")

    if "virtual source" in text:
        tags.append("virtual")
        tags.append("virtual-source")

    if "loopback" in text:
        tags.append("virtual")

    if "dpdfnet" in text:
        tags.append("dpdfnet")

    if "microphone" in text or " mic" in text or text.startswith("mic "):
        tags.append("mic")

    if "headphone" in text or "headset" in text:
        tags.append("headphones")

    if "speaker" in text:
        tags.append("speakers")

    # Preserve insertion order while removing duplicates.
    deduped: list[str] = []
    seen: set[str] = set()
    for tag in tags:
        if tag not in seen:
            seen.add(tag)
            deduped.append(tag)

    return tuple(deduped)


def _iter_input_channel_candidates(max_channels: int) -> Iterable[int]:
    """
    Prefer mono first, then stereo, then native max as a last resort.

    Why:
    - mono is ideal for a speech denoiser
    - stereo often works if mono-open fails on quirky devices
    - max-channels is a final fallback for devices with odd constraints
    """
    seen: set[int] = set()
    for candidate in (1, 2, max_channels):
        if 1 <= candidate <= max_channels and candidate not in seen:
            seen.add(candidate)
            yield candidate


def _iter_output_channel_candidates(max_channels: int) -> Iterable[int]:
    """
    Prefer stereo first, then mono, then native max as a last resort.

    Why:
    - the bridge output is enhanced mono
    - if we open 2 output channels, we duplicate mono into both channels
    - opening a stereo-ish sink as 1 channel can map to left-only on some
      Linux virtual devices and sinks
    - max-channels is still a fallback for odd devices
    """
    seen: set[int] = set()
    for candidate in (2, 1, max_channels):
        if 1 <= candidate <= max_channels and candidate not in seen:
            seen.add(candidate)
            yield candidate


def _probe_device_channels(
    *,
    device_id: int,
    direction: str,
    samplerate: int,
    dtype: str = "float32",
) -> tuple[Optional[int], tuple[str, ...]]:
    sd_mod = _require_sounddevice()
    info = _get_device_info(device_id)

    if direction == "input":
        max_channels = int(info["max_input_channels"])
        check = sd_mod.check_input_settings
        channel_candidates = _iter_input_channel_candidates(max_channels)
    elif direction == "output":
        max_channels = int(info["max_output_channels"])
        check = sd_mod.check_output_settings
        channel_candidates = _iter_output_channel_candidates(max_channels)
    else:
        raise ValueError(f"Unsupported direction '{direction}'")

    if max_channels < 1:
        return None, (f"device exposes no {direction} channels",)

    failures: list[str] = []
    for channels in channel_candidates:
        try:
            check(
                device=device_id,
                channels=channels,
                samplerate=samplerate,
                dtype=dtype,
            )
            return channels, tuple()
        except Exception as exc:
            failures.append(f"{channels}ch -> {exc}")

    return None, tuple(failures)


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
    - we prefer mono, then stereo, then native max
    - any opened input will be downmixed to mono before enhancement

    For output:
    - we prefer stereo, then mono, then native max
    - enhanced mono will be duplicated across every opened output channel

    The input/output preference split is deliberate. Mono is ideal for capture,
    but mono-open on some output devices can produce left-only audio.
    """
    channels, failures = _probe_device_channels(
        device_id=device_id,
        direction=direction,
        samplerate=samplerate,
        dtype=dtype,
    )

    if channels is not None:
        return channels

    failure_text = "\n".join(f"  - {line}" for line in failures)
    raise RuntimeError(
        f"Could not open {direction} device {device_id} at {samplerate} Hz, dtype={dtype}.\n"
        f"Tried channel counts:\n{failure_text}"
    )


def _same_hostapi_guidance() -> str:
    system = platform.system()
    if system == "Windows":
        return "Choose two devices from the same host API, ideally WASAPI on Windows."
    if system == "Linux":
        return (
            "Choose two devices from the same host API, ideally PipeWire if your "
            "PortAudio build exposes it, otherwise PulseAudio, then JACK, then ALSA "
            "on Linux/PipeWire."
        )
    return "Choose two devices from the same host API."


def _linux_virtual_mic_command() -> str:
    """
    Return the recommended PipeWire/PulseAudio compatibility command used in the
    Linux docs and usage notes.

    This is intentionally very specific:
    - `media.class=Audio/Source/Virtual` improves discoverability in apps
    - `channels=1 channel_map=mono` keeps the target explicitly mono
    """
    return (
        "pactl load-module module-null-sink "
        "media.class=Audio/Source/Virtual "
        f"sink_name={LINUX_VIRTUAL_MIC_SINK_NAME} "
        f"sink_properties=device.description={LINUX_VIRTUAL_MIC_DESCRIPTION} "
        "channels=1 channel_map=mono"
    )


def _next_power_of_two(value: int) -> int:
    if value <= 1:
        return 1
    return 1 << (value - 1).bit_length()


def _is_power_of_two(value: int) -> bool:
    return value > 0 and (value & (value - 1)) == 0


def _jack_block_ms_for_frames(frames: int, sample_rate: int) -> float:
    return (float(frames) * 1000.0) / float(sample_rate)


def _format_jack_block_ms_hints(sample_rate: int) -> str:
    return ", ".join(
        f"{_jack_block_ms_for_frames(frames, sample_rate):.3f}"
        for frames in JACK_BLOCK_HINT_FRAMES
    )


def _recommended_linux_jack_block_frames(sample_rate: int) -> int:
    """
    Choose a forgiving exact block size for Linux/JACK when the user did not
    explicitly request one.

    Why not raw 10.0 ms?
    - at 48 kHz, 10.0 ms is 480 frames, which is not a typical JACK-friendly
      exact frame count
    - Python + PortAudio often behaves better with exact frame counts here
    - using a roomier default reduces real-world stutter/crackle reports

    Current rule:
    - at least ~20 ms worth of audio
    - at least 512 frames
    - rounded up to the next power of two
    """
    target_frames = max(
        int(math.ceil(sample_rate * (LINUX_JACK_AUTO_MIN_BLOCK_MS / 1000.0))),
        LINUX_JACK_AUTO_MIN_BLOCK_FRAMES,
    )
    return _next_power_of_two(target_frames)


def _resolve_effective_block_size(
    *,
    sample_rate: int,
    requested_block_ms: Optional[float],
    hostapi_name: str,
) -> tuple[int, float, str]:
    """
    Resolve the stream block size to an exact frame count.

    Returns:
        (block_size_frames, effective_block_ms, policy_name)

    Policy names:
    - explicit
    - default-10ms
    - auto-linux-jack
    """
    if requested_block_ms is not None:
        if requested_block_ms <= 0:
            raise ValueError("--block-ms must be > 0.")
        block_size = int(round(sample_rate * (requested_block_ms / 1000.0)))
        if block_size <= 0:
            raise ValueError(
                f"--block-ms={requested_block_ms} produced invalid block_size={block_size}."
            )
        effective_ms = _jack_block_ms_for_frames(block_size, sample_rate)
        return block_size, effective_ms, "explicit"

    hostapi_key = _hostapi_filter_key_for_name(hostapi_name)
    if platform.system() == "Linux" and hostapi_key == "jack":
        block_size = _recommended_linux_jack_block_frames(sample_rate)
        effective_ms = _jack_block_ms_for_frames(block_size, sample_rate)
        return block_size, effective_ms, "auto-linux-jack"

    block_size = int(round(sample_rate * (DEFAULT_BLOCK_MS / 1000.0)))
    effective_ms = _jack_block_ms_for_frames(block_size, sample_rate)
    return block_size, effective_ms, "default-10ms"


@dataclass(frozen=True)
class DeviceProbe:
    device_id: int
    direction: str
    name: str
    hostapi_name: str
    default_samplerate: float
    max_channels: int
    open_channels: Optional[int]
    probe_failures: tuple[str, ...]
    tags: tuple[str, ...]


@dataclass(frozen=True)
class RunConfig:
    model_name: str
    sample_rate: int
    block_ms: float
    requested_block_ms: Optional[float]
    block_policy: str
    block_size: int
    input_device_id: int
    input_device_name: str
    input_hostapi_name: str
    input_channels: int
    input_tags: tuple[str, ...]
    output_device_id: int
    output_device_name: str
    output_hostapi_name: str
    output_channels: int
    output_tags: tuple[str, ...]
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


def _score_input_probe(probe: DeviceProbe) -> int:
    if probe.direction != "input":
        raise ValueError("Input scoring called on non-input probe.")

    if probe.open_channels is None:
        return -10_000

    tags = set(probe.tags)
    score = _hostapi_preference_score(probe.hostapi_name)

    if "mic" in tags:
        score += 40
    if "monitor" in tags:
        score -= 50
    if "dpdfnet" in tags:
        score -= 30
    if "virtual" in tags:
        score -= 20
    if "vb-cable-recording" in tags or "vb-cable-playback" in tags:
        score -= 80
    if probe.max_channels <= 2:
        score += 5
    if "primary sound capture driver" in probe.name.casefold():
        score -= 30

    return score


def _score_output_probe(probe: DeviceProbe) -> int:
    if probe.direction != "output":
        raise ValueError("Output scoring called on non-output probe.")

    if probe.open_channels is None:
        return -10_000

    tags = set(probe.tags)
    score = _hostapi_preference_score(probe.hostapi_name)

    if "vb-cable-playback" in tags:
        score += 60
    if "virtual-sink" in tags:
        score += 45
    if "dpdfnet" in tags:
        score += 40
    if "vb-cable-recording" in tags:
        score -= 80
    if "monitor" in tags:
        score -= 80
    if "headphones" in tags or "speakers" in tags:
        score += 10
    if (
        "virtual" in tags
        and "vb-cable-playback" not in tags
        and "virtual-sink" not in tags
        and "dpdfnet" not in tags
    ):
        score -= 10
    if "primary sound driver" in probe.name.casefold():
        score -= 30

    return score


def _probe_sort_key(probe: DeviceProbe) -> tuple[bool, int, int]:
    if probe.direction == "input":
        score = _score_input_probe(probe)
    else:
        score = _score_output_probe(probe)

    return (probe.open_channels is None, -score, probe.device_id)


def _build_device_probes(
    *,
    devices,
    hostapi_names: list[str],
    hostapi_filter: str,
    samplerate: int,
) -> tuple[list[DeviceProbe], list[DeviceProbe]]:
    input_probes: list[DeviceProbe] = []
    output_probes: list[DeviceProbe] = []

    for device_id, raw_info in enumerate(devices):
        info = dict(raw_info)
        hostapi_name = _hostapi_name_for_device(info, hostapi_names)

        if not _hostapi_matches_filter(hostapi_name, hostapi_filter):
            continue

        name = str(info["name"])
        tags = _infer_device_tags(name)
        default_sr = float(info["default_samplerate"])

        max_in = int(info["max_input_channels"])
        if max_in > 0:
            open_channels, failures = _probe_device_channels(
                device_id=device_id,
                direction="input",
                samplerate=samplerate,
                dtype="float32",
            )
            input_probes.append(
                DeviceProbe(
                    device_id=device_id,
                    direction="input",
                    name=name,
                    hostapi_name=hostapi_name,
                    default_samplerate=default_sr,
                    max_channels=max_in,
                    open_channels=open_channels,
                    probe_failures=failures,
                    tags=tags,
                )
            )

        max_out = int(info["max_output_channels"])
        if max_out > 0:
            open_channels, failures = _probe_device_channels(
                device_id=device_id,
                direction="output",
                samplerate=samplerate,
                dtype="float32",
            )
            output_probes.append(
                DeviceProbe(
                    device_id=device_id,
                    direction="output",
                    name=name,
                    hostapi_name=hostapi_name,
                    default_samplerate=default_sr,
                    max_channels=max_out,
                    open_channels=open_channels,
                    probe_failures=failures,
                    tags=tags,
                )
            )

    input_probes.sort(key=_probe_sort_key)
    output_probes.sort(key=_probe_sort_key)

    return input_probes, output_probes


def _format_probe_openability(probe: DeviceProbe, samplerate: int) -> str:
    if probe.open_channels is None:
        return f"opens@{samplerate}=no"
    return f"opens@{samplerate}={probe.open_channels}ch"


def _print_probe_section(
    *,
    title: str,
    probes: list[DeviceProbe],
    samplerate: int,
) -> None:
    print(title)

    if not probes:
        print("  (none)")
        return

    for probe in probes:
        max_label = "max_in" if probe.direction == "input" else "max_out"
        print(f"  [{probe.device_id}] {probe.name}")
        print(
            "       "
            f"hostapi={probe.hostapi_name}, "
            f"{max_label}={probe.max_channels}, "
            f"default_sr={_format_samplerate(probe.default_samplerate)}, "
            f"{_format_probe_openability(probe, samplerate)}, "
            f"tags={_format_tags(probe.tags)}"
        )


def _is_likely_physical_mic_input(probe: DeviceProbe) -> bool:
    tags = set(probe.tags)
    return (
        probe.direction == "input"
        and probe.open_channels is not None
        and "mic" in tags
        and "virtual" not in tags
        and "monitor" not in tags
        and "dpdfnet" not in tags
        and "vb-cable-recording" not in tags
        and "vb-cable-playback" not in tags
    )


def _is_likely_virtual_bridge_output(probe: DeviceProbe) -> bool:
    tags = set(probe.tags)
    return (
        probe.direction == "output"
        and probe.open_channels is not None
        and ("vb-cable-playback" in tags or "virtual-sink" in tags or "dpdfnet" in tags)
    )


def _suggest_run_pair(
    input_probes: list[DeviceProbe], output_probes: list[DeviceProbe]
) -> Optional[tuple[DeviceProbe, DeviceProbe]]:
    likely_inputs = [
        probe for probe in input_probes if _is_likely_physical_mic_input(probe)
    ]
    likely_outputs = [
        probe for probe in output_probes if _is_likely_virtual_bridge_output(probe)
    ]

    best_pair: Optional[tuple[DeviceProbe, DeviceProbe]] = None
    best_score: Optional[int] = None

    for input_probe in likely_inputs:
        for output_probe in likely_outputs:
            if input_probe.hostapi_name != output_probe.hostapi_name:
                continue

            score = _score_input_probe(input_probe) + _score_output_probe(output_probe)
            if best_pair is None or best_score is None or score > best_score:
                best_pair = (input_probe, output_probe)
                best_score = score

    if best_pair is not None:
        return best_pair

    viable_inputs = [probe for probe in input_probes if probe.open_channels is not None]
    viable_outputs = [
        probe for probe in output_probes if probe.open_channels is not None
    ]

    if len(viable_inputs) == 1 and len(viable_outputs) == 1:
        only_input = viable_inputs[0]
        only_output = viable_outputs[0]
        if only_input.hostapi_name == only_output.hostapi_name:
            return only_input, only_output

    return None


def _build_run_command_suggestion(
    *,
    input_id: int,
    output_id: int,
    model_name: str,
) -> str:
    parts = [
        "python",
        "mic_bridge.py",
        "run",
        "--input",
        str(input_id),
        "--output",
        str(output_id),
    ]

    if model_name != DEFAULT_MODEL:
        parts.extend(["--model", model_name])

    return " ".join(parts)


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
    sd_mod = _require_sounddevice()

    sample_rate = _resolve_stream_sample_rate(
        args.model, args.samplerate, context="run"
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
            f"{_same_hostapi_guidance()}"
        )

    requested_block_ms = None if args.block_ms is None else float(args.block_ms)
    block_size, effective_block_ms, block_policy = _resolve_effective_block_size(
        sample_rate=sample_rate,
        requested_block_ms=requested_block_ms,
        hostapi_name=input_hostapi,
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
        block_ms=effective_block_ms,
        requested_block_ms=requested_block_ms,
        block_policy=block_policy,
        block_size=block_size,
        input_device_id=int(args.input),
        input_device_name=str(input_info["name"]),
        input_hostapi_name=input_hostapi,
        input_channels=input_channels,
        input_tags=_infer_device_tags(str(input_info["name"])),
        output_device_id=int(args.output),
        output_device_name=str(output_info["name"]),
        output_hostapi_name=output_hostapi,
        output_channels=output_channels,
        output_tags=_infer_device_tags(str(output_info["name"])),
        latency=str(args.latency),
    )


def _print_platform_usage_notes(*, samplerate: int, hostapi_filter: str) -> None:
    system = platform.system()

    if system == "Windows":
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
        print(
            f"  - Probe results above show whether a device can open at {samplerate} Hz."
        )
        print("  - Version 0 intentionally has no separate 'validate' command.")
        print(
            "    Use 'run' and stop it with Ctrl+C as the quick smoke test after choosing IDs."
        )
        print()
        return

    if system == "Linux":
        print("Linux PipeWire usage notes:")
        print("  - This script uses the PortAudio host APIs exposed by sounddevice.")
        print(
            "  - On PipeWire systems, prefer a real 'PipeWire' host API if your PortAudio build has one,"
        )
        print("    otherwise prefer PulseAudio, then JACK, then ALSA.")
        print("  - Keep --input and --output on the same host API.")
        print("  - Recommended virtual mic target command:")
        print(f"      {_linux_virtual_mic_command()}")
        print("  - Why this form:")
        print(
            "      * `media.class=Audio/Source/Virtual` makes many apps expose it more like a mic"
        )
        print(
            "      * `channels=1 channel_map=mono` keeps it explicitly mono and avoids left/right surprises"
        )
        print(
            "      * the bridge still writes to it as an output/playback device, despite the mic-like naming"
        )
        print(
            f"  - Probe results above show whether a device can open at {samplerate} Hz."
        )
        print(
            f"  - In apps, first try choosing '{LINUX_VIRTUAL_MIC_DESCRIPTION}' directly as the microphone."
        )
        print(
            "    If a given app does not expose it directly, try the matching monitor/source instead."
        )
        if hostapi_filter == "jack":
            auto_frames = _recommended_linux_jack_block_frames(samplerate)
            auto_ms = _jack_block_ms_for_frames(auto_frames, samplerate)
            print("  - Your current device view is using JACK.")
            print(
                "  - On Linux/JACK, if you omit --block-ms, 'run' auto-selects an exact JACK-friendly"
            )
            print(
                f"    block size, typically {auto_ms:.3f} ms ({auto_frames} frames) at {samplerate} Hz."
            )
            print(
                "    This avoids a common glitch case where raw 10.0 ms becomes a non-exact frame count."
            )
            print(
                f"  - If you override --block-ms on JACK and hear crackle/stutter, try exact values such as:"
            )
            print(f"      {_format_jack_block_ms_hints(samplerate)} ms")
        print("  - Version 0 intentionally has no separate 'validate' command.")
        print(
            "    Use 'run' and stop it with Ctrl+C as the quick smoke test after choosing IDs."
        )
        print()
        return

    print("Platform usage notes:")
    print(
        "  - This script is primarily intended for Windows 10/11 and Linux systems running PipeWire."
    )
    print(f"  - Probe results above show whether a device can open at {samplerate} Hz.")
    print()
    return


def list_devices_command(args: argparse.Namespace) -> int:
    sd_mod = _require_sounddevice()

    probe_sample_rate = _resolve_stream_sample_rate(
        args.model, args.samplerate, context="device listing"
    )

    if platform.system() not in ("Windows", "Linux"):
        _eprint(
            "[note] This script is primarily intended for Windows 10/11 and Linux PipeWire systems, though device listing may still work elsewhere."
        )

    devices = sd_mod.query_devices()
    hostapi_names = _get_hostapi_names(sd_mod)
    hostapi_filter = _resolve_list_hostapi_filter(args, hostapi_names)

    input_probes, output_probes = _build_device_probes(
        devices=devices,
        hostapi_names=hostapi_names,
        hostapi_filter=hostapi_filter,
        samplerate=probe_sample_rate,
    )

    print()
    print(f"Recommended devices for model {args.model} @ {probe_sample_rate} Hz")
    print(f"Host API filter: {HOSTAPI_FILTER_DISPLAY_NAMES[hostapi_filter]}")
    if hostapi_filter != "all":
        print("Use --all-apis to show every PortAudio device.")
    print()

    _print_probe_section(
        title="Input candidates",
        probes=input_probes,
        samplerate=probe_sample_rate,
    )
    print()
    _print_probe_section(
        title="Output candidates",
        probes=output_probes,
        samplerate=probe_sample_rate,
    )
    print()

    if hostapi_filter != "all" and not input_probes and not output_probes:
        print(
            f"No devices matched the current Host API filter '{hostapi_filter}'. Try --all-apis."
        )
        print()

    suggested_pair = _suggest_run_pair(input_probes, output_probes)
    if suggested_pair is not None:
        input_probe, output_probe = suggested_pair
        print("Likely first run:")
        print(
            "  "
            + _build_run_command_suggestion(
                input_id=input_probe.device_id,
                output_id=output_probe.device_id,
                model_name=args.model,
            )
        )
        if platform.system() == "Linux" and hostapi_filter == "jack":
            auto_frames = _recommended_linux_jack_block_frames(probe_sample_rate)
            auto_ms = _jack_block_ms_for_frames(auto_frames, probe_sample_rate)
            print(
                f"  (On Linux/JACK, omitting --block-ms auto-selects {auto_ms:.3f} ms / {auto_frames} frames.)"
            )
        print()

    _print_platform_usage_notes(
        samplerate=probe_sample_rate,
        hostapi_filter=hostapi_filter,
    )

    return 0


def _print_run_summary(config: RunConfig) -> None:
    system = platform.system()
    input_tags = set(config.input_tags)
    output_tags = set(config.output_tags)
    input_hostapi_key = _hostapi_filter_key_for_name(config.input_hostapi_name)

    print()
    print("Run configuration")
    print("-----------------")
    print(f"Model                : {config.model_name}")
    print(f"Model/native SR      : {config.sample_rate} Hz")

    if config.block_policy == "explicit":
        print(
            "Block size           : "
            f"{config.block_size} samples "
            f"({config.block_ms:.3f} ms effective, "
            f"{config.requested_block_ms:.3f} ms requested)"
        )
    elif config.block_policy == "auto-linux-jack":
        print(
            "Block size           : "
            f"{config.block_size} samples "
            f"({config.block_ms:.3f} ms auto-selected for Linux/JACK)"
        )
    else:
        print(
            "Block size           : "
            f"{config.block_size} samples "
            f"({config.block_ms:.3f} ms default)"
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
            auto_frames = _recommended_linux_jack_block_frames(config.sample_rate)
            auto_ms = _jack_block_ms_for_frames(auto_frames, config.sample_rate)
            if config.block_policy == "auto-linux-jack":
                print(
                    "  - Linux/JACK note: because --block-ms was omitted, the script auto-selected"
                )
                print(
                    f"    {config.block_ms:.3f} ms ({config.block_size} frames), an exact JACK-friendly block size."
                )
            elif not _is_power_of_two(config.block_size):
                print(
                    "  - Linux/JACK note: this block size is not an exact power-of-two frame count."
                )
                print(f"    If you hear stutter/crackle, try --block-ms {auto_ms:.3f}")
                print(
                    f"    or another exact JACK-friendly value such as {_format_jack_block_ms_hints(config.sample_rate)} ms."
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
    sd_mod = _require_sounddevice()
    dpdfnet_mod = _require_dpdfnet()

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
            "Command-line DPDFNet microphone bridge for Windows 10/11 and Linux PipeWire setups.\n"
            "Use 'list-devices' first, then 'run' with numeric device IDs.\n"
            "On Linux, PipeWire is reached through the PortAudio host APIs exposed by sounddevice,\n"
            "usually PipeWire directly if available, otherwise PulseAudio, JACK, or ALSA.\n"
            "For Linux/PipeWire virtual mic targets, the recommended creation command is:\n"
            f"  {_linux_virtual_mic_command()}\n"
            "Version 0 intentionally has no separate 'validate' subcommand,\n"
            "use 'run' as the quick smoke-test path."
        ),
        formatter_class=argparse.RawTextHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python mic_bridge.py list-devices\n"
            "  python mic_bridge.py run --input 18 --output 16\n"
        ),
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser(
        "list-devices",
        help=(
            "List recommended audio devices and probe whether they open at the selected sample rate."
        ),
        formatter_class=argparse.RawTextHelpFormatter,
        description=(
            "List audio devices in a platform-friendly way.\n"
            "On Windows, the default view is filtered to WASAPI because that is the\n"
            "recommended starting point for this bridge.\n"
            "On Linux, the default view prefers a PipeWire host API if your PortAudio\n"
            "build exposes one, otherwise PulseAudio, then JACK, then ALSA.\n"
            "Use --all-apis to show every PortAudio host API instead."
        ),
    )
    list_parser.add_argument(
        "--model",
        type=str,
        choices=sorted(MODEL_SAMPLE_RATES),
        default=DEFAULT_MODEL,
        help=f"DPDFNet model whose native sample rate will be probed. Default: {DEFAULT_MODEL}",
    )
    list_parser.add_argument(
        "--samplerate",
        type=int,
        default=None,
        help=(
            "Probe sample rate in Hz. In Version 0 this must match the model's native "
            "sample rate exactly. If omitted, the model's native rate is used."
        ),
    )
    list_parser.add_argument(
        "--hostapi",
        type=str,
        choices=LIST_DEVICE_HOSTAPI_CHOICES,
        default=None,
        help=(
            "Host API filter for listing.\n"
            "Windows default: WASAPI.\n"
            "Linux default: PipeWire if available, otherwise PulseAudio, JACK, then ALSA.\n"
            "Use --hostapi all or --all-apis to show every PortAudio host API."
        ),
    )
    list_parser.add_argument(
        "--all-apis",
        action="store_true",
        help="Show every PortAudio host API. Equivalent to --hostapi all.",
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
            "Windows with VB-CABLE: usually the playback endpoint named 'CABLE Input'.\n"
            "Linux/PipeWire: usually the virtual mic target created with:\n"
            f"  {_linux_virtual_mic_command()}\n"
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
        default=None,
        help=(
            "Callback block size in milliseconds.\n"
            "If omitted, the default is backend-aware:\n"
            "  - Linux/JACK: auto-select an exact JACK-friendly block size,\n"
            "    typically 21.333 ms at 48 kHz\n"
            "  - other backends: 10.0 ms\n"
            "If you override this on Linux/JACK and hear crackle/stutter,\n"
            "prefer exact values such as 10.667 or 21.333 ms at 48 kHz."
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
