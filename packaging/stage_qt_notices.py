"""Retain matching Qt sources and extract license/attribution texts safely."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import tarfile
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]


def main():
    sources = ROOT / "build/third-party-sources"
    notices = ROOT / "build/qt-notices"
    sources.mkdir(parents=True, exist_ok=True)
    notices.mkdir(parents=True, exist_ok=True)
    manifest = ROOT / "packaging/qt-sources.json"
    for spec in json.loads(manifest.read_text()):
        target = sources / spec["url"].rsplit("/", 1)[1]
        if not target.exists():
            temporary = target.with_suffix(target.suffix + ".download")
            try:
                print(f"Downloading {target.name}", flush=True)
                with (
                    urlopen(spec["url"], timeout=120) as response,
                    temporary.open("wb") as output,
                ):
                    shutil.copyfileobj(response, output)
                temporary.replace(target)
            finally:
                temporary.unlink(missing_ok=True)
        with target.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if digest != spec["sha256"]:
            raise RuntimeError(f"Source checksum mismatch: {target}")
        count = 0
        # Never extract executable code or archive-controlled links. Only copy
        # bounded regular notice files underneath our owned output directory.
        with tarfile.open(target, "r|xz") as archive:
            for member in archive:
                path = PurePosixPath(member.name)
                name = path.name.lower()
                if (
                    member.isfile()
                    and member.size < 5_000_000
                    and (
                        name.startswith(
                            ("license", "licence", "copying", "notice", "copyright")
                        )
                        or name == "qt_attribution.json"
                        or name == "qt_attributions.json"
                        or "LICENSES" in path.parts
                    )
                ):
                    if path.is_absolute() or ".." in path.parts:
                        raise RuntimeError("Unsafe source archive notice path")
                    output = notices.joinpath(*path.parts)
                    output.parent.mkdir(parents=True, exist_ok=True)
                    with (
                        archive.extractfile(member) as stream,
                        output.open("wb") as destination,
                    ):
                        shutil.copyfileobj(stream, destination)
                    count += 1
        if count == 0:
            raise RuntimeError(f"No license notices in {target}")
        print(f"Collected {count} notice files from {target.name}", flush=True)
    shutil.copy2(manifest, sources / manifest.name)
    shutil.copy2(manifest, notices / manifest.name)


if __name__ == "__main__":
    main()
