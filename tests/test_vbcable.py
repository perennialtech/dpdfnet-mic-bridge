from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import sys
import types
from pathlib import Path
from zipfile import ZIP_STORED, ZipFile

import pytest

from dpdfnet_mic_bridge import vbcable


def staging_module():
    path = Path(__file__).resolve().parents[1] / "packaging/stage_vbcable.py"
    spec = importlib.util.spec_from_file_location("stage_vbcable", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_archive(extra_name=None):
    with io.BytesIO() as data:
        with ZipFile(data, "w", ZIP_STORED) as package:
            package.writestr("VBCABLE_Setup_x64.exe", b"vendor-setup")
            package.writestr("vbMmeCable64_win10.inf", b"vendor-inf")
            package.writestr("readme.txt", b"vendor-notice")
            if extra_name is not None:
                package.writestr(extra_name, b"unsafe")
        return data.getvalue()


def test_archive_staging_requires_integrity_and_safe_members(tmp_path):
    stage = staging_module()
    archive = make_archive()
    config = {
        "sha256": hashlib.sha256(archive).hexdigest(),
        "setup_x64_sha256": hashlib.sha256(b"vendor-setup").hexdigest(),
    }
    dest = tmp_path / "driver"
    stage.extract_verified_archive(archive, dest, config)
    assert (dest / "readme.txt").read_bytes() == b"vendor-notice"

    with pytest.raises(ValueError, match="SHA-256"):
        stage.extract_verified_archive(
            archive, tmp_path / "wrong", {**config, "sha256": "0" * 64}
        )
    with pytest.raises(ValueError, match="installer"):
        stage.extract_verified_archive(
            archive, tmp_path / "wrong", {**config, "setup_x64_sha256": "0" * 64}
        )

    malicious = make_archive("../unsafe.sys")
    with pytest.raises(ValueError, match="Unsafe"):
        stage.extract_verified_archive(
            malicious,
            tmp_path / "malicious",
            {**config, "sha256": hashlib.sha256(malicious).hexdigest()},
        )
    assert not (tmp_path / "unsafe.sys").exists()


def test_bundled_setup_requires_frozen_windows_and_exact_vendor_binary(
    monkeypatch, tmp_path
):
    exe = tmp_path / "dpdfnet-mic-bridge.exe"
    path = tmp_path / "vendor/vbcable/VBCABLE_Setup_x64.exe"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"genuine")
    monkeypatch.setattr(sys, "executable", str(exe))
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(vbcable, "SETUP_SHA256", hashlib.sha256(b"genuine").hexdigest())
    assert vbcable.bundled_setup() == path
    path.write_bytes(b"tampered")
    assert vbcable.bundled_setup() is None
    monkeypatch.setattr(sys, "frozen", False)
    assert vbcable.bundled_setup() is None


def test_existing_driver_is_never_launched(monkeypatch):
    monkeypatch.setattr(vbcable, "driver_installed", lambda: True)
    monkeypatch.setattr(
        vbcable, "bundled_setup", lambda: pytest.fail("must not be called")
    )
    with pytest.raises(RuntimeError, match="already installed"):
        vbcable.launch_setup()


def test_elevated_launch_and_declined_elevation(monkeypatch, tmp_path):
    setup = tmp_path / "VBCABLE_Setup_x64.exe"
    monkeypatch.setattr(vbcable, "driver_installed", lambda: False)
    monkeypatch.setattr(vbcable, "bundled_setup", lambda: setup)
    launches = []

    def invoke(*args):
        launches.append(args)
        return 42

    monkeypatch.setattr(
        vbcable.ctypes,
        "windll",
        types.SimpleNamespace(shell32=types.SimpleNamespace(ShellExecuteW=invoke)),
        raising=False,
    )
    vbcable.launch_setup()
    assert launches == [(None, "runas", str(setup), None, str(tmp_path), 1)]

    monkeypatch.setattr(vbcable.ctypes.windll.shell32, "ShellExecuteW", lambda *_: 5)
    with pytest.raises(OSError, match="error 5"):
        vbcable.launch_setup()


def test_packaging_pinned_to_vendor_binary():
    root = Path(__file__).resolve().parents[1]
    pin = json.loads((root / "packaging/vbcable.json").read_text())
    assert pin["setup_x64_sha256"] == vbcable.SETUP_SHA256
    script = (root / "packaging/dpdfnet-mic-bridge.iss").read_text()
    assert "UsePreviousTasks=no" in script
    task = next(
        line
        for line in script.splitlines()
        if line.startswith('Name: "installvbcable"')
    )
    # A first install and an upgrade must require the user's opt-in.
    assert "Flags: unchecked" in task
    assert "Check: not VBCableInstalled" in task
    assert '#include "vbcable-installed.iss"' in script
    detector = (root / "packaging/vbcable-installed.iss").read_text()
    # Inno Pascal strings do not require escaped path separators.
    assert r"'SYSTEM\CurrentControlSet\Services\VBAudioVACMME'" in detector
    assert r"'SYSTEM\\CurrentControlSet" not in detector
    assert "Check: CanLaunchVBCableSetup" in script
    assert "GetSHA256OfFile" in script
    assert "skipifsilent" in script
    assert 'Verb: "runas"' in script
    assert "[UninstallRun]" not in script
