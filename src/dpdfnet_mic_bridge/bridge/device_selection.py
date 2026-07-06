"""Run-specific input/output resolution and handoff errors."""

from __future__ import annotations

import platform
from collections.abc import Callable

from ..audio.hostapi import hostapi_filter_key_for_name
from ..audio.inventory import (device_info_from_inventory,
                               hostapi_name_for_device, query_audio_inventory)
from ..audio.probing import build_device_probes
from ..audio.selection import (choose_best_auto_input, choose_best_auto_output,
                               is_likely_virtual_bridge_output,
                               is_viable_auto_input_candidate,
                               suggest_run_pair)
from ..audio.types import AudioInventory, DeviceProbe, ProbeSnapshot


def scan_device_probes(
    sample_rate: int,
    hostapi_filter: str,
) -> ProbeSnapshot:
    inventory = query_audio_inventory()
    input_probes, output_probes = build_device_probes(
        inventory=inventory,
        hostapi_filter=hostapi_filter,
        samplerate=sample_rate,
    )
    return ProbeSnapshot(
        inventory=inventory,
        input_probes=input_probes,
        output_probes=output_probes,
    )


def list_devices_handoff(next_step: str) -> str:
    return f"Run `dpdfnet-mic-bridge devices`, then {next_step}"


def get_current_run_device_info(
    *,
    inventory: AudioInventory,
    device_id: int,
    role: str,
) -> dict:
    """Resolve a device ID against one already-queried PortAudio inventory."""
    try:
        return device_info_from_inventory(inventory, device_id)
    except ValueError as exc:
        lines = [
            f"{role.title()} device ID {device_id} is not available.",
            list_devices_handoff(f"retry with a fresh --{role} ID."),
        ]
        raise ValueError("\n".join(lines)) from exc


def _hostapis_for(
    probes: list[DeviceProbe],
    predicate: Callable[[DeviceProbe], bool],
) -> list[str]:
    return list(
        dict.fromkeys(probe.hostapi_name for probe in probes if predicate(probe))
    )


def _candidate_input_hostapis(input_probes: list[DeviceProbe]) -> list[str]:
    return _hostapis_for(input_probes, is_viable_auto_input_candidate)


def _bridge_output_hostapis(output_probes: list[DeviceProbe]) -> list[str]:
    return _hostapis_for(output_probes, is_likely_virtual_bridge_output)


def _build_no_auto_input_handoff(
    *,
    sample_rate: int,
    output_hostapi_name: str | None,
    input_probes: list[DeviceProbe],
) -> str:
    visible_input_hostapis = _candidate_input_hostapis(input_probes)

    if output_hostapi_name is not None and visible_input_hostapis:
        return (
            "No usable microphone input on the output's host API.\n"
            f"Output host API: {output_hostapi_name}\n"
            f"Inputs seen on: {', '.join(visible_input_hostapis)}\n"
            + list_devices_handoff("pin --input and --output.")
        )

    return (
        f"No physical-looking microphone input opened at {sample_rate} Hz. Auto mode only picks devices that look like a mic, headset, Bluetooth mic, or audio interface.\n"
        + list_devices_handoff("pin --input and --output.")
    )


def _build_no_auto_output_handoff(
    *,
    input_hostapi_name: str | None,
    output_probes: list[DeviceProbe],
) -> str:
    visible_bridge_hostapis = _bridge_output_hostapis(output_probes)

    if input_hostapi_name is not None and visible_bridge_hostapis:
        return (
            "No bridge output on the input's host API.\n"
            f"Input host API: {input_hostapi_name}\n"
            f"Bridge outputs seen on: {', '.join(visible_bridge_hostapis)}\n"
            + list_devices_handoff("pin matching IDs.")
        )

    system = platform.system()

    if system == "Windows":
        return (
            "No bridge output found. Auto mode only picks VB-CABLE-style playback endpoints, not speakers/headphones.\n"
            + list_devices_handoff("pin --output, or pin both IDs.")
        )

    if system == "Linux":
        return (
            "No bridge output found. Auto mode only picks virtual mic targets, not speakers/headphones or monitor/source endpoints.\n"
            + list_devices_handoff("pin --output, or pin both IDs.")
        )

    return "No bridge output found.\n" + list_devices_handoff(
        "pin --output, or pin both IDs."
    )


