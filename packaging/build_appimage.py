"""Wrap the frozen folder using checksum-pinned AppImage tools."""

from __future__ import annotations

import hashlib
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import tomllib
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", type=Path, default=ROOT / "dist/dpdfnet-mic-bridge")
    parser.add_argument("--appdir", type=Path, default=ROOT / "build/AppDir")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "release")
    args = parser.parse_args()
    version = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    appdir = args.appdir.resolve()
    if appdir.exists():
        raise RuntimeError(
            f"Use a clean build directory; AppDir already exists: {appdir}"
        )
    executable = appdir / "usr/bin/dpdfnet-mic-bridge"
    executable.parent.mkdir(parents=True)
    executable.write_text(
        '#!/bin/sh\nexec "$(dirname "$0")/../lib/dpdfnet-mic-bridge/dpdfnet-mic-bridge" "$@"\n'
    )
    executable.chmod(0o755)
    tools = {}
    manifest = json.loads((ROOT / "packaging/appimage-tools.json").read_text())
    for name, spec in manifest.items():
        target = ROOT / "build" / f"{name}.AppImage"
        if not target.exists():
            with (
                urlopen(spec["url"], timeout=120) as response,
                target.open("wb") as output,
            ):
                shutil.copyfileobj(response, output)
        with target.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if digest != spec["sha256"]:
            raise RuntimeError(f"Checksum mismatch for {name}")
        target.chmod(0o755)
        tools[name] = str(target)
    env = {
        **os.environ,
        "APPIMAGE_EXTRACT_AND_RUN": "1",
        "ARCH": "x86_64",
        "VERSION": version,
    }
    # PyInstaller and the PulseAudio staging step own the runtime dependency
    # trees. Do not run a second recursive collector over them or bundle glibc.
    subprocess.run(
        [
            tools["linuxdeploy"],
            "--appdir",
            str(appdir),
            "--desktop-file",
            str(ROOT / "packaging/dpdfnet-mic-bridge.desktop"),
            "--icon-file",
            str(ROOT / "packaging/dpdfnet-mic-bridge.svg"),
            "--custom-apprun",
            str(ROOT / "packaging/AppRun"),
        ],
        check=True,
        env=env,
    )
    shutil.copytree(args.bundle, appdir / "usr/lib/dpdfnet-mic-bridge")
    (appdir / "AppRun").chmod(0o755)
    release = args.output_dir.resolve()
    release.mkdir(parents=True, exist_ok=True)
    target = release / f"dpdfnet-mic-bridge-{version}-linux-x86_64.AppImage"
    subprocess.run(
        [
            tools["appimagetool"],
            "--runtime-file",
            tools["runtime"],
            str(appdir),
            str(target),
        ],
        check=True,
        env=env,
    )
    print(target)


if __name__ == "__main__":
    main()
