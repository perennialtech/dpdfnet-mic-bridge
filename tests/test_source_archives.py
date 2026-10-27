"""The public release should have six attachments, not hundreds of source files."""

import hashlib
import io
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile

import pytest


@pytest.mark.skipif(shutil.which("zstd") is None, reason="zstd not available")
@pytest.mark.parametrize("qt_archives_on_windows", [True, False])
def test_source_archives_are_bundled_without_modifying_binaries(
    tmp_path: Path, qt_archives_on_windows: bool
) -> None:
    for platform in ("Linux", "Windows"):
        base = tmp_path / "source-materials" / f"sources-and-notices-{platform}"
        notice_dir = base / "notices"
        notice_dir.mkdir(parents=True)
        (notice_dir / "LICENSE.txt").write_text(platform)
        sources = base / "third-party-sources"
        (sources / "rust-crates").mkdir(parents=True)
        (sources / "rust-crates" / "LICENSE.txt").write_text("Rust")
        # CI may omit the duplicate Qt source archives from the Windows
        # artifact. Both layouts must produce exactly one Qt source bundle.
        if platform == "Linux" or qt_archives_on_windows:
            (sources / "qt-everywhere-src-6.12.0.tar.xz").write_bytes(b"Qt")
            (sources / "pyside-setup-everywhere-src-6.12.0.tar.xz").write_bytes(
                b"PySide"
            )
        (sources / "qt-sources.json").write_text(f"{platform} manifest")
        if platform == "Linux":
            (sources / "libalsa.dsc").write_text("Ubuntu source")
            (sources / "xz-utils-source.json").write_text("Ubuntu descriptor")
    release = tmp_path / "release"
    release.mkdir()
    binaries = [
        release / "dpdfnet-mic-bridge-0.2.0-linux-x86_64.AppImage",
        release / "dpdfnet-mic-bridge-0.2.0-windows-x64-setup.exe",
    ]
    for path in binaries:
        path.write_bytes(path.name.encode())
    initial_hashes = [hashlib.sha256(p.read_bytes()).digest() for p in binaries]
    root = Path(__file__).resolve().parents[1]
    subprocess.run(
        [sys.executable, str(root / "packaging/prepare_release_sources.py")],
        cwd=tmp_path,
        check=True,
    )
    subprocess.run(
        [sys.executable, str(root / "packaging/checksums.py")],
        cwd=tmp_path,
        check=True,
    )
    sums = {
        filename: digest
        for digest, filename in (
            line.split("  ", 1)
            for line in (release / "SHA256SUMS").read_text().splitlines()
        )
    }
    assert all(
        hashlib.sha256(path.read_bytes()).hexdigest() == sums[path.name]
        for path in release.iterdir()
        if path.name != "SHA256SUMS"
    )
    assert len(list(release.iterdir())) == 6
    assert initial_hashes == [hashlib.sha256(p.read_bytes()).digest() for p in binaries]

    def source_names(filename: str) -> list[str]:
        archive_bytes = subprocess.check_output(
            ["zstd", "-d", "-c", str(release / filename)]
        )
        with tarfile.open(fileobj=io.BytesIO(archive_bytes)) as archive:
            return archive.getnames()

    qt_names = source_names("qt-corresponding-sources.tar.zst")
    assert (
        len([name for name in qt_names if name.startswith("qt/qt-everywhere-src-")])
        == 1
    )
    assert "qt/manifests/Linux-qt-sources.json" in qt_names
    assert "qt/manifests/Windows-qt-sources.json" in qt_names
    ubuntu_names = source_names("ubuntu-corresponding-sources.tar.zst")
    assert "ubuntu/libalsa.dsc" in ubuntu_names
    assert "ubuntu/xz-utils-source.json" in ubuntu_names
