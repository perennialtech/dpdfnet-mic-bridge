from dpdfnet_mic_bridge.audio import pulse


def test_discovery_excludes_monitors_and_owned_sources(monkeypatch):
    sources = [
        {"index": 1, "name": "physical", "monitor_of_sink": None},
        {"index": 2, "name": "speaker.monitor", "monitor_of_sink": 5},
        {"index": 3, "name": "dpdfnet_mic_bridge_abc_capture", "monitor_of_sink": None},
    ]
    monkeypatch.setattr(pulse, "pulse_list", lambda kind: sources)
    assert pulse.microphones() == sources[:1]
    assert pulse.select_microphone(1)["name"] == "physical"


def test_stable_source_name_is_used(monkeypatch):
    monkeypatch.setattr(pulse, "microphones", lambda: [{"index": 9, "name": "usb_mic"}])
    assert pulse.select_microphone(None, "usb_mic")["index"] == 9
