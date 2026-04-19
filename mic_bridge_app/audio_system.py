"""
Platform-audio policy and device probing helpers.

This module owns the rules that depend on PortAudio host APIs, sounddevice
device metadata, and backend-specific stream behavior, especially Windows
WASAPI preferences, Linux/PipeWire/JACK handling, and the candidate policy used
by auto-selection.

Keep user-facing workflow text in `listing.py` and `runtime.py`. Keep parser
definition in `cli.py`. This file should focus on reusable audio-system logic.

Keep bridge-output identification centralized here so auto-selection, pinned
pair suggestions, and runtime warnings stay aligned.

Refactor guardrail:
Prefer a few cohesive helpers over many one-line wrappers. This module is meant
to centralize policy, not to fragment straightforward code into helper-sprawl.
"""

from __future__ import annotations

import math
import platform
from typing import Iterable

from .constants import (
    DEFAULT_BLOCK_MS,
    DEFAULT_MODEL,
    HOSTAPI_FILTER_ALIASES,
    HOSTAPI_PREFERENCE_SCORES,
    JACK_BLOCK_HINT_FRAMES,
    LINUX_JACK_AUTO_MIN_BLOCK_FRAMES,
    LINUX_JACK_AUTO_MIN_BLOCK_MS,
    LINUX_VIRTUAL_MIC_DESCRIPTION,
    LINUX_VIRTUAL_MIC_SINK_NAME,
)
from .dependencies import require_sounddevice
from .domain import DeviceProbe


def format_samplerate(value: float) -> str:
    rounded = int(round(value))
    if abs(value - rounded) < 1e-6:
        return str(rounded)
    return f"{value:.1f}"


def format_tags(tags: tuple[str, ...]) -> str:
    return ", ".join(tags) if tags else "-"


def normalize_hostapi_name(name: str) -> str:
    return " ".join(name.casefold().replace("_", " ").replace("-", " ").split())


_NORMALIZED_HOSTAPI_ALIASES: dict[str, tuple[str, ...]] = {
    filter_name: tuple(normalize_hostapi_name(alias) for alias in aliases)
    for filter_name, aliases in HOSTAPI_FILTER_ALIASES.items()
}


def get_hostapi_names(sd_mod) -> list[str]:
    hostapis = sd_mod.query_hostapis()
    return [str(item["name"]) for item in hostapis]


def query_audio_inventory():
    """
    Query devices and host APIs through sounddevice/PortAudio and turn
    enumeration failures into short user-facing handoffs.
    """
    sd_mod = require_sounddevice()

    try:
        devices = sd_mod.query_devices()
        hostapi_names = get_hostapi_names(sd_mod)
    except Exception as exc:
        detail_text = str(exc).strip()
        detail = detail_text.splitlines()[0] if detail_text else type(exc).__name__
        system = platform.system()

        if system == "Windows":
            guidance = (
                "Windows did not provide a usable PortAudio device list. "
                "Make sure your audio devices appear in Windows sound settings, "
                "and if you plan to bridge into VB-CABLE, install it beforehand outside this script."
            )
        elif system == "Linux":
            guidance = (
                "PortAudio could not enumerate Linux audio devices. Make sure your audio session is running and that your PortAudio build can see the expected PipeWire, PulseAudio, JACK, or ALSA backend."
            )
        else:
            guidance = "PortAudio could not enumerate audio devices on this system."

        raise RuntimeError(f"Could not query audio devices.\n{guidance}\nDetails: {detail}") from exc

    return devices, hostapi_names


def hostapi_filter_key_for_name(hostapi_name: str) -> str | None:
    normalized = normalize_hostapi_name(hostapi_name)
    for filter_name, aliases in _NORMALIZED_HOSTAPI_ALIASES.items():
        if any(normalized == alias or alias in normalized for alias in aliases):
            return filter_name
    return None


