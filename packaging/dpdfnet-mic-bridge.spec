# packaging/dpdfnet-mic-bridge.spec
# -*- mode: python ; coding: utf-8 -*-

import os
from pathlib import Path
import sys
import sysconfig

from PyInstaller.utils.hooks import collect_all, collect_data_files, copy_metadata

# Freeze the installed wheel, not stale metadata in a source checkout.
sys.path.insert(0, sysconfig.get_path("purelib"))

root = Path(SPECPATH).parent
notices = root / "build" / "notices"
if not notices.is_dir():
    raise RuntimeError("Run packaging/stage_notices.py before freezing")

from dpdfnet_mic_bridge.models.loading import resolve_local_model
from dpdfnet_mic_bridge.models.registry import DEFAULT_MODEL

resolve_local_model(DEFAULT_MODEL)
datas = [(str(notices), "notices")]
binaries = []
hiddenimports = []

datas += collect_data_files("dpdfnet_mic_bridge")
datas += copy_metadata("dpdfnet_mic_bridge", recursive=True)

for pkg in [
    "dpdfnet",
    "numba",
    "llvmlite",
    "librosa",
    "onnxruntime",
]:
    pkg_datas, pkg_binaries, pkg_hiddenimports = collect_all(pkg)
    datas += pkg_datas
    binaries += pkg_binaries
    hiddenimports += pkg_hiddenimports

    datas += copy_metadata(pkg)

if sys.platform == "linux":
    pulse = root / "build" / "pulse"
    for name in ("pactl", "parec", "pacat"):
        path = pulse / "bin" / name
        if not path.is_file():
            raise RuntimeError("Run packaging/stage_linux_clients.py before freezing")
        binaries.append((str(path), "pulse/bin"))
    binaries += [(str(path), "pulse/lib") for path in (pulse / "lib").iterdir()]

portaudio_path = (
    os.environ.get("PORTAUDIO_DLL")
    or os.environ.get("PORTAUDIO_SO")
    or os.environ.get("PORTAUDIO_DYLIB")
)

if portaudio_path:
    if not Path(portaudio_path).is_file():
        raise RuntimeError(f"PortAudio library does not exist: {portaudio_path}")
    binaries.append((portaudio_path, "."))
elif sys.platform == "win32":
    raise RuntimeError("PORTAUDIO_DLL must name the vcpkg PortAudio runtime DLL")

launcher_path = os.path.join(SPECPATH, "launcher.py")
hooks_path = os.path.join(SPECPATH, "hooks")

root_path = os.path.abspath(os.path.join(SPECPATH, ".."))

a = Analysis(
    [launcher_path],
    pathex=[root_path],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[hooks_path],
    excludes=[
        "pytest",
        "black",
        "ruff",
        "mypy",
        "basedpyright",
        "tkinter",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

if sys.platform == "linux":
    sys.path.insert(0, str(root / "packaging"))
    from stage_notices import append_native_notices
    system_libraries = [source for _, source, _ in a.binaries if source.startswith(("/lib/", "/usr/lib/"))]
    append_native_notices(system_libraries, notices)
    known_data = {item[0] for item in a.datas}
    for path in notices.rglob("*"):
        if path.is_file():
            target = "notices/" + path.relative_to(notices).as_posix()
            if target not in known_data:
                a.datas.append((target, str(path), "DATA"))

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="dpdfnet-mic-bridge",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=sys.platform != "win32",
)

executables = [exe]
if sys.platform == "win32":
    executables.append(EXE(
        pyz, a.scripts, [], exclude_binaries=True,
        name="dpdfnet-mic-bridge-cli", console=True, strip=False, upx=False,
    ))

coll = COLLECT(
    *executables,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="dpdfnet-mic-bridge",
)
