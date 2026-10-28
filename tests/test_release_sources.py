import hashlib
import importlib.util
from pathlib import Path, PurePosixPath

import pytest


def helper(name):
    path = Path(__file__).resolve().parents[1] / "packaging" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name + "_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_qt_notice_names_are_short_and_traceable():
    module = helper("stage_qt_notices")
    path = "qt/source/" + "deeply-nested/" * 40 + "LICENSE"
    name = module.compact_notice_name("qt.tar.xz", path)
    assert len(name) < 80
    assert name.endswith("-LICENSE")
    assert module.compact_notice_name("qt.tar.xz", path + ".txt") != name
    assert module.is_notice(PurePosixPath(path))
    assert not module.is_notice(PurePosixPath("node_modules/.bin/license-checker"))
    assert not module.is_notice(PurePosixPath("tools/license.py"))


def test_archived_source_descriptor_matches_exact_version(tmp_path):
    module = helper("stage_native_sources")
    digest = hashlib.sha256(b"source").hexdigest()
    descriptor = f"Source: example\nVersion: 1:2.0-1\nChecksums-Sha256:\n {digest} 6 example.tar.xz\nFiles:\n unused\n"
    files = module.parse_descriptor(descriptor, "example", "1:2.0-1")
    target = tmp_path / "example.tar.xz"
    target.write_bytes(b"source")
    module.verify_file(target, files[target.name])
    target.write_bytes(b"edited")
    with pytest.raises(RuntimeError, match="checksum mismatch"):
        module.verify_file(target, files[target.name])
    with pytest.raises(RuntimeError, match="expected Version"):
        module.parse_descriptor(descriptor, "example", "different")
    with pytest.raises(RuntimeError, match="Invalid source descriptor"):
        module.parse_descriptor(
            descriptor.replace("example.tar.xz", "../escape.tar.xz"),
            "example",
            "1:2.0-1",
        )


def test_cached_ubuntu_sources_are_verified(tmp_path):
    module = helper("stage_native_sources")
    data = b"source"
    digest = hashlib.sha256(data).hexdigest()
    descriptor = (
        "Source: example\nVersion: 1:2.0-1\nChecksums-Sha256:\n"
        f" {digest} {len(data)} example.orig.tar.xz\n"
    )
    (tmp_path / "example.dsc").write_text(descriptor)
    archive = tmp_path / "example.orig.tar.xz"
    archive.write_bytes(data)
    module.verify_cached_sources({("example", "1:2.0-1")}, tmp_path)
    with pytest.raises(RuntimeError, match="Expected one cached descriptor"):
        module.verify_cached_sources({("example", "other-version")}, tmp_path)
    archive.write_bytes(b"broken")
    with pytest.raises(RuntimeError, match="checksum mismatch"):
        module.verify_cached_sources({("example", "1:2.0-1")}, tmp_path)
    archive.unlink()
    with pytest.raises(RuntimeError, match="Missing cached source file"):
        module.verify_cached_sources({("example", "1:2.0-1")}, tmp_path)