def available_known_hostapi_filters(hostapi_names: list[str]) -> set[str]:
    available: set[str] = set()
    for hostapi_name in hostapi_names:
        key = hostapi_filter_key_for_name(hostapi_name)
        if key is not None:
            available.add(key)
    return available


def get_device_info(device_id: int) -> dict:
    sd_mod = require_sounddevice()
    try:
        info = sd_mod.query_devices(device_id)
    except Exception as exc:
        raise ValueError(f"Invalid device ID {device_id}: {exc}") from exc
    return dict(info)


def hostapi_name_for_device(info: dict, hostapi_names: list[str]) -> str:
    index = int(info["hostapi"])
    if 0 <= index < len(hostapi_names):
        return hostapi_names[index]
    return f"<unknown:{index}>"


def hostapi_matches_filter(hostapi_name: str, filter_name: str) -> bool:
    if filter_name == "all":
        return True
    return hostapi_filter_key_for_name(hostapi_name) == filter_name


def resolve_list_hostapi_filter(
    requested_hostapi: str | None,
    all_apis: bool,
    hostapi_names: list[str],
    *,
    system: str | None = None,
) -> str:
    if all_apis:
        if requested_hostapi not in (None, "all"):
            raise ValueError("Use either --all-apis or --hostapi, not both.")
        return "all"

    if requested_hostapi is not None:
        return str(requested_hostapi)

    current_system = platform.system() if system is None else system
    available = available_known_hostapi_filters(hostapi_names)

    if current_system == "Windows":
        return "wasapi" if "wasapi" in available else "all"

    if current_system == "Linux":
        for candidate in ("pipewire", "pulse", "jack", "alsa"):
            if candidate in available:
                return candidate
        return "all"

    return "all"


def hostapi_preference_score(hostapi_name: str) -> int:
    key = hostapi_filter_key_for_name(hostapi_name)
    if key is None:
        return 0
    return HOSTAPI_PREFERENCE_SCORES.get(key, 0)


def infer_device_tags(name: str) -> tuple[str, ...]:
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

    deduped: list[str] = []
    seen: set[str] = set()
    for tag in tags:
        if tag not in seen:
            seen.add(tag)
            deduped.append(tag)

    return tuple(deduped)


def has_bridge_output_tags(tags: tuple[str, ...]) -> bool:
    """
    Return whether name-derived tags positively identify a bridge-style output.

    Keep the positive-tag rule centralized here so runtime warnings do not drift
    away from auto-selection policy.
    """
    tag_set = set(tags)
    return (
        "vb-cable-playback" in tag_set
        or "virtual-sink" in tag_set
        or "dpdfnet" in tag_set
    )


def iter_input_channel_candidates(max_channels: int) -> Iterable[int]:
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


def iter_output_channel_candidates(max_channels: int) -> Iterable[int]:
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


def probe_device_channels(
    *,
    device_id: int,
    direction: str,
    samplerate: int,
    dtype: str = "float32",
    device_info: dict | None = None,
) -> tuple[int | None, tuple[str, ...]]:
    sd_mod = require_sounddevice()
    info = dict(device_info) if device_info is not None else get_device_info(device_id)

    if direction == "input":
        max_channels = int(info["max_input_channels"])
        check = sd_mod.check_input_settings
        channel_candidates = iter_input_channel_candidates(max_channels)
    elif direction == "output":
        max_channels = int(info["max_output_channels"])
        check = sd_mod.check_output_settings
        channel_candidates = iter_output_channel_candidates(max_channels)
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


