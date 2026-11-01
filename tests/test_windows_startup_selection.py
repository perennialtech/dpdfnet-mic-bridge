from types import SimpleNamespace

import pytest

from dpdfnet_mic_bridge.audio.types import AudioInventory
from dpdfnet_mic_bridge.bridge import config, device_selection


def _inventory():
    return AudioInventory(
        hostapi_names=["Windows WASAPI", "Windows MME"],
        devices=[
            {
                "id": 1,
                "name": "Microphone (USB)",
                "hostapi": 0,
                "max_input_channels": 1,
                "max_output_channels": 0,
            },
            {
                "id": 2,
                "name": "Unrelated Headset",
                "hostapi": 0,
                "max_input_channels": 0,
                "max_output_channels": 2,
            },
            {
                "id": 3,
                "name": "CABLE Input (VB-Audio A)",
                "hostapi": 0,
                "max_input_channels": 0,
                "max_output_channels": 2,
            },
            {
                "id": 4,
                "name": "CABLE Input (MME)",
                "hostapi": 1,
                "max_input_channels": 0,
                "max_output_channels": 2,
            },
            {
                "id": 5,
                "name": "CABLE Input (VB-Audio B)",
                "hostapi": 0,
                "max_input_channels": 0,
                "max_output_channels": 2,
            },
            {
                "id": 6,
                "name": "CABLE Output (VB-Audio)",
                "hostapi": 0,
                "max_input_channels": 2,
                "max_output_channels": 0,
            },
        ],
    )


def _setup(monkeypatch, inventory):
    monkeypatch.setattr(config.platform, "system", lambda: "Windows")
    monkeypatch.setattr(config, "query_audio_inventory", lambda: inventory)
    monkeypatch.setattr(device_selection, "query_audio_inventory", lambda: inventory)
    monkeypatch.setattr(
        device_selection,
        "scan_device_probes",
        lambda *args, **kwargs: pytest.fail("Unrelated audio devices were probed"),
    )
    monkeypatch.setattr(
        config,
        "iter_auto_block_sizes",
        lambda **kwargs: [(480, 10.0, "automatic")],
    )


def _args(output=None):
    return SimpleNamespace(
        model="dpdfnet8_48khz_hr", input=1, output=output, latency="high"
    )


def test_windows_desktop_tries_only_matching_cables_until_one_opens(monkeypatch):
    _setup(monkeypatch, _inventory())
    attempted = []

    def negotiate(**kwargs):
        attempted.append(kwargs["output_device_id"])
        if kwargs["output_device_id"] == 3:
            raise RuntimeError("Unavailable output")
        return (1, 2)

    monkeypatch.setattr(config, "negotiate_full_duplex_channels", negotiate)
    route = config.build_run_config(_args(), app_microphone_name=None)
    assert attempted == [3, 5]
    assert route.input_device_id == 1
    assert route.output_device_id == 5
    assert route.output_was_auto_selected


def test_windows_desktop_reports_missing_wasapi_cable(monkeypatch):
    existing = _inventory()
    inventory = AudioInventory(
        devices=[item for item in existing.devices if item["id"] not in (3, 5)],
        hostapi_names=existing.hostapi_names,
    )
    _setup(monkeypatch, inventory)
    with pytest.raises(ValueError, match="No VB-CABLE render endpoint"):
        config.build_run_config(_args(), app_microphone_name=None)


def test_windows_explicit_output_is_not_reselected(monkeypatch):
    _setup(monkeypatch, _inventory())
    attempts = []

    def negotiate(**kwargs):
        attempts.append(kwargs["output_device_id"])
        return (1, 2)

    monkeypatch.setattr(config, "negotiate_full_duplex_channels", negotiate)
    route = config.build_run_config(_args(output=5), app_microphone_name=None)
    assert attempts == [5]
    assert route.output_device_id == 5
    assert not route.output_was_auto_selected
