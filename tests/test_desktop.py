import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMessageBox

from dpdfnet_mic_bridge import vbcable
from dpdfnet_mic_bridge.desktop import BridgeWindow


class VendorSetup:
    def __init__(self):
        self.active = True
        self.closed = False

    def running(self):
        if not self.active:
            self.closed = True
        return self.active

    def close(self):
        self.closed = True


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

    count = 0

    def receive(state, sequence=None):
        nonlocal count
        count += 1
        if sequence is None:
            sequence = count
        window.process = Process(
            (
                json.dumps(
                    {
                        "event": "inference_health",
                        "state": state,
                        "rtf": 1.5,
                        "sequence": sequence,
                    }
                )
                + "\n"
            ).encode()
        )
        window.read_output()

    receive("degraded")
    receive("degraded")
    assert notified == [True]
    assert "Audio processing can't keep up" in window.status.text()
    window.process = Process(b'{"event":"ready"}\n')
    window.read_output()
    assert "Audio processing can't keep up" in window.status.text()
    receive("healthy")
    assert notified == [True]
    assert window.status.text() == "Noise reduction is on."
    receive("degraded", 8)
    receive("healthy", 7)
    assert "Audio processing can't keep up" in window.status.text()
    assert notified == [True, True]
    receive("healthy", 9)
    assert window.status.text() == "Noise reduction is on."
    window.process = None
    window.close()
    app.processEvents()


def test_vbcable_installer_blocks_parallel_setup_and_retries_after_exit(monkeypatch):
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

    def launch():
        process = VendorSetup()
        launches.append(process)
        return process

    monkeypatch.setattr(vbcable, "launch_setup", launch)

    window = BridgeWindow()
    window.install_driver.setVisible(True)
    window.install_vbcable()
    assert len(launches) == 1
    assert window.vbcable_setup_running()
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
    assert "Complete VB-CABLE setup" in window.driver.text()
    assert window.install_driver.isHidden()
    window.install_vbcable()
    assert len(launches) == 1

    # Vendor GUI has exited without registering the service (Cancel/Failure).
    launches[0].active = False
    window.vbcable_setup_timer.timeout.emit()
    assert launches[0].closed
    assert "wasn't installed" in window.driver.text()
    assert not window.install_driver.isHidden()
    window.install_vbcable()
    assert len(launches) == 2
    assert window.vbcable_setup_running()
    assert window.install_driver.isHidden()

    window.close()
    app.processEvents()
    assert launches[1].closed


def test_vbcable_registered_after_setup_exit_cannot_launch_vendor_uninstaller(
    monkeypatch,
):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(BridgeWindow, "discover", lambda self: None)
    installed = [False]
    monkeypatch.setattr(vbcable, "driver_installed", lambda: installed[0])
    monkeypatch.setattr(vbcable, "bundled_setup", lambda: "vendor-installer")
    monkeypatch.setattr(
        QMessageBox, "question", lambda *_: QMessageBox.StandardButton.Yes
    )
    monkeypatch.setattr(QMessageBox, "information", lambda *_: None)
    process = VendorSetup()
    launches = []
    monkeypatch.setattr(
        vbcable, "launch_setup", lambda: (launches.append(True), process)[1]
    )
    window = BridgeWindow()
    window.driver.setVisible(True)
    window.install_vbcable()
    assert launches == [True]

    process.active = False
    installed[0] = True
    window.check_vbcable_setup()
    assert "registered" in window.driver.text()
    assert window.install_driver.isHidden()
    window.install_vbcable()  # Bypassing the disabled button is also safe.
    assert launches == [True]

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
        return VendorSetup()

    monkeypatch.setattr(vbcable, "launch_setup", launch)
    window = BridgeWindow()
    window.install_vbcable()
    assert not window.vbcable_setup_running()
    window.install_vbcable()
    assert launches == ["attempt", "attempt"]
    assert window.vbcable_setup_running()

    window.close()
    app.processEvents()


@pytest.mark.skipif(sys.platform != "linux", reason="POSIX notify-send fixture")
def test_notify_send_timeout_waits_for_child_exit(monkeypatch, tmp_path, capfd):
    import time
    from types import SimpleNamespace

    from PySide6.QtCore import QProcess

    from dpdfnet_mic_bridge import desktop

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(BridgeWindow, "discover", lambda self: None)
    monkeypatch.setattr(desktop.platform, "system", lambda: "Linux")
    notifier = tmp_path / "notify-send"
    notifier.write_text("#!/bin/sh\nsleep 30\n")
    notifier.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}:{os.environ.get('PATH', '')}")
    window = BridgeWindow()
    messages = []
    window.tray = SimpleNamespace(showMessage=lambda *args: messages.append(args))
    window.notify_overload()
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        app.processEvents()
        if window._notification_processes:
            process = next(iter(window._notification_processes))
            if process.state() == QProcess.ProcessState.Running:
                break
        time.sleep(0.01)
    else:
        raise AssertionError("notify-send did not start")

    # A crash error may arrive before finished, particularly after kill().
    window._notification_error(process, QProcess.ProcessError.Crashed)
    assert process in window._notification_processes
    window._notification_timeout(process)
    deadline = time.monotonic() + 3
    while window._notification_processes and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    assert not window._notification_processes
    assert len(messages) == 1
    window.close()
    app.processEvents()
    assert "Destroyed while process" not in capfd.readouterr().err