def negotiate_stream_channels(
    *,
    device_id: int,
    direction: str,
    samplerate: int,
    dtype: str = "float32",
) -> int:
    """
    Pick a channel count that the selected device can open.

    For input:
    - prefer mono, then stereo, then native max
    - any opened input will later be downmixed to mono before enhancement

    For output:
    - prefer stereo, then mono, then native max
    - enhanced mono will later be duplicated across every opened output channel

    The input/output preference split is deliberate. Mono is ideal for capture,
    but mono-open on some output devices can produce left-only audio.
    """
    channels, failures = probe_device_channels(
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


def same_hostapi_guidance() -> str:
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


def linux_virtual_mic_command() -> str:
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


def next_power_of_two(value: int) -> int:
    if value <= 1:
        return 1
    return 1 << (value - 1).bit_length()


def is_power_of_two(value: int) -> bool:
    return value > 0 and (value & (value - 1)) == 0


def jack_block_ms_for_frames(frames: int, sample_rate: int) -> float:
    return (float(frames) * 1000.0) / float(sample_rate)


def format_jack_block_ms_hints(sample_rate: int) -> str:
    return ", ".join(
        f"{jack_block_ms_for_frames(frames, sample_rate):.3f}"
        for frames in JACK_BLOCK_HINT_FRAMES
    )


def recommended_linux_jack_block_frames(sample_rate: int) -> int:
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
    return next_power_of_two(target_frames)


def resolve_effective_block_size(
    *,
    sample_rate: int,
    requested_block_ms: float | None,
    hostapi_name: str,
    system: str | None = None,
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
    current_system = platform.system() if system is None else system

    if requested_block_ms is not None:
        if requested_block_ms <= 0:
            raise ValueError("--block-ms must be > 0.")
        block_size = int(round(sample_rate * (requested_block_ms / 1000.0)))
        if block_size <= 0:
            raise ValueError(
                f"--block-ms={requested_block_ms} produced invalid block_size={block_size}."
            )
        effective_ms = jack_block_ms_for_frames(block_size, sample_rate)
        return block_size, effective_ms, "explicit"

    hostapi_key = hostapi_filter_key_for_name(hostapi_name)
    if current_system == "Linux" and hostapi_key == "jack":
        block_size = recommended_linux_jack_block_frames(sample_rate)
        effective_ms = jack_block_ms_for_frames(block_size, sample_rate)
        return block_size, effective_ms, "auto-linux-jack"

    block_size = int(round(sample_rate * (DEFAULT_BLOCK_MS / 1000.0)))
    effective_ms = jack_block_ms_for_frames(block_size, sample_rate)
    return block_size, effective_ms, "default-10ms"


def _score_input_probe(probe: DeviceProbe) -> int:
    if probe.direction != "input":
        raise ValueError("Input scoring called on non-input probe.")

    if probe.open_channels is None:
        return -10_000

    tags = set(probe.tags)
    score = hostapi_preference_score(probe.hostapi_name)

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
    score = hostapi_preference_score(probe.hostapi_name)

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


def build_device_probes(
    *,
    devices,
    hostapi_names: list[str],
    hostapi_filter: str,
    samplerate: int,
) -> tuple[list[DeviceProbe], list[DeviceProbe]]:
    input_probes: list[DeviceProbe] = []
    output_probes: list[DeviceProbe] = []

    direction_specs = (
        ("input", "max_input_channels", input_probes),
        ("output", "max_output_channels", output_probes),
    )

    for device_id, raw_info in enumerate(devices):
        info = dict(raw_info)
        hostapi_name = hostapi_name_for_device(info, hostapi_names)

        if not hostapi_matches_filter(hostapi_name, hostapi_filter):
            continue

        name = str(info["name"])
        tags = infer_device_tags(name)
        default_sr = float(info["default_samplerate"])

        for direction, max_channel_key, bucket in direction_specs:
            max_channels = int(info[max_channel_key])
            if max_channels < 1:
                continue

            open_channels, failures = probe_device_channels(
                device_id=device_id,
                direction=direction,
                samplerate=samplerate,
                dtype="float32",
                device_info=info,
            )
            bucket.append(
                DeviceProbe(
                    device_id=device_id,
                    direction=direction,
                    name=name,
                    hostapi_name=hostapi_name,
                    default_samplerate=default_sr,
                    max_channels=max_channels,
                    open_channels=open_channels,
                    probe_failures=failures,
                    tags=tags,
                )
            )

    input_probes.sort(key=_probe_sort_key)
    output_probes.sort(key=_probe_sort_key)

    return input_probes, output_probes


def format_probe_openability(probe: DeviceProbe, samplerate: int) -> str:
    if probe.open_channels is None:
        return f"opens@{samplerate}=no"
    return f"opens@{samplerate}={probe.open_channels}ch"


def is_viable_auto_input_candidate(probe: DeviceProbe) -> bool:
    """
    Inputs for auto mode are intentionally broad enough to catch many physical
    interfaces whose names do not literally contain "microphone", while still
    excluding obvious loopbacks, monitors, and bridge endpoints.
    """
    tags = set(probe.tags)
    return (
        probe.direction == "input"
        and probe.open_channels is not None
        and "virtual" not in tags
        and "monitor" not in tags
        and "dpdfnet" not in tags
        and "vb-cable-recording" not in tags
        and "vb-cable-playback" not in tags
    )


def choose_best_auto_input(
    input_probes: list[DeviceProbe],
    *,
    hostapi_name: str | None = None,
) -> DeviceProbe | None:
    for probe in input_probes:
        if hostapi_name is not None and probe.hostapi_name != hostapi_name:
            continue
        if is_viable_auto_input_candidate(probe):
            return probe
    return None


def is_likely_virtual_bridge_output(probe: DeviceProbe) -> bool:
    """
    Outputs for auto mode must be playback-side bridge endpoints.

    Positive identification is tag-based and intentionally narrow. Outputs that
    look like monitors or recording-side endpoints are excluded explicitly even
    if their names also contain other virtual-audio markers.
    """
    tags = set(probe.tags)
    return (
        probe.direction == "output"
        and probe.open_channels is not None
        and has_bridge_output_tags(probe.tags)
        and "monitor" not in tags
        and "vb-cable-recording" not in tags
    )


def choose_best_auto_output(
    output_probes: list[DeviceProbe],
    *,
    hostapi_name: str | None = None,
) -> DeviceProbe | None:
    for probe in output_probes:
        if hostapi_name is not None and probe.hostapi_name != hostapi_name:
            continue
        if is_likely_virtual_bridge_output(probe):
            return probe
    return None


def suggest_run_pair(
    input_probes: list[DeviceProbe],
    output_probes: list[DeviceProbe],
) -> tuple[DeviceProbe, DeviceProbe] | None:
    """
    Suggest the pair that auto mode would be happiest to use.

    Inputs are allowed to be broad physical-looking capture devices, but
    outputs remain deliberately strict. We only suggest bridge-style outputs
    here, never speakers/headphones, because that is the same safety boundary
    used by auto mode itself.
    """
    likely_inputs = [
        probe for probe in input_probes if is_viable_auto_input_candidate(probe)
    ]
    likely_outputs = [
        probe for probe in output_probes if is_likely_virtual_bridge_output(probe)
    ]

    best_pair: tuple[DeviceProbe, DeviceProbe] | None = None
    best_score: int | None = None

    for input_probe in likely_inputs:
        for output_probe in likely_outputs:
            if input_probe.hostapi_name != output_probe.hostapi_name:
                continue

            score = _score_input_probe(input_probe) + _score_output_probe(output_probe)
            if best_pair is None or best_score is None or score > best_score:
                best_pair = (input_probe, output_probe)
                best_score = score

    return best_pair


def build_run_command_suggestion(
    *,
    input_id: int,
    output_id: int,
    model_name: str,
) -> str:
    parts = ["python", "mic_bridge.py"]

    if model_name != DEFAULT_MODEL:
        parts.extend(["--model", model_name])

    parts.extend(
        [
            "--input",
            str(input_id),
            "--output",
            str(output_id),
        ]
    )

    return " ".join(parts)
