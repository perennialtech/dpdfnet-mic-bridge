"""Retain exact Ubuntu source versions, including superseded Launchpad uploads."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
from urllib.parse import unquote, urlencode, urlsplit
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE = "https://api.launchpad.net/1.0/ubuntu/+archive/primary"


def request(url: str):
    return urlopen(
        Request(url, headers={"User-Agent": "dpdfnet-mic-bridge-release-builder"}),
        timeout=120,
    )


def read_json(url: str):
    with request(url) as response:
        return json.load(response)


def parse_descriptor(text: str, source: str, version: str) -> dict:
    for field, expected in (("Source", source), ("Version", version)):
        match = re.search(rf"(?m)^{field}: (.+)$", text)
        if match is None or match.group(1).strip() != expected:
            raise RuntimeError(
                f"Source descriptor does not match expected {field}: {expected}"
            )
    block = re.search(r"(?m)^Checksums-Sha256:\r?\n((?:[ \t].*\r?\n)+)", text)
    if block is None:
        raise RuntimeError("Source descriptor has no SHA-256 file checksums")
    files = {}
    for line in block.group(1).splitlines():
        digest, size, name = line.split()
        if (
            "/" in name
            or "\\" in name
            or name in {".", ".."}
            or not re.fullmatch(r"[0-9a-f]{64}", digest)
            or not size.isdecimal()
        ):
            raise RuntimeError("Invalid source descriptor file entry")
        files[name] = {"sha256": digest, "size": int(size)}
    if not files:
        raise RuntimeError("Source descriptor lists no files")
    return files


def verify_file(path: Path, expected: dict) -> None:
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    if path.stat().st_size != expected["size"] or digest != expected["sha256"]:
        raise RuntimeError(f"Archived source checksum mismatch: {path.name}")


def download_launchpad_sources(source: str, version: str, destination: Path) -> None:
    publication = None
    for status in ("Published", "Superseded", "Deleted"):
        query = urlencode(
            {
                "ws.op": "getPublishedSources",
                "source_name": source,
                "version": version,
                "exact_match": "true",
                "status": status,
            }
        )
        entries = read_json(ARCHIVE + "?" + query).get("entries", [])
        publication = next(
            (
                item
                for item in entries
                if item["distro_series_link"]
                == "https://api.launchpad.net/1.0/ubuntu/jammy"
                and item["source_package_name"] == source
                and item["source_package_version"] == version
            ),
            None,
        )
        if publication is not None:
            break
    if publication is None:
        raise RuntimeError(
            f"No exact Ubuntu 22.04 source publication for {source}={version}"
        )
    if not publication["self_link"].startswith(ARCHIVE + "/+sourcepub/"):
        raise RuntimeError("Unexpected source publication URL")
    urls = read_json(publication["self_link"] + "?ws.op=sourceFileUrls")
    files = {}
    for url in urls:
        parsed = urlsplit(url)
        name = unquote(parsed.path.rsplit("/", 1)[-1])
        if (
            parsed.scheme != "https"
            or parsed.hostname != "launchpad.net"
            or "/" in name
            or "\\" in name
            or name in {".", ".."}
        ):
            raise RuntimeError("Unexpected archived source file URL")
        files[name] = url
    descriptors = [name for name in files if name.endswith(".dsc")]
    if len(descriptors) != 1:
        raise RuntimeError("Expected one archived source descriptor")
    descriptor = descriptors[0]
    with request(files[descriptor]) as response:
        data = response.read(5_000_001)
    if len(data) > 5_000_000:
        raise RuntimeError("Archived source descriptor is oversized")
    expected = parse_descriptor(data.decode("utf-8"), source, version)
    destination.mkdir(parents=True, exist_ok=True)
    (destination / descriptor).write_bytes(data)
    for name, integrity in expected.items():
        if name not in files:
            raise RuntimeError(f"Archive does not provide descriptor file: {name}")
        target = destination / name
        if not target.exists():
            temporary = target.with_name(target.name + ".download")
            try:
                with request(files[name]) as response, temporary.open("wb") as output:
                    shutil.copyfileobj(response, output)
                verify_file(temporary, integrity)
                temporary.replace(target)
            finally:
                temporary.unlink(missing_ok=True)
        verify_file(target, integrity)
    provenance = {
        "publication": publication["self_link"],
        "source": source,
        "version": version,
        "descriptor_sha256": hashlib.sha256(data).hexdigest(),
        "files": expected,
    }
    record = re.sub(r"[^A-Za-z0-9._-]", "_", f"{source}-{version}") + "-source.json"
    (destination / record).write_text(json.dumps(provenance, indent=2) + "\n")
    print(f"Retained exact archived sources for {source}={version}", flush=True)


def main():
    destination = ROOT / "build/third-party-sources"
    destination.mkdir(parents=True, exist_ok=True)
    versions = (
        (ROOT / "build/notices/native-package-versions.txt").read_text().splitlines()
    )
    packages = {tuple(line.split()[2:4]) for line in versions if line.strip()}
    for source, version in sorted(packages):
        result = subprocess.run(
            ["apt-get", "source", "--download-only", f"{source}={version}"],
            cwd=destination,
        )
        if result.returncode:
            download_launchpad_sources(source, version, destination)


if __name__ == "__main__":
    main()