@pytest.mark.skipif(sys.platform != "linux", reason="POSIX notify-send fixture")
def test_closing_window_waits_for_running_notification(monkeypatch, tmp_path, capfd):
    import time

    from PySide6.QtCore import QProcess

    from dpdfnet_mic_bridge import desktop

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(BridgeWindow, "discover", lambda self: None)
    monkeypatch.setattr(desktop.platform, "system", lambda: "Linux")
    notifier = tmp_path / "notify-send"
    notifier.write_text("#!/bin/sh\nsleep 30\n")
    notifier.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}:{os.environ.get('PATH', '')}")
    window = BridgeWindow()
    window.notify_overload()
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        app.processEvents()
        if window._notification_processes:
            process = next(iter(window._notification_processes))
            if process.state() == QProcess.ProcessState.Running:
                break
        time.sleep(0.01)
    else:
        raise AssertionError("notify-send did not start")
    window.close()
    deadline = time.monotonic() + 3
    while window._notification_processes and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    assert not window._notification_processes
    app.processEvents()
    assert "Destroyed while process" not in capfd.readouterr().err


def test_diagnostics_button_matches_visibility(monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(BridgeWindow, "discover", lambda self: None)
    window = BridgeWindow()
    assert window.log.isHidden()
    assert window.diagnostics.text() == "Show details"

    window.diagnostics.click()
    assert not window.log.isHidden()
    assert window.diagnostics.text() == "Hide details"

    window.diagnostics.click()
    assert window.log.isHidden()
    assert window.diagnostics.text() == "Show details"

    window.show_diagnostics()
    assert not window.log.isHidden()
    assert window.diagnostics.text() == "Hide details"
    window.close()
    app.processEvents()


def test_inventory_status_distinguishes_no_microphones_from_ready(monkeypatch):
    import json
    from types import SimpleNamespace

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(BridgeWindow, "discover", lambda self: None)
    window = BridgeWindow()
    window.mode = "inventory"

    def inventory(microphones):
        window.process = SimpleNamespace(
            readAllStandardOutput=lambda: (
                json.dumps({"microphones": microphones, "driver_ready": True}) + "\n"
            ).encode()
        )
        window.read_output()

    inventory([])
    assert (
        window.status.text() == "No microphones found. Connect one and click Refresh."
    )
    assert not window.start.isEnabled()
    inventory([{"name": "USB microphone", "key": "mic0"}])
    assert window.status.text() == "Ready to start."
    assert window.start.isEnabled()
    window.process = None
    window.close()
    app.processEvents()


def test_inventory_error_shows_details(monkeypatch):
    from types import SimpleNamespace

    from PySide6.QtCore import QProcess

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(BridgeWindow, "discover", lambda self: None)
    window = BridgeWindow()
    window.mode = "inventory"
    window.process = SimpleNamespace(
        readAllStandardOutput=lambda: b"",
        readAllStandardError=lambda: b"device enumeration failed\n",
        deleteLater=lambda: None,
    )
    window.finished(2, QProcess.ExitStatus.CrashExit)
    assert (
        window.status.text()
        == "Couldn't list microphones. Check details and click Refresh."
    )
    assert window.diagnostics.text() == "Hide details"
    assert "device enumeration failed" in window.log.toPlainText()
    window.close()
    app.processEvents()


def test_missing_vbcable_without_bundled_setup_links_to_vendor(monkeypatch):
    import json
    from types import SimpleNamespace

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(BridgeWindow, "discover", lambda self: None)
    monkeypatch.setattr("dpdfnet_mic_bridge.desktop.platform.system", lambda: "Windows")
    monkeypatch.setattr(vbcable, "driver_installed", lambda: False)
    monkeypatch.setattr(vbcable, "bundled_setup", lambda: None)
    window = BridgeWindow()
    window.mode = "inventory"
    window.process = SimpleNamespace(
        readAllStandardOutput=lambda: (
            json.dumps(
                {"microphones": [{"name": "Mic", "key": "1"}], "driver_ready": False}
            )
            + "\n"
        ).encode()
    )
    window.read_output()
    assert not window.start.isEnabled()
    assert window.install_driver.isHidden()
    assert "VB-Audio website" in window.driver.text()
    assert "below" not in window.driver.text()
    assert "Finish VB-CABLE setup" in window.status.text()
    window.process = None
    window.close()
    app.processEvents()
