import hashlib
import importlib.util
from io import BytesIO
import json
from pathlib import Path, PurePosixPath
from urllib.error import URLError
from urllib.parse import parse_qs, urlsplit

import pytest


def helper(name):
    path = Path(__file__).resolve().parents[1] / "packaging" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name + "_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def mock_published_descriptor(module, monkeypatch, descriptor, status="Published"):
    publication = {
        "distro_series_link": "https://api.launchpad.net/1.0/ubuntu/jammy",
        "source_package_name": "example",
        "source_package_version": "1:2.0-1",
        "self_link": module.ARCHIVE + "/+sourcepub/123",
    }
    descriptor_url = "https://launchpad.net/ubuntu/+archive/primary/+files/example.dsc"
    archive_url = (
        "https://launchpad.net/ubuntu/+archive/primary/+files/example.orig.tar.xz"
    )
    statuses = []
    downloaded = []

    def read_json(url):
        if url.startswith(module.ARCHIVE + "?"):
            query = parse_qs(urlsplit(url).query)
            statuses.append(query["status"][0])
            assert query["source_name"] == ["example"]
            assert query["exact_match"] == ["true"]
            return {"entries": [publication] if query["status"] == [status] else []}
        assert url == publication["self_link"] + "?ws.op=sourceFileUrls"
        return [descriptor_url, archive_url]

    def request(url):
        downloaded.append(url)
        assert url == descriptor_url  # Never re-download the large archive.
        return BytesIO(descriptor.encode())

    monkeypatch.setattr(module, "read_json", read_json)
    monkeypatch.setattr(module, "request", request)
    return statuses, downloaded


def source_descriptor(data):
    return (
        "Source: example\nVersion: 1:2.0-1\nChecksums-Sha256:\n"
        f" {hashlib.sha256(data).hexdigest()} {len(data)} example.orig.tar.xz\n"
    )


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


def test_cached_ubuntu_sources_are_verified(tmp_path, monkeypatch):
    module = helper("stage_native_sources")
    data = b"source"
    descriptor = source_descriptor(data)
    statuses, downloaded = mock_published_descriptor(module, monkeypatch, descriptor)
    (tmp_path / "example.dsc").write_text(descriptor)
    archive = tmp_path / "example.orig.tar.xz"
    archive.write_bytes(data)
    record = tmp_path / "example-1_2.0-1-source.json"
    record.write_text(
        json.dumps(
            {
                "publication": module.ARCHIVE + "/+sourcepub/123",
                "source": "example",
                "version": "1:2.0-1",
                "descriptor_sha256": hashlib.sha256(descriptor.encode()).hexdigest(),
                "files": module.parse_descriptor(descriptor, "example", "1:2.0-1"),
            }
        )
    )
    module.verify_cached_sources({("example", "1:2.0-1")}, tmp_path)
    assert statuses == ["Published"]
    assert downloaded == [
        "https://launchpad.net/ubuntu/+archive/primary/+files/example.dsc"
    ]
    record.write_text(record.read_text().replace("+sourcepub/123", "+sourcepub/999"))
    with pytest.raises(RuntimeError, match="provenance mismatch"):
        module.verify_cached_sources({("example", "1:2.0-1")}, tmp_path)
    record.write_text(record.read_text().replace("+sourcepub/999", "+sourcepub/123"))
    extra = tmp_path / "EXTRA-UNVERIFIED"
    extra.write_text("not a corresponding source")
    with pytest.raises(RuntimeError, match="Unexpected cached source files"):
        module.verify_cached_sources({("example", "1:2.0-1")}, tmp_path)
    extra.unlink()
    with pytest.raises(RuntimeError, match="No exact Ubuntu 22.04 source publication"):
        module.verify_cached_sources({("example", "other-version")}, tmp_path)
    archive.write_bytes(b"broken")
    with pytest.raises(RuntimeError, match="checksum mismatch"):
        module.verify_cached_sources({("example", "1:2.0-1")}, tmp_path)
    archive.unlink()
    with pytest.raises(RuntimeError, match="Missing cached source file"):
        module.verify_cached_sources({("example", "1:2.0-1")}, tmp_path)


def test_forged_descriptor_archive_and_provenance_are_rejected(tmp_path, monkeypatch):
    module = helper("stage_native_sources")
    authentic = source_descriptor(b"source")
    mock_published_descriptor(module, monkeypatch, authentic)
    forged = source_descriptor(b"forged")
    (tmp_path / "example.dsc").write_text(forged)
    (tmp_path / "example.orig.tar.xz").write_bytes(b"forged")
    (tmp_path / "example-1_2.0-1-source.json").write_text(
        json.dumps(
            {
                "publication": module.ARCHIVE + "/+sourcepub/123",
                "source": "example",
                "version": "1:2.0-1",
                "descriptor_sha256": hashlib.sha256(forged.encode()).hexdigest(),
                "files": module.parse_descriptor(forged, "example", "1:2.0-1"),
            }
        )
    )
    with pytest.raises(RuntimeError, match="descriptor differs from Ubuntu"):
        module.verify_cached_sources({("example", "1:2.0-1")}, tmp_path)


def test_superseded_jammy_cache_is_authenticated(tmp_path, monkeypatch):
    module = helper("stage_native_sources")
    descriptor = source_descriptor(b"source")
    statuses, downloaded = mock_published_descriptor(
        module, monkeypatch, descriptor, status="Superseded"
    )
    (tmp_path / "example.dsc").write_text(descriptor)
    (tmp_path / "example.orig.tar.xz").write_bytes(b"source")
    module.verify_cached_sources({("example", "1:2.0-1")}, tmp_path)
    assert statuses == ["Published", "Superseded"]
    assert len(downloaded) == 1


def test_cache_verification_fails_closed_without_publisher(tmp_path, monkeypatch):
    module = helper("stage_native_sources")
    descriptor = source_descriptor(b"source")
    mock_published_descriptor(module, monkeypatch, descriptor)
    (tmp_path / "example.dsc").write_text(descriptor)
    (tmp_path / "example.orig.tar.xz").write_bytes(b"source")

    def unavailable(url):
        raise URLError("publisher unavailable")

    monkeypatch.setattr(module, "request", unavailable)
    with pytest.raises(URLError, match="publisher unavailable"):
        module.verify_cached_sources({("example", "1:2.0-1")}, tmp_path)


def test_publisher_redirect_must_remain_https(monkeypatch):
    module = helper("stage_native_sources")

    class RedirectedResponse(BytesIO):
        def geturl(self):
            return "http://launchpadlibrarian.net/example.dsc"

    response = RedirectedResponse(b"untrusted")
    monkeypatch.setattr(module, "urlopen", lambda request, timeout: response)
    with pytest.raises(RuntimeError, match="outside HTTPS"):
        module.request("https://launchpad.net/example.dsc")
    assert response.closed
