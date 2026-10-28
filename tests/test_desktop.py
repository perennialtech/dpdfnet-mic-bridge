import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMessageBox

from dpdfnet_mic_bridge import vbcable
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
    receive("degraded")
    assert notified == [True]
    assert "missing audio deadlines" in window.status.text()
    window.process = Process(b'{"event":"ready"}\n')
    window.read_output()
    assert "missing audio deadlines" in window.status.text()
    receive("healthy")
    assert notified == [True]
    assert window.status.text() == "Streaming enhanced microphone audio."
    window.process = None
    window.close()
    app.processEvents()


def test_vbcable_installer_launches_only_once_even_after_refresh(monkeypatch):
    import json
    from types import SimpleNamespace

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(BridgeWindow, "discover", lambda self: None)
    monkeypatch.setattr("dpdfnet_mic_bridge.desktop.platform.system", lambda: "Windows")
    monkeypatch.setattr(vbcable, "driver_installed", lambda: False)
    monkeypatch.setattr(vbcable, "bundled_setup", lambda: "vendor-installer")
    monkeypatch.setattr(
        QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes
    )
    monkeypatch.setattr(QMessageBox, "information", lambda *args: None)
    launches = []
    monkeypatch.setattr(vbcable, "launch_setup", lambda: launches.append("launched"))

    window = BridgeWindow()
    window.install_driver.setVisible(True)
    window.install_vbcable()
    assert launches == ["launched"]
    assert window.vbcable_setup_launched
    assert window.install_driver.isHidden()

    window.install_vbcable()
    window.discover()
    window.mode = "inventory"
    window.process = SimpleNamespace(
        readAllStandardOutput=lambda: (
            json.dumps({"microphones": [], "driver_ready": False}) + "\n"
        ).encode()
    )
    window.read_output()
    window.process = None
    assert "setup was launched" in window.driver.text()
    assert window.install_driver.isHidden()
    window.install_vbcable()
    assert launches == ["launched"]

    window.close()
    app.processEvents()


def test_failed_notify_send_falls_back_to_tray(monkeypatch, tmp_path):
    import time
    from types import SimpleNamespace

    from dpdfnet_mic_bridge import desktop

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(BridgeWindow, "discover", lambda self: None)
    monkeypatch.setattr(desktop.platform, "system", lambda: "Linux")
    notifier = tmp_path / "notify-send"
    notifier.write_text("#!/bin/sh\nexit 17\n")
    notifier.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}:{os.environ.get('PATH', '')}")
    window = BridgeWindow()
    messages = []
    window.tray = SimpleNamespace(showMessage=lambda *args: messages.append(args))
    window.notify_overload()
    deadline = time.monotonic() + 2
    while not messages and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    assert len(messages) == 1
    assert not window._notification_processes
    window.close()
    app.processEvents()


def test_worker_diagnostics_are_read_from_stderr(monkeypatch):
    from types import SimpleNamespace

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(BridgeWindow, "discover", lambda self: None)
    window = BridgeWindow()
    window.process = SimpleNamespace(readAllStandardError=lambda: b"worker log\n")
    window.read_error()
    assert "worker log" in window.log.toPlainText()
    window.process = None
    window.close()
    app.processEvents()


def test_vbcable_failed_launch_allows_retry(monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(BridgeWindow, "discover", lambda self: None)
    monkeypatch.setattr(vbcable, "driver_installed", lambda: False)
    monkeypatch.setattr(
        QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes
    )
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: None)
    monkeypatch.setattr(QMessageBox, "information", lambda *args: None)
    launches = []

    def launch():
        launches.append("attempt")
        if len(launches) == 1:
            raise OSError("UAC declined")

    monkeypatch.setattr(vbcable, "launch_setup", launch)
    window = BridgeWindow()
    window.install_vbcable()
    assert not window.vbcable_setup_launched
    window.install_vbcable()
    assert launches == ["attempt", "attempt"]
    assert window.vbcable_setup_launched

    window.close()
    app.processEvents()
