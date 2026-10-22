"""Stage PulseAudio clients and their ELF dependency closure, excluding glibc."""

from __future__ import annotations

import json
from pathlib import Path
import re
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / "build/pulse"
GLIBC = re.compile(
    r"^(ld-linux.*|lib(c|m|pthread|dl|rt|resolv|util|anl)\.so\..*|linux-vdso.*)$"
)


def dependencies(path: Path) -> list[Path]:
    result = subprocess.run(
        ["ldd", str(path)], text=True, capture_output=True, check=True
    )
    if "not found" in result.stdout:
        raise RuntimeError(f"Unresolved native dependency for {path}:\n{result.stdout}")
    found = []
    for line in result.stdout.splitlines():
        match = re.search(r"=> (/\S+)", line)
        if match and not GLIBC.fullmatch(Path(match[1]).name):
            found.append(Path(match[1]))
    return found


def main():
    (DEST / "bin").mkdir(parents=True, exist_ok=True)
    (DEST / "lib").mkdir(exist_ok=True)
    pending = []
    for name in ("pactl", "parec", "pacat"):
        source = shutil.which(name)
        if source is None:
            raise RuntimeError(f"Missing required build client: {name}")
        pending.append(Path(source))
        shutil.copy2(source, DEST / "bin" / name)
    visited = set()
    provenance = []
    while pending:
        path = pending.pop()
        if str(path) in visited:
            continue
        visited.add(str(path))
        provenance.append(str(path))
        for dependency in dependencies(path):
            shutil.copy2(dependency, DEST / "lib" / dependency.name)
            pending.append(dependency)
    (DEST / "provenance.json").write_text(
        json.dumps(sorted(provenance), indent=2) + "\n"
    )
    print(f"Staged {len(visited)} audio client binaries/libraries")


if __name__ == "__main__":
    main()
