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
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .desktop_process import child_command


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
        self.status = QLabel("Looking for microphones…")
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
        self.process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        env = QProcessEnvironment.systemEnvironment()
        env.insert("PYTHONUNBUFFERED", "1")
        env.insert("PYTHONIOENCODING", "utf-8")
        self.process.setProcessEnvironment(env)
        self.process.readyReadStandardOutput.connect(self.read_output)
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
        self.status.setText("Loading model and starting audio…")
        self.spawn(
            "worker",
            ["_desktop-worker", "--device", str(self.microphones.currentData())],
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
                self.driver.setVisible(not message["driver_ready"])
                self.start.setEnabled(
                    bool(message["microphones"]) and message["driver_ready"]
                )
                self.status.setText(
                    "Ready."
                    if self.start.isEnabled()
                    else "Choose an available microphone and complete audio setup, then Refresh."
                )
            elif message.get("event") == "ready" and not self.stopping:
                self.status.setText("Streaming enhanced microphone audio.")
            elif message.get("event") == "endpoints":
                self.endpoint = message

    def process_error(self, error):
        if error == QProcess.ProcessError.FailedToStart:
            self.log.appendPlainText(self.process.errorString())
            self.finished(2, QProcess.ExitStatus.CrashExit)

    def finished(self, code, exit_status):
        self.deadline.stop()
        self.read_output()
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
