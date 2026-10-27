"""Remove superseded individual source files after replacement assets exist.

Must be run with GH_TOKEN in a trusted GitHub Actions workflow.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import json
from pathlib import Path
import time
import subprocess
import sys

EXPECTED = {
    "dpdfnet-mic-bridge-0.2.0-linux-x86_64.AppImage",
    "dpdfnet-mic-bridge-0.2.0-windows-x64-setup.exe",
    "qt-corresponding-sources.tar.zst",
    "ubuntu-corresponding-sources.tar.zst",
    "third-party-notices-and-rust-sources.tar.gz",
    "SHA256SUMS",
}


def gh(*args: str) -> str:
    return subprocess.check_output(["gh", *args], text=True)


def assets(repo: str, release_id: str) -> list[dict]:
    output = gh(
        "api",
        "--paginate",
        f"repos/{repo}/releases/{release_id}/assets?per_page=100",
        "--jq",
        ".[]|@json",
    )
    return [json.loads(row) for row in output.splitlines() if row.strip()]


def main(tag: str, repo: str) -> None:
    if not tag.startswith("v"):
        raise RuntimeError(f"Refusing non-version tag {tag}")
    version = tag[1:]
    expected = {name.replace("0.2.0", version) for name in EXPECTED}
    release_files = Path("release")
    uploaded = assets(
        repo, gh("api", f"repos/{repo}/releases/tags/{tag}", "--jq", ".id").strip()
    )
    names = [item["name"] for item in uploaded]
    if any(names.count(name) != 1 for name in expected):
        raise RuntimeError(
            f"Missing or duplicate required release assets: {expected - set(names)}"
        )

    checksum_lines = (release_files / "SHA256SUMS").read_text().splitlines()
    listed = {line.split("  ", 1)[1] for line in checksum_lines}
    if listed != expected - {"SHA256SUMS"} or len(checksum_lines) != 5:
        raise RuntimeError(
            f"Checksum manifest does not describe exactly five attachments: {listed}"
        )
    for item in uploaded:
        if item["name"] in expected:
            file = release_files / item["name"]
            if not file.exists() or item["size"] != file.stat().st_size:
                raise RuntimeError(
                    f"Required uploaded asset differs from validated local file: {file}"
                )
            digest = item.get("digest")
            if digest and digest.startswith("sha256:"):
                import hashlib

                with file.open("rb") as stream:
                    actual = hashlib.file_digest(stream, "sha256").hexdigest()
                if digest != f"sha256:{actual}":
                    raise RuntimeError(f"Remote asset digest does not match: {file}")

    extra = [item for item in uploaded if item["name"] not in expected]
    print(
        f"Verified all six replacement assets. Removing {len(extra)} legacy source files.",
        flush=True,
    )

    def remove(item: dict) -> None:
        for attempt in range(5):
            result = subprocess.run(
                [
                    "gh",
                    "api",
                    "-X",
                    "DELETE",
                    f"repos/{repo}/releases/assets/{item['id']}",
                ],
                capture_output=True,
                text=True,
            )
            # Stay well below GitHub's secondary REST API rate limit.
            time.sleep(0.8)
            if result.returncode == 0 or "HTTP 404" in result.stderr:
                return
            if attempt == 4:
                raise RuntimeError(f"Cannot delete {item['name']}: {result.stderr}")
            time.sleep(2 ** (attempt + 2))

    with ThreadPoolExecutor(max_workers=3) as pool:
        pending = [pool.submit(remove, item) for item in extra]
        for future in as_completed(pending):
            future.result()
    final = assets(
        repo, gh("api", f"repos/{repo}/releases/tags/{tag}", "--jq", ".id").strip()
    )
    final_names = {item["name"] for item in final}
    if len(final) != len(expected) or final_names != expected:
        raise RuntimeError(
            f"Unexpected release assets after cleanup: {final_names ^ expected}"
        )
    print(f"Release {tag}: precisely {len(final)} uploaded assets, all verified.")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("usage: prune_release_assets.py TAG OWNER/REPO")
    main(sys.argv[1], sys.argv[2])
