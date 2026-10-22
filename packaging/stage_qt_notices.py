"""Retain matching Qt sources and extract license/attribution texts safely."""

from __future__ import annotations

import hashlib
import argparse
import json
from pathlib import Path, PurePosixPath
import shutil
import re
import tarfile
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]


def is_notice(path: PurePosixPath) -> bool:
    name = path.name.lower()
    if (
        path.suffix.lower() in {".py", ".js", ".ts", ".sh"}
        or ".bin" in path.parts
        or name == "license-checker"
    ):
        return False
    prefixes = ("license", "licence", "copying", "notice", "copyright")
    return (
        name.startswith(prefixes)
        or name in {"qt_attribution.json", "qt_attributions.json"}
        or "LICENSES" in path.parts
    )


def compact_notice_name(archive: str, member: str) -> str:
    identity = hashlib.sha256(f"{archive}:{member}".encode()).hexdigest()[:24]
    basename = re.sub(r"[^A-Za-z0-9._-]", "_", PurePosixPath(member).name)[:48]
    return f"{identity}-{basename}"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--notices-dir", type=Path, default=ROOT / "build/qt-notices")
    args = parser.parse_args()
    sources = ROOT / "build/third-party-sources"
    notices = args.notices_dir.resolve()
    sources.mkdir(parents=True, exist_ok=True)
    if notices.exists() and any(notices.iterdir()):
        raise RuntimeError(
            "Use a fresh Qt notices directory; --notices-dir can select another location"
        )
    notices.mkdir(parents=True, exist_ok=True)
    index = {}
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
                if member.isfile() and member.size < 5_000_000 and is_notice(path):
                    if path.is_absolute() or ".." in path.parts:
                        raise RuntimeError("Unsafe source archive notice path")
                    output = notices / compact_notice_name(target.name, member.name)
                    if output.name in index:
                        raise RuntimeError(f"Duplicate notice identity: {member.name}")
                    with archive.extractfile(member) as stream:
                        content = stream.read()
                    output.write_bytes(content)
                    index[output.name] = {
                        "archive": target.name,
                        "source_path": member.name,
                        "sha256": hashlib.sha256(content).hexdigest(),
                    }
                    count += 1
        if count == 0:
            raise RuntimeError(f"No license notices in {target}")
        print(f"Collected {count} notice files from {target.name}", flush=True)
    shutil.copy2(manifest, sources / manifest.name)
    shutil.copy2(manifest, notices / manifest.name)
    (notices / "index.json").write_text(
        json.dumps(index, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
