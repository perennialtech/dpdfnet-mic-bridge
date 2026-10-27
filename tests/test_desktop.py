import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from dpdfnet_mic_bridge.desktop import BridgeWindow


def test_window_has_no_basic_model_or_output_controls(monkeypatch):
    app = QApplication.instance() or QApplication([])
    # Qt signal connections need a real callable, not unittest.mock objects.
    monkeypatch.setattr(BridgeWindow, "discover", lambda self: None)
    window = BridgeWindow()
    assert window.windowTitle() == "DPDFNet Mic Bridge"
    assert not window.start.isEnabled()
    assert not window.stop.isEnabled()
    assert window.log.isHidden()
    window.close()
    app.processEvents()


def test_health_events_update_status_and_notify_once(monkeypatch):
    import json

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(BridgeWindow, "discover", lambda self: None)
    window = BridgeWindow()
    window.mode = "worker"
    notified = []
    monkeypatch.setattr(window, "notify_overload", lambda: notified.append(True))

    class Process:
        def __init__(self, message):
            self.message = message

        def readAllStandardOutput(self):
            value, self.message = self.message, b""
            return value

    def receive(state):
        window.process = Process(
            (
                json.dumps({"event": "inference_health", "state": state, "rtf": 1.5})
                + "\n"
            ).encode()
        )
        window.read_output()

    receive("degraded")
    assert notified == [True]
    assert "slower than real time" in window.status.text()
    receive("healthy")
    assert notified == [True]
    assert window.status.text() == "Streaming enhanced microphone audio."
    window.process = None
    window.close()
    app.processEvents()
