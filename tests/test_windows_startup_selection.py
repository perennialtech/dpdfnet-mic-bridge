import pytest

from dpdfnet_mic_bridge.audio.types import AudioInventory
from dpdfnet_mic_bridge.bridge import device_selection


def _inventory():
    return AudioInventory(
        hostapi_names=["Windows WASAPI", "Windows MME"],
        devices=[
            {
                "id": 1,
                "name": "Microphone (USB)",
                "hostapi": 0,
                "max_output_channels": 0,
            },
            {
                "id": 2,
                "name": "Unrelated Headset",
                "hostapi": 0,
                "max_output_channels": 2,
            },
            {
                "id": 3,
                "name": "CABLE Input (VB-Audio)",
                "hostapi": 0,
                "max_output_channels": 2,
            },
            {
                "id": 4,
                "name": "CABLE Input (MME)",
                "hostapi": 1,
                "max_output_channels": 2,
            },
        ],
    )


def test_windows_desktop_route_uses_cable_without_probing_unrelated_devices(
    monkeypatch,
):
    monkeypatch.setattr(device_selection.platform, "system", lambda: "Windows")
    monkeypatch.setattr(device_selection, "query_audio_inventory", _inventory)
    monkeypatch.setattr(
        device_selection,
        "scan_device_probes",
        lambda *args, **kwargs: pytest.fail("Unrelated audio devices were probed"),
    )
    input_id, output_id, auto_input, auto_output, _ = (
        device_selection.resolve_run_device_selection(
            sample_rate=48000, requested_input_id=1, requested_output_id=None
        )
    )
    assert (input_id, output_id, auto_input, auto_output) == (1, 3, False, True)


def test_windows_desktop_route_reports_missing_wasapi_cable(monkeypatch):
    monkeypatch.setattr(device_selection.platform, "system", lambda: "Windows")
    existing = _inventory()
    inventory = AudioInventory(
        devices=[item for item in existing.devices if item["id"] != 3],
        hostapi_names=existing.hostapi_names,
    )
    monkeypatch.setattr(device_selection, "query_audio_inventory", lambda: inventory)
    with pytest.raises(ValueError, match="No VB-CABLE render endpoint"):
        device_selection.resolve_run_device_selection(
            sample_rate=48000, requested_input_id=1, requested_output_id=None
        )