def _build_no_auto_pair_handoff(
    *,
    sample_rate: int,
    input_probes: list[DeviceProbe],
    output_probes: list[DeviceProbe],
) -> str:
    visible_input_hostapis = _candidate_input_hostapis(input_probes)
    visible_bridge_hostapis = _bridge_output_hostapis(output_probes)

    if not visible_input_hostapis:
        return _build_no_auto_input_handoff(
            sample_rate=sample_rate,
            output_hostapi_name=None,
            input_probes=input_probes,
        )

    if not visible_bridge_hostapis:
        return _build_no_auto_output_handoff(
            input_hostapi_name=None,
            output_probes=output_probes,
        )

    return (
        "No microphone and bridge output matched on the same host API.\n"
        f"Inputs: {', '.join(visible_input_hostapis)}\n"
        f"Bridge outputs: {', '.join(visible_bridge_hostapis)}\n"
        + list_devices_handoff("pin matching IDs.")
    )


def _snapshot_for_selection(
    *,
    sample_rate: int,
    requested_input_id: int | None,
    requested_output_id: int | None,
) -> ProbeSnapshot:
    hostapi_filter = "all"

    if (requested_input_id is None) != (requested_output_id is None):
        inventory = query_audio_inventory()
        role = "input" if requested_input_id is not None else "output"
        device_id = (
            requested_input_id
            if requested_input_id is not None
            else requested_output_id
        )
        assert device_id is not None

        pinned_info = get_current_run_device_info(
            inventory=inventory,
            device_id=device_id,
            role=role,
        )
        hostapi_key = hostapi_filter_key_for_name(
            hostapi_name_for_device(pinned_info, inventory.hostapi_names)
        )
        if hostapi_key is not None:
            hostapi_filter = hostapi_key

    return scan_device_probes(sample_rate, hostapi_filter=hostapi_filter)


def resolve_run_device_selection(
    *,
    sample_rate: int,
    requested_input_id: int | None,
    requested_output_id: int | None,
) -> tuple[int, int, bool, bool, AudioInventory]:
    """Return input id, output id, input-auto flag, output-auto flag, inventory."""
    if requested_input_id is not None and requested_output_id is not None:
        inventory = query_audio_inventory()
        get_current_run_device_info(
            inventory=inventory,
            device_id=requested_input_id,
            role="input",
        )
        get_current_run_device_info(
            inventory=inventory,
            device_id=requested_output_id,
            role="output",
        )
        return requested_input_id, requested_output_id, False, False, inventory

    snapshot = _snapshot_for_selection(
        sample_rate=sample_rate,
        requested_input_id=requested_input_id,
        requested_output_id=requested_output_id,
    )

    if requested_input_id is not None and requested_output_id is None:
        input_info = get_current_run_device_info(
            inventory=snapshot.inventory,
            device_id=requested_input_id,
            role="input",
        )
        input_hostapi = hostapi_name_for_device(
            input_info,
            snapshot.inventory.hostapi_names,
        )

        output_probe = choose_best_auto_output(
            snapshot.output_probes,
            hostapi_name=input_hostapi,
        )
        if output_probe is not None:
            return (
                requested_input_id,
                output_probe.device_id,
                False,
                True,
                snapshot.inventory,
            )

        raise ValueError(
            _build_no_auto_output_handoff(
                input_hostapi_name=input_hostapi,
                output_probes=snapshot.output_probes,
            )
        )

    if requested_input_id is None and requested_output_id is not None:
        output_info = get_current_run_device_info(
            inventory=snapshot.inventory,
            device_id=requested_output_id,
            role="output",
        )
        output_hostapi = hostapi_name_for_device(
            output_info,
            snapshot.inventory.hostapi_names,
        )

        input_probe = choose_best_auto_input(
            snapshot.input_probes,
            hostapi_name=output_hostapi,
        )
        if input_probe is not None:
            return (
                input_probe.device_id,
                requested_output_id,
                True,
                False,
                snapshot.inventory,
            )

        raise ValueError(
            _build_no_auto_input_handoff(
                sample_rate=sample_rate,
                output_hostapi_name=output_hostapi,
                input_probes=snapshot.input_probes,
            )
        )

    suggested_pair = suggest_run_pair(snapshot.input_probes, snapshot.output_probes)
    if suggested_pair is not None:
        input_probe, output_probe = suggested_pair
        return (
            input_probe.device_id,
            output_probe.device_id,
            True,
            True,
            snapshot.inventory,
        )

    raise ValueError(
        _build_no_auto_pair_handoff(
            sample_rate=sample_rate,
            input_probes=snapshot.input_probes,
            output_probes=snapshot.output_probes,
        )
    )
