"""Collect installed dependency notices; fail when a required license is absent."""

from __future__ import annotations

import importlib.metadata as metadata
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import sysconfig
from urllib.request import urlopen

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / "build/notices"


def append_native_notices(paths, destination: Path) -> None:
    if sys.platform != "linux":
        return
    inventory_path = destination / "native-libraries.json"
    paths = set(map(str, paths)) | (
        set(json.loads(inventory_path.read_text()))
        if inventory_path.exists()
        else set()
    )
    inventory_path.write_text(json.dumps(sorted(paths), indent=2) + "\n")
    if not shutil.which("dpkg-query"):
        return  # Non-Debian local builds are not the supported release baseline.
    version_path = destination / "native-package-versions.txt"
    packages = (
        {line.split()[0] for line in version_path.read_text().splitlines()}
        if version_path.exists()
        else set()
    )
    for path in sorted(paths):
        candidates = [path, str(Path(path).resolve())]
        if path.startswith("/lib/"):
            candidates.append("/usr" + path)
        elif path.startswith("/usr/lib/"):
            candidates.append(path.removeprefix("/usr"))
        for candidate in dict.fromkeys(candidates):
            result = subprocess.run(
                ["dpkg-query", "-S", candidate], text=True, capture_output=True
            )
            if result.returncode == 0:
                break
        else:
            raise RuntimeError(f"Cannot identify the Debian package for {path}")
        package = result.stdout.split(": ", 1)[0].split(", ")[0]
        if package not in packages:
            packages.add(package)
            copyright_file = (
                Path("/usr/share/doc") / package.split(":")[0] / "copyright"
            )
            shutil.copy2(
                copyright_file,
                destination / f"native-{package.replace(':', '-')}-copyright.txt",
            )
    versions = subprocess.check_output(
        [
            "dpkg-query",
            "-W",
            "-f=${binary:Package} ${Version} ${source:Package} ${source:Version}\n",
            *sorted(packages),
        ],
        text=True,
    )
    version_path.write_text(versions)


def find_python_license(prefix: Path | None = None, stdlib: Path | None = None) -> Path:
    prefix = prefix or Path(sys.base_prefix)
    stdlib = stdlib or Path(sysconfig.get_path("stdlib"))
    for path in (
        stdlib / "LICENSE.txt",
        prefix / "LICENSE.txt",
        prefix / "LICENSE.cpython.txt",
        prefix / "LICENSE",
    ):
        if path.is_file():
            return path
    raise RuntimeError("The build interpreter's Python license file is missing")


def main():
    DEST.mkdir(parents=True, exist_ok=True)
    (DEST / "python-packages.json").unlink(missing_ok=True)
    shutil.copy2(ROOT / "LICENSE", DEST / "LICENSE-application.txt")
    shutil.copy2(
        ROOT / "packaging/THIRD_PARTY_NOTICES.md", DEST / "THIRD_PARTY_NOTICES.md"
    )
    shutil.copy2(find_python_license(), DEST / "LICENSE-Python.txt")
    pending = ["dpdfnet-mic-bridge"]
    visited = set()
    inventory = []
    while pending:
        name = canonicalize_name(pending.pop())
        if name in visited:
            continue
        visited.add(name)
        dist = metadata.distribution(name)
        directory = DEST / name
        directory.mkdir(exist_ok=True)
        texts = []
        for file in dist.files or []:
            if any(
                word in file.name.lower()
                for word in ("license", "licence", "copying", "notice")
            ):
                source = Path(dist.locate_file(file))
                if source.is_file():
                    target = directory / str(file).replace("/", "_").replace("\\", "_")
                    shutil.copy2(source, target)
                    texts.append(target.name)
        if name == "flatbuffers" and not texts:
            if dist.version != "25.12.19":
                raise RuntimeError(
                    "Review the FlatBuffers source license for this version"
                )
            url = (
                "https://raw.githubusercontent.com/google/flatbuffers/v25.12.19/LICENSE"
            )
            with urlopen(url, timeout=60) as response:
                text = response.read()
            if (
                hashlib.sha256(text).hexdigest()
                != "cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30"
            ):
                raise RuntimeError("FlatBuffers license checksum mismatch")
            (directory / "LICENSE.txt").write_bytes(text)
            texts.append("LICENSE.txt")
        # Qt wheels omit license texts. The checked, source-derived Qt notices
        # directory is staged separately by stage_qt_notices.py.
        if not texts and name not in {
            "dpdfnet-mic-bridge",
            "pyside6-essentials",
            "shiboken6",
        }:
            raise RuntimeError(f"No license text found for required package: {name}")
        (directory / "METADATA.txt").write_text(
            dist.read_text("METADATA") or "", encoding="utf-8"
        )
        inventory.append(
            {"name": name, "version": dist.version, "license_files": texts}
        )
        for raw in dist.requires or []:
            requirement = Requirement(raw)
            if requirement.marker is None or requirement.marker.evaluate({"extra": ""}):
                pending.append(requirement.name)
    qt_notices = ROOT / "build/qt-notices"
    if not qt_notices.is_dir():
        raise RuntimeError(
            "Run packaging/stage_qt_notices.py before collecting notices"
        )
    shutil.copytree(qt_notices, DEST / "qt", dirs_exist_ok=True)
    # Reuse the runtime package's Apache license for the separately licensed
    # weights, together with the pinned model card declaring their license.
    model_license = next((DEST / "dpdfnet").glob("*LICENSE*"))
    shutil.copy2(model_license, DEST / "LICENSE-model-Apache-2.0.txt")
    card = ROOT / "src/dpdfnet_mic_bridge/assets/models/MODEL_CARD.md"
    shutil.copy2(card, DEST / "MODEL_CARD.md")
    cargo = json.loads(
        subprocess.check_output(
            [
                "cargo",
                "metadata",
                "--locked",
                "--format-version",
                "1",
                "--manifest-path",
                str(ROOT / "rust/native_audio/Cargo.toml"),
            ],
            text=True,
        )
    )
    for package in cargo["packages"]:
        directory = Path(package["manifest_path"]).parent
        if directory == ROOT / "rust/native_audio":
            continue
        target = DEST / "rust" / f"{package['name']}-{package['version']}"
        target.mkdir(parents=True, exist_ok=True)
        licenses = [
            path
            for path in directory.iterdir()
            if path.is_file()
            and path.name.lower().startswith(
                ("license", "licence", "copying", "notice")
            )
        ]
        if not licenses:
            raise RuntimeError(f"Missing Rust dependency license: {package['name']}")
        for path in licenses:
            shutil.copy2(path, target / path.name)
    if sys.platform == "win32":
        native_license = (
            Path(os.environ["VCPKG_ROOT"])
            / "installed/x64-windows/share/portaudio/copyright"
        )
        shutil.copy2(native_license, DEST / "LICENSE-PortAudio.txt")
    if sys.platform == "linux":
        native = json.loads((ROOT / "build/pulse/provenance.json").read_text())
        append_native_notices(native, DEST)
    (DEST / "python-packages.json").write_text(
        json.dumps(inventory, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Collected notices for {len(inventory)} runtime packages")


if __name__ == "__main__":
    main()
