"""Stage the unmodified, checksum-pinned official VB-CABLE driver for Windows setup."""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
from urllib.request import urlopen
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
METADATA = ROOT / "packaging/vbcable.json"
OUTPUT = ROOT / "build/vbcable"
EXPECTED = {"VBCABLE_Setup_x64.exe", "vbMmeCable64_win10.inf", "readme.txt"}


def extract_verified_archive(archive: bytes, output: Path, metadata: dict) -> None:
    digest = hashlib.sha256(archive).hexdigest()
    if digest != metadata["sha256"]:
        raise ValueError(f"Unexpected VB-CABLE archive SHA-256: {digest}")

    with ZipFile(io.BytesIO(archive)) as zf:
        members = zf.infolist()
        names = {info.filename for info in members}
        if not EXPECTED.issubset(names) or len(names) != len(members):
            raise ValueError("Unexpected VB-CABLE package contents")
        if any(
            info.is_dir()
            or info.filename in {".", ".."}
            or "/" in info.filename
            or "\\" in info.filename
            for info in members
        ):
            raise ValueError("Unsafe VB-CABLE package member")
        setup = zf.read("VBCABLE_Setup_x64.exe")
        if hashlib.sha256(setup).hexdigest() != metadata["setup_x64_sha256"]:
            raise ValueError("Unexpected VB-CABLE installer")
        output.mkdir(parents=True, exist_ok=True)
        for info in members:
            (output / info.filename).write_bytes(zf.read(info))


def main() -> None:
    metadata = json.loads(METADATA.read_text(encoding="utf-8"))
    with urlopen(metadata["url"], timeout=60) as response:
        archive = response.read()
    extract_verified_archive(archive, OUTPUT, metadata)


if __name__ == "__main__":
    main()
