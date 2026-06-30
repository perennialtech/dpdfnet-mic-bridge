"""PortAudio host-API normalization, filtering, and preference policy."""

from __future__ import annotations

import platform

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

_DEFAULT_LIST_HOSTAPI_ORDER_BY_SYSTEM: dict[str, tuple[str, ...]] = {
    "Windows": ("wasapi",),
    "Linux": ("pipewire", "pulse", "jack", "alsa"),
}


def normalize_hostapi_name(name: str) -> str:
    return " ".join(name.casefold().replace("_", " ").replace("-", " ").split())


_NORMALIZED_HOSTAPI_ALIASES: dict[str, tuple[str, ...]] = {
    filter_name: tuple(normalize_hostapi_name(alias) for alias in aliases)
    for filter_name, aliases in HOSTAPI_FILTER_ALIASES.items()
}


def hostapi_filter_key_for_name(hostapi_name: str) -> str | None:
    normalized = normalize_hostapi_name(hostapi_name)
    for filter_name, aliases in _NORMALIZED_HOSTAPI_ALIASES.items():
        if any(normalized == alias or alias in normalized for alias in aliases):
            return filter_name
    return None


def available_known_hostapi_filters(hostapi_names: list[str]) -> set[str]:
    return {
        key
        for hostapi_name in hostapi_names
        if (key := hostapi_filter_key_for_name(hostapi_name)) is not None
    }


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

    for candidate in _DEFAULT_LIST_HOSTAPI_ORDER_BY_SYSTEM.get(current_system, ()):
        if candidate in available:
            return candidate

    return "all"


def hostapi_preference_score(hostapi_name: str) -> int:
    key = hostapi_filter_key_for_name(hostapi_name)
    if key is None:
        return 0
    return HOSTAPI_PREFERENCE_SCORES.get(key, 0)


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
