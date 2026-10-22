import ctypes
from pathlib import Path
import runpy
import sys
from types import SimpleNamespace

import pytest


def test_windowed_startup_error_is_visible(monkeypatch, tmp_path):
    messages = []

    def message_box(*args):
        messages.append(args)
        return 1

    def failed_main():
        print("The bundled application is damaged. Reinstall it.", file=sys.stderr)
        return 2

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sys, "stderr", None)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(
        ctypes,
        "WinDLL",
        lambda *args, **kwargs: SimpleNamespace(MessageBoxW=message_box),
        raising=False,
    )
    monkeypatch.setitem(
        sys.modules, "dpdfnet_mic_bridge.app", SimpleNamespace(main=failed_main)
    )
    launcher = Path(__file__).resolve().parents[1] / "packaging/launcher.py"
    with pytest.raises(SystemExit) as result:
        runpy.run_path(str(launcher))
    assert result.value.code == 2
    assert len(messages) == 1
    assert "Reinstall" in messages[0][1]
    assert message_box.argtypes[0] == ctypes.c_void_p
