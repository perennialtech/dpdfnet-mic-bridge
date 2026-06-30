"""Device scoring, auto-selection, and pair suggestion."""

from __future__ import annotations

from .hostapi import hostapi_preference_score
from .tags import has_bridge_output_tags
from .types import DeviceProbe


def _score_input_probe(probe: DeviceProbe) -> int:
    if probe.direction != "input":
        raise ValueError("Input scoring called on non-input probe.")

    if probe.open_channels is None:
        return -10_000

    tags = set(probe.tags)
    score = hostapi_preference_score(probe.hostapi_name)

    if "mic" in tags:
        score += 40
    if "audio-interface" in tags:
        score += 20
    if "dummy" in tags:
        score -= 200
    if "hdmi" in tags:
        score -= 90
    if "headphones" in tags or "speakers" in tags or "bluetooth" in tags:
        score -= 25
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
    if "dummy" in tags:
        score -= 120
    if "headphones" in tags or "speakers" in tags:
        score -= 10
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


def device_probe_sort_key(probe: DeviceProbe) -> tuple[bool, int, int]:
    if probe.direction == "input":
        score = _score_input_probe(probe)
    else:
        score = _score_output_probe(probe)

    return (probe.open_channels is None, -score, probe.device_id)


def is_viable_auto_input_candidate(probe: DeviceProbe) -> bool:
    """
    Inputs for auto mode are intentionally broad enough to catch many physical
    interfaces whose names do not literally contain "microphone", while still
    excluding obvious loopbacks, monitors, and bridge endpoints.
    """
    tags = set(probe.tags)
    physical_input_tags = {
        "mic",
        "audio-interface",
        "bluetooth",
        "headphones",
    }

    if tags.isdisjoint(physical_input_tags):
        return False

    return (
        probe.direction == "input"
        and probe.open_channels is not None
        and "virtual" not in tags
        and "monitor" not in tags
        and "dummy" not in tags
        and "hdmi" not in tags
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
