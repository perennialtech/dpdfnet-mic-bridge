"""Bundle corresponding third-party sources without flooding GitHub Releases.

Input: source-materials/sources-and-notices-{Linux,Windows}/ (CI artifacts).
Output: three archives in release/; no modification of application binaries.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
import shutil
import subprocess
import tarfile

SOURCE_ROOT = Path("source-materials")
RELEASE = Path("release")
MAX_ASSET_SIZE = 2_000_000_000


def add_zstd_archive(destination: Path, entries: list[tuple[Path, str]]) -> None:
    """Stream tar into fast zstd compression, avoiding a second huge disk copy."""
    zstd = shutil.which("zstd")
    if zstd is None:
        raise RuntimeError("zstd is required to bundle corresponding sources")
    process = subprocess.Popen(
        [zstd, "-T2", "-1", "-q", "-o", str(destination)],
        stdin=subprocess.PIPE,
    )
    try:
        assert process.stdin is not None
        with tarfile.open(fileobj=process.stdin, mode="w|") as archive:
            for path, name in entries:
                archive.add(path, arcname=name, recursive=True)
    finally:
        assert process.stdin is not None
        process.stdin.close()
    if process.wait() != 0:
        raise RuntimeError(f"zstd failed to create {destination}")
    check_size(destination)


def check_size(path: Path) -> None:
    if not path.is_file() or not 0 < path.stat().st_size < MAX_ASSET_SIZE:
        raise RuntimeError(f"Invalid or oversized GitHub release asset: {path}")


def same_file(left: Path, right: Path) -> bool:
    if left.stat().st_size != right.stat().st_size:
        return False

    def digest(path: Path) -> bytes:
        with path.open("rb") as f:
            return hashlib.file_digest(f, "sha256").digest()

    return digest(left) == digest(right)


def main() -> None:
    RELEASE.mkdir(parents=True, exist_ok=True)
    platforms = {
        platform: SOURCE_ROOT / f"sources-and-notices-{platform}"
        for platform in ("Linux", "Windows")
    }
    if any(not p.is_dir() for p in platforms.values()):
        raise RuntimeError(f"Expected Linux and Windows source materials: {platforms}")

    qt: dict[str, Path] = {}
    qt_metadata: list[tuple[Path, str]] = []
    ubuntu: list[tuple[Path, str]] = []
    notices: list[tuple[Path, str]] = []
    rust_sources: Path | None = None
    for platform, base in platforms.items():
        notice_dir = base / "notices"
        if not notice_dir.is_dir():
            raise RuntimeError(f"Missing license notices: {notice_dir}")
        notices.append((notice_dir, f"{platform}/notices"))
        source_dir = base / "third-party-sources"
        if not source_dir.is_dir():
            raise RuntimeError(f"Missing corresponding sources: {source_dir}")
        for source in sorted(source_dir.iterdir()):
            if source.name == "rust-crates" and source.is_dir():
                if rust_sources is None:
                    rust_sources = source
            elif source.name == "qt-sources.json":
                qt_metadata.append((source, f"qt/manifests/{platform}-qt-sources.json"))
            elif source.name.startswith(
                ("qt-everywhere-src-", "pyside-setup-everywhere-src-")
            ):
                existing = qt.setdefault(source.name, source)
                if existing != source and not same_file(existing, source):
                    raise RuntimeError(f"Conflicting Qt source archive: {source.name}")
            elif platform == "Linux" and source.name == "ubuntu" and source.is_dir():
                ubuntu.extend(
                    (path, f"ubuntu/{path.name}") for path in sorted(source.iterdir())
                )
            elif platform == "Linux":
                ubuntu.append((source, f"ubuntu/{source.name}"))
            else:
                notices.append((source, f"Windows/other-sources/{source.name}"))
    if rust_sources is None:
        raise RuntimeError("Missing Rust crate source bundle")
    notices.append((rust_sources, "rust-crates"))
    if (
        not qt_metadata
        or not any(n.startswith("qt-everywhere-src-") for n in qt)
        or not any(n.startswith("pyside-setup-everywhere-src-") for n in qt)
    ):
        raise RuntimeError("Qt corresponding sources or manifests are incomplete")
    if not ubuntu:
        raise RuntimeError("Ubuntu corresponding source materials are missing")

    add_zstd_archive(
        RELEASE / "qt-corresponding-sources.tar.zst",
        [(p, f"qt/{n}") for n, p in sorted(qt.items())] + qt_metadata,
    )
    add_zstd_archive(RELEASE / "ubuntu-corresponding-sources.tar.zst", ubuntu)
    notice_bundle = RELEASE / "third-party-notices-and-rust-sources.tar.gz"
    with tarfile.open(notice_bundle, "w:gz", compresslevel=1) as archive:
        for source, name in notices:
            archive.add(source, arcname=name)
    check_size(notice_bundle)


if __name__ == "__main__":
    main()
