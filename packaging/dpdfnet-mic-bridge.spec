# packaging/dpdfnet-mic-bridge.spec
# -*- mode: python ; coding: utf-8 -*-

import os

from PyInstaller.utils.hooks import collect_all, collect_data_files, copy_metadata

block_cipher = None

datas = []
binaries = []
hiddenimports = []

try:
    datas += collect_data_files("dpdfnet_mic_bridge")
    datas += copy_metadata("dpdfnet_mic_bridge")
except Exception:
    pass

for pkg in [
    "dpdfnet",
    "numba",
    "llvmlite",
    "librosa",
]:
    pkg_datas, pkg_binaries, pkg_hiddenimports = collect_all(pkg)
    datas += pkg_datas
    binaries += pkg_binaries
    hiddenimports += pkg_hiddenimports

    try:
        datas += copy_metadata(pkg)
    except Exception:
        pass

for pkg in [
    "numpy",
    "dpdfnet",
    "numba",
    "llvmlite",
    "librosa",
]:
    try:
        datas += copy_metadata(pkg)
    except Exception:
        pass

portaudio_path = (
    os.environ.get("PORTAUDIO_DLL")
    or os.environ.get("PORTAUDIO_SO")
    or os.environ.get("PORTAUDIO_DYLIB")
)

if portaudio_path:
    binaries.append((portaudio_path, "."))

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

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

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
    console=True,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="dpdfnet-mic-bridge",
)
