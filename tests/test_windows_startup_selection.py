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
                "name": "CABLE Input (VB-Audio Virtual Cable)",
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
                "name": "CABLE-A Input (VB-Audio Cable A)",
                "hostapi": 0,
                "max_input_channels": 0,
                "max_output_channels": 2,
            },
            {
                "id": 6,
                "name": "CABLE-A Output (VB-Audio Cable A)",
                "hostapi": 0,
                "max_input_channels": 2,
                "max_output_channels": 0,
            },
            {
                "id": 7,
                "name": "CABLE Output (VB-Audio Virtual Cable)",
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
    assert route.app_microphone_name == "CABLE-A Output (VB-Audio Cable A)"


def test_windows_never_selects_cable_without_its_matching_capture(monkeypatch):
    existing = _inventory()
    inventory = AudioInventory(
        devices=[item for item in existing.devices if item["id"] != 6],
        hostapi_names=existing.hostapi_names,
    )
    _setup(monkeypatch, inventory)
    attempted = []

    def negotiate(**kwargs):
        attempted.append(kwargs["output_device_id"])
        if kwargs["output_device_id"] == 3:
            raise RuntimeError("Unavailable output")
        return (1, 2)

    monkeypatch.setattr(config, "negotiate_full_duplex_channels", negotiate)
    with pytest.raises(RuntimeError, match="capture endpoint matching"):
        config.build_run_config(_args(), app_microphone_name=None)
    # B's render is openable, but must not be used without B's capture.
    assert attempted == [3]


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
    assert route.app_microphone_name == "CABLE-A Output (VB-Audio Cable A)"


def test_windows_explicit_output_requires_its_own_capture(monkeypatch):
    existing = _inventory()
    inventory = AudioInventory(
        devices=[item for item in existing.devices if item["id"] != 6],
        hostapi_names=existing.hostapi_names,
    )
    _setup(monkeypatch, inventory)
    with pytest.raises(ValueError, match="capture endpoint matching"):
        config.build_run_config(_args(output=5), app_microphone_name=None)


@pytest.mark.parametrize("letter", ["A", "B", "C", "D"])
def test_real_vb_cable_families_are_recognized(letter):
    from dpdfnet_mic_bridge.audio.tags import infer_device_tags, vb_cable_family

    render = f"CABLE-{letter} Input (VB-Audio Cable {letter})"
    capture = f"CABLE-{letter} Output (VB-Audio Cable {letter})"
    assert "vb-cable-playback" in infer_device_tags(render)
    assert "vb-cable-recording" in infer_device_tags(capture)
    assert vb_cable_family(render, "input") == letter.casefold()
    assert vb_cable_family(capture, "output") == letter.casefold()


def test_renamed_capture_is_accepted_for_unique_cable_family(monkeypatch):
    existing = _inventory()
    inventory = AudioInventory(
        devices=[
            {**item, "name": "CABLE Output (Voice Chat)"} if item["id"] == 7 else item
            for item in existing.devices
            if item["id"] not in (5, 6)
        ],
        hostapi_names=existing.hostapi_names,
    )
    _setup(monkeypatch, inventory)
    monkeypatch.setattr(
        config, "negotiate_full_duplex_channels", lambda **kwargs: (1, 2)
    )
    route = config.build_run_config(_args(), app_microphone_name=None)
    assert route.output_device_id == 3
    assert route.app_microphone_name == "CABLE Output (Voice Chat)"


def test_ambiguous_renamed_cable_capture_is_rejected(monkeypatch):
    existing = _inventory()
    inventory = AudioInventory(
        devices=[
            {**item, "name": "CABLE Output (Voice Chat)"} if item["id"] == 7 else item
            for item in existing.devices
            if item["id"] not in (5, 6)
        ]
        + [
            {
                "id": 8,
                "name": "CABLE Input (VB-Audio Another Cable)",
                "hostapi": 0,
                "max_input_channels": 0,
                "max_output_channels": 2,
            }
        ],
        hostapi_names=existing.hostapi_names,
    )
    _setup(monkeypatch, inventory)
    with pytest.raises(RuntimeError, match="ambiguous"):
        config.build_run_config(_args(), app_microphone_name=None)


def test_fully_renamed_windows_cable_uses_driver_metadata(monkeypatch):
    existing = _inventory()
    inventory = AudioInventory(
        devices=[
            (
                {
                    **item,
                    "name": (
                        "Studio Playback" if item["id"] == 3 else "Conference Capture"
                    ),
                    "manufacturer": "VB-Audio Software",
                    "device_description": "VB-Audio Virtual Cable",
                    "container_id": "virtual-cable-1",
                }
                if item["id"] in (3, 7)
                else item
            )
            for item in existing.devices
            if item["id"] not in (5, 6)
        ],
        hostapi_names=existing.hostapi_names,
    )
    _setup(monkeypatch, inventory)
    monkeypatch.setattr(config, "negotiate_full_duplex_channels", lambda **kw: (1, 2))
    route = config.build_run_config(_args(), app_microphone_name=None)
    assert (route.output_device_id, route.app_microphone_name) == (
        3,
        "Conference Capture",
    )
    from dpdfnet_mic_bridge.audio.tags import infer_device_tags

    assert "vb-cable-playback" in infer_device_tags(inventory.devices[2])


def test_duplicate_playback_names_are_rejected_without_identity(monkeypatch):
    existing = _inventory()
    inventory = AudioInventory(
        devices=[item for item in existing.devices if item["id"] not in (5, 6)]
        + [
            {
                "id": 8,
                "name": "CABLE Input (VB-Audio Virtual Cable)",
                "hostapi": 0,
                "max_input_channels": 0,
                "max_output_channels": 2,
            }
        ],
        hostapi_names=existing.hostapi_names,
    )
    _setup(monkeypatch, inventory)
    with pytest.raises(RuntimeError, match="ambiguous"):
        config.build_run_config(_args(), app_microphone_name=None)


def test_duplicate_playback_names_can_use_distinct_container_identity(monkeypatch):
    existing = _inventory()
    inventory = AudioInventory(
        devices=[
            {**item, "container_id": "one"} if item["id"] in (3, 7) else item
            for item in existing.devices
            if item["id"] not in (5, 6)
        ]
        + [
            {
                "id": 8,
                "name": "CABLE Input (VB-Audio Virtual Cable)",
                "hostapi": 0,
                "max_input_channels": 0,
                "max_output_channels": 2,
                "container_id": "two",
            }
        ],
        hostapi_names=existing.hostapi_names,
    )
    _setup(monkeypatch, inventory)
    monkeypatch.setattr(config, "negotiate_full_duplex_channels", lambda **kw: (1, 2))
    route = config.build_run_config(_args(), app_microphone_name=None)
    assert route.output_device_id == 3
    assert route.app_microphone_name == "CABLE Output (VB-Audio Virtual Cable)"
