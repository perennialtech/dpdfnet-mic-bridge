import subprocess
import sys

import pytest

from dpdfnet_mic_bridge.app import main
from dpdfnet_mic_bridge.cli.parser import build_parser


def test_import_and_help_without_resource_module():
    code = """
import sys, platform, shutil, subprocess
sys.platform = 'win32'
platform.system = lambda: 'Windows'
sys.modules['resource'] = None
import dpdfnet_mic_bridge
import dpdfnet_mic_bridge.app
import dpdfnet_mic_bridge.realtime_env.cli
import dpdfnet_mic_bridge.realtime_env.runtime
import dpdfnet_mic_bridge.realtime_env.backend_latency
assert 'dpdfnet_mic_bridge.realtime_env.linux' not in sys.modules
"""
    subprocess.run([sys.executable, "-c", code], check=True)


def test_desktop_is_default(monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setitem(
        sys.modules,
        "dpdfnet_mic_bridge.desktop",
        SimpleNamespace(desktop_main=lambda: 17),
    )
    assert main([]) == 17


def test_realtime_is_off_for_ordinary_launch():
    assert build_parser().parse_args(["run"]).realtime == "off"


def test_removed_self_test_command_is_rejected(capsys):
    parser = build_parser()
    assert "self-test" not in parser.format_help()
    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["self-test"])
    assert exc.value.code == 2
    assert "invalid choice" in capsys.readouterr().err


def test_live_runner_imports_before_device_setup():
    from dpdfnet_mic_bridge.bridge.runner import run_bridge

    assert callable(run_bridge)


def test_desktop_child_uses_utf8_for_redirected_output(monkeypatch):
    import io
    from dpdfnet_mic_bridge import desktop_process

    output = io.BytesIO()
    stream = io.TextIOWrapper(output, encoding="cp1252")
    monkeypatch.setattr(sys, "stdout", stream)
    monkeypatch.setattr(
        desktop_process, "desktop_inventory", lambda: {"microphones": []}
    )
    assert desktop_process.internal_command(["_desktop-inventory"]) == 0
    stream.write("麦克风")
    stream.flush()
    assert stream.encoding == "utf-8"
    assert "麦克风".encode() in output.getvalue()
