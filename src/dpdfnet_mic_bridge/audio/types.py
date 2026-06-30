"""Audio-device data structures."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AudioInventory:
    """Snapshot of the current PortAudio device view."""

    devices: list[dict]
    hostapi_names: list[str]


@dataclass(frozen=True)
class DeviceProbe:
    device_id: int
    direction: str
    name: str
    hostapi_name: str
    default_samplerate: float
    max_channels: int
    open_channels: int | None
    probe_failures: tuple[str, ...]
    tags: tuple[str, ...]


@dataclass(frozen=True)
class ProbeSnapshot:
    """One probing pass over the current audio inventory."""

    inventory: AudioInventory
    input_probes: list[DeviceProbe]
    output_probes: list[DeviceProbe]
