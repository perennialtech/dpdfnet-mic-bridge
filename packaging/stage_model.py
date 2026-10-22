"""Stage only the pinned default model; never copy a builder's user cache."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
MODELS = ROOT / "src/dpdfnet_mic_bridge/assets/models"


def verify(path: Path, manifest: dict) -> None:
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    if path.stat().st_size != manifest["size"] or digest != manifest["sha256"]:
        raise RuntimeError(f"Model checksum/size mismatch: {path}")


def main() -> None:
    manifest = json.loads((MODELS / "default.json").read_text(encoding="utf-8"))
    card = MODELS / "MODEL_CARD.md"
    with urlopen(manifest["card_url"], timeout=60) as response:
        data = response.read()
    if hashlib.sha256(data).hexdigest() != manifest["card_sha256"]:
        raise RuntimeError("Model license card checksum mismatch")
    card.write_bytes(data)
    directory = MODELS / manifest["name"]
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / manifest["filename"]
    if target.exists():
        verify(target, manifest)
        print(f"Verified {target}")
        return
    fd, name = tempfile.mkstemp(dir=directory, suffix=".download")
    temporary = Path(name)
    try:
        with (
            os.fdopen(fd, "wb") as output,
            urlopen(manifest["url"], timeout=120) as response,
        ):
            while chunk := response.read(1024 * 1024):
                output.write(chunk)
        verify(temporary, manifest)
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    print(f"Staged {target}")


if __name__ == "__main__":
    main()
