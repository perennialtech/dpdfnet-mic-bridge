"""Write checksums for final downloadable release files."""

import hashlib
from pathlib import Path

root = Path("release")
lines = []
for path in sorted(root.iterdir()):
    if path.is_file() and path.name != "SHA256SUMS":
        with path.open("rb") as stream:
            lines.append(
                f"{hashlib.file_digest(stream, 'sha256').hexdigest()}  {path.name}\n"
            )
if not lines:
    raise RuntimeError("No release files to checksum")
(root / "SHA256SUMS").write_text("".join(lines), encoding="utf-8")
