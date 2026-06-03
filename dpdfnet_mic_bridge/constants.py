"""Shared constants."""

from __future__ import annotations

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

LINUX_REUSABLE_VIRTUAL_MIC_SINK_NAME = "dpdfnet_mic"
LINUX_REUSABLE_VIRTUAL_MIC_DESCRIPTION = "DPDFNet_Mic"

# On Linux/JACK, Python + PortAudio tends to behave more reliably when given a
# somewhat roomier exact block size instead of raw 10.0 ms.
LINUX_JACK_AUTO_MIN_BLOCK_MS = 20.0
LINUX_JACK_AUTO_MIN_BLOCK_FRAMES = 512

# Display-name order intentionally defines CLI presentation order.
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

LIST_DEVICE_HOSTAPI_CHOICES: tuple[str, ...] = tuple(HOSTAPI_FILTER_DISPLAY_NAMES)

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

# These scores are applied after a host API name has been normalized to the
# canonical filter key above. Keeping them keyed by filter name avoids having
# separate scoring logic that can drift from filtering logic.
HOSTAPI_PREFERENCE_SCORES: dict[str, int] = {
    "pipewire": 110,
    "wasapi": 100,
    "pulse": 95,
    "jack": 80,
    "wdm-ks": 80,
    "alsa": 60,
    "directsound": 40,
    "mme": 20,
}

CALLBACK_STATUS_FLAG_NAMES: tuple[str, ...] = (
    "input_underflow",
    "input_overflow",
    "output_underflow",
    "output_overflow",
    "priming_output",
)
