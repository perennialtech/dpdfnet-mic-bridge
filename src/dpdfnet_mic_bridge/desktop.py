"""Small native desktop UI; inference and device discovery run out of process."""

from __future__ import annotations

import json
import platform
import sys

from PySide6.QtCore import QProcess, QProcessEnvironment, QTimer
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QStyle,
    QSystemTrayIcon,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .desktop_process import child_command
from . import vbcable


class BridgeWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("DPDFNet Mic Bridge")
        self.resize(560, 320)
        self.process = None
        self.closing = False
        self.stopping = False
        self.endpoint = None
        self.buffer = ""
        self.mode = ""
        self.forced = False
        self.worker_failed = False
        self.inference_degraded = False
        self._notification_processes = set()
        self.vbcable_setup_launched = False
        self.status = QLabel("Looking for microphones…")
        self.tray = None
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray = QSystemTrayIcon(
                self.style().standardIcon(QStyle.StandardPixmap.SP_ComputerIcon),
                self,
            )
            self.tray.setToolTip("DPDFNet Mic Bridge")
            self.tray.show()
        self.microphones = QComboBox()
        self.refresh = QPushButton("Refresh")
        self.refresh.clicked.connect(self.discover)
        self.start = QPushButton("Start")
        self.start.setEnabled(False)
        self.start.clicked.connect(self.start_bridge)
        self.stop = QPushButton("Stop")
        self.stop.setEnabled(False)
        self.stop.clicked.connect(self.stop_bridge)
        name = "CABLE Output" if platform.system() == "Windows" else "DPDFNet_Mic"
        guidance = QLabel(f"In Discord or another calling app, select: <b>{name}</b>")
        guidance.setWordWrap(True)
        self.driver = QLabel(
            'VB-CABLE is missing. <a href="https://vb-audio.com/Cable/">Get it from VB-Audio</a>, install its driver, then Refresh. The driver may need administrator approval and a restart.'
        )
        self.driver.setWordWrap(True)
        self.driver.setOpenExternalLinks(True)
        self.driver.hide()
        self.install_driver = QPushButton("Install VB-CABLE…")
        self.install_driver.clicked.connect(self.install_vbcable)
        self.install_driver.hide()
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(1500)
        self.log.hide()
        diagnostics = QPushButton("Show/hide diagnostics")
        diagnostics.clicked.connect(
            lambda: self.log.setVisible(not self.log.isVisible())
        )
        layout = QVBoxLayout()
        layout.addWidget(QLabel("Microphone"))
        row = QHBoxLayout()
        row.addWidget(self.microphones, 1)
        row.addWidget(self.refresh)
        layout.addLayout(row)
        layout.addWidget(self.driver)
        layout.addWidget(self.install_driver)
        buttons = QHBoxLayout()
        buttons.addWidget(self.start)
        buttons.addWidget(self.stop)
        layout.addLayout(buttons)
        layout.addWidget(self.status)
        layout.addWidget(guidance)
        layout.addWidget(diagnostics)
        layout.addWidget(self.log)
        widget = QWidget()
        widget.setLayout(layout)
        self.setCentralWidget(widget)
        self.deadline = QTimer(self)
        self.deadline.setSingleShot(True)
        self.deadline.timeout.connect(self.force_stop)
        QTimer.singleShot(0, self.discover)

    def spawn(self, mode, arguments):
        self.mode = mode
        self.buffer = ""
        self.process = QProcess(self)
        self.process.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)
        env = QProcessEnvironment.systemEnvironment()
        env.insert("PYTHONUNBUFFERED", "1")
        env.insert("PYTHONIOENCODING", "utf-8")
        self.process.setProcessEnvironment(env)
        self.process.readyReadStandardOutput.connect(self.read_output)
        self.process.readyReadStandardError.connect(self.read_error)
        self.process.finished.connect(self.finished)
        self.process.errorOccurred.connect(self.process_error)
        executable, arguments = child_command(arguments)
        self.process.start(executable, arguments)

    def discover(self):
        self.start.setEnabled(False)
        self.refresh.setEnabled(False)
        self.microphones.clear()
        self.status.setText("Looking for microphones…")
        self.spawn("inventory", ["_desktop-inventory"])

    def start_bridge(self):
        self.start.setEnabled(False)
        self.refresh.setEnabled(False)
        self.microphones.setEnabled(False)
        self.stop.setEnabled(True)
        self.endpoint = None
        self.stopping = self.forced = False
        self.worker_failed = False
        self.inference_degraded = False
        self.status.setText("Loading model and starting audio…")
        self.spawn(
            "worker",
            ["_desktop-worker", "--device", str(self.microphones.currentData())],
        )

    def install_vbcable(self):
        if self.vbcable_setup_launched:
            return
        if vbcable.driver_installed():
            QMessageBox.information(
                self,
                "VB-CABLE already installed",
                "The VB-CABLE driver is already registered. Restart Windows, then Refresh. "
                "The vendor setup may offer to remove an installed driver.",
            )
            return
        if (
            QMessageBox.question(
                self,
                "Install VB-CABLE?",
                "VB-CABLE is a separate donationware driver by VB-Audio (www.vb-cable.com). "
                "All contributions are welcome. Its setup will request administrator "
                "approval, and you must restart Windows after installation. Continue?",
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        try:
            vbcable.launch_setup()
        except (OSError, RuntimeError) as exc:
            QMessageBox.warning(self, "VB-CABLE installation", str(exc))
            return
        # The vendor setup can still be running when ShellExecuteW returns.
        # Keep the action disabled for this session, even across Refresh.
        self.vbcable_setup_launched = True
        self.install_driver.hide()
        QMessageBox.information(
            self,
            "VB-CABLE installer launched",
            "Complete installation in the VB-Audio window, restart Windows, "
            "then return here and click Refresh. DPDFNet Mic Bridge will never "
            "uninstall this shared driver.",
        )

    def stop_bridge(self):
        if self.process is None:
            return
        self.stopping = True
        self.stop.setEnabled(False)
        self.status.setText("Stopping and removing virtual audio endpoints…")
        self.process.write(b"stop\n")
        self.process.closeWriteChannel()
        self.deadline.start(10000)

    def force_stop(self):
        if self.process is not None:
            self.forced = True
            self.log.appendPlainText(
                "The child did not stop within 10 seconds; ending it and recovering owned endpoints."
            )
            self.process.kill()

    def read_output(self):
        self.buffer += bytes(self.process.readAllStandardOutput()).decode(
            "utf-8", errors="replace"
        )
        while "\n" in self.buffer:
            line, self.buffer = self.buffer.split("\n", 1)
            self.log.appendPlainText(line.rstrip())
            try:
                message = json.loads(line)
            except ValueError:
                continue
            if self.mode == "inventory" and "microphones" in message:
                for item in message["microphones"]:
                    self.microphones.addItem(item["name"], item["key"])
                missing_driver = (
                    platform.system() == "Windows" and not message["driver_ready"]
                )
                self.driver.setVisible(missing_driver)
                self.install_driver.hide()
                if missing_driver:
                    if vbcable.driver_installed():
                        self.driver.setText(
                            "VB-CABLE is registered but its audio endpoints are missing. "
                            "Restart Windows, check Sound settings, then Refresh. "
                            '<a href="https://vb-audio.com/Cable/">VB-Audio support</a>.'
                        )
                    else:
                        self.driver.setText(
                            "VB-CABLE by VB-Audio is missing. It is donationware; "
                            'all contributions are welcome. <a href="https://www.vb-cable.com">'
                            "VB-Audio website</a>. Installation needs administrator "
                            "approval and a restart."
                        )
                        if self.vbcable_setup_launched:
                            self.driver.setText(
                                "VB-CABLE setup was launched. Complete installation "
                                "in the VB-Audio window, restart Windows, then Refresh."
                            )
                        self.install_driver.setVisible(
                            not self.vbcable_setup_launched
                            and vbcable.bundled_setup() is not None
                        )
                self.start.setEnabled(
                    bool(message["microphones"]) and message["driver_ready"]
                )
                self.status.setText(
                    "Ready."
                    if self.start.isEnabled()
                    else "Choose an available microphone and complete audio setup, then Refresh."
                )
            elif message.get("event") == "ready" and not self.stopping:
                if not self.inference_degraded:
                    self.status.setText("Streaming enhanced microphone audio.")
            elif message.get("event") == "endpoints":
                self.endpoint = message
            elif (
                message.get("event") == "inference_health"
                and self.mode == "worker"
                and not self.stopping
            ):
                if message.get("state") == "degraded":
                    if self.inference_degraded:
                        continue
                    self.inference_degraded = True
                    self.status.setText(
                        "Inference is missing audio deadlines; your microphone may stutter."
                    )
                    self.notify_overload()
                elif message.get("state") == "healthy":
                    self.inference_degraded = False
                    self.status.setText("Streaming enhanced microphone audio.")

    def read_error(self):
        diagnostics = bytes(self.process.readAllStandardError()).decode(
            "utf-8", errors="replace"
        )
        if diagnostics:
            self.log.appendPlainText(diagnostics.rstrip("\n"))

    def notify_overload(self):
        title = "DPDFNet Mic Bridge"
        detail = "Inference is missing audio deadlines; your microphone may stutter."
        if platform.system() == "Linux":
            # Observe the exit status asynchronously. Successful process launch
            # does not mean the desktop notification server accepted the request.
            notifier = QProcess(self)
            notifier.setProgram("notify-send")
            notifier.setArguments(["--app-name=DPDFNet Mic Bridge", title, detail])
            env = QProcessEnvironment.systemEnvironment()
            if getattr(sys, "frozen", False):
                original = env.value("LD_LIBRARY_PATH_ORIG")
                if original:
                    env.insert("LD_LIBRARY_PATH", original)
                else:
                    env.remove("LD_LIBRARY_PATH")
            notifier.setProcessEnvironment(env)
            self._notification_processes.add(notifier)
            notifier.finished.connect(
                lambda code, status, process=notifier: self._notification_finished(
                    process, code, status
                )
            )
            notifier.errorOccurred.connect(
                lambda error, process=notifier: self._complete_notification(
                    process, False
                )
            )
            notifier.start()
            QTimer.singleShot(
                3000, lambda process=notifier: self._notification_timeout(process)
            )
            return
        self._notification_fallback(title, detail)

    def _notification_fallback(self, title, detail):
        if self.tray is not None:
            self.tray.showMessage(title, detail, QSystemTrayIcon.MessageIcon.Warning)

    def _notification_finished(self, process, code, status):
        self._complete_notification(
            process, code == 0 and status == QProcess.ExitStatus.NormalExit
        )

    def _notification_timeout(self, process):
        if process in self._notification_processes:
            process.kill()
            self._complete_notification(process, False)

    def _complete_notification(self, process, succeeded):
        if process not in self._notification_processes:
            return
        self._notification_processes.remove(process)
        if not succeeded:
            self._notification_fallback(
                "DPDFNet Mic Bridge",
                "Inference is missing audio deadlines; your microphone may stutter.",
            )
        process.deleteLater()

    def process_error(self, error):
        if error == QProcess.ProcessError.FailedToStart:
            self.log.appendPlainText(self.process.errorString())
            self.finished(2, QProcess.ExitStatus.CrashExit)

    def finished(self, code, exit_status):
        self.deadline.stop()
        self.read_output()
        self.read_error()
        if self.buffer.strip():
            self.log.appendPlainText(self.buffer.strip())
        previous_mode = self.mode
        if previous_mode == "worker":
            self.worker_failed = self.forced or (code != 0 and not self.stopping)
        if previous_mode == "cleanup" and self.worker_failed:
            code = code or 2
        process = self.process
        self.process = None
        process.deleteLater()
        if previous_mode == "worker" and self.endpoint is not None:
            endpoint, self.endpoint = self.endpoint, None
            # The child normally cleaned up already. Verify ownership before a
            # recovery pass, including when a native crash skipped its finally.
            self.spawn(
                "cleanup",
                ["_desktop-cleanup", endpoint["sink"], json.dumps(endpoint["modules"])],
            )
            self.status.setText("Checking virtual microphone cleanup…")
            if code != 0 and not self.stopping:
                self.log.show()
            return
        self.stop.setEnabled(False)
        self.refresh.setEnabled(True)
        self.microphones.setEnabled(True)
        if previous_mode != "inventory":
            self.start.setEnabled(
                self.microphones.count() > 0 and self.driver.isHidden()
            )
            self.status.setText(
                "Stopped."
                if code == 0
                else "The bridge stopped with an error. Open diagnostics for details."
            )
        if code != 0:
            self.status.setText(
                "Audio setup or streaming failed. See diagnostics, then Refresh to retry."
            )
            self.log.show()
        if self.closing:
            self.close()

    def closeEvent(self, event):
        if self.process is not None:
            self.closing = True
            if self.mode == "worker":
                if not self.stopping:
                    self.stop_bridge()
            elif self.mode == "inventory":
                self.process.kill()
            event.ignore()
        else:
            # QTimer callbacks must never dereference a notifier destroyed
            # alongside the closing window.
            for notifier in tuple(self._notification_processes):
                self._notification_processes.remove(notifier)
                if notifier.state() != QProcess.ProcessState.NotRunning:
                    notifier.kill()
                notifier.deleteLater()
            event.accept()


def desktop_main() -> int:
    app = QApplication.instance() or QApplication([sys.argv[0]])
    try:
        window = BridgeWindow()
        window.show()
        return app.exec()
    except Exception as exc:
        QMessageBox.critical(None, "DPDFNet Mic Bridge", str(exc))
        return 2
