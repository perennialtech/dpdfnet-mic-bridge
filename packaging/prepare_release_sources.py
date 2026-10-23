"""Keep shared source archives once; archive small notices and Rust sources."""

import hashlib
from pathlib import Path
import shutil
import tarfile

root = Path("source-materials")
release = Path("release")
with tarfile.open(
    release / "third-party-notices-and-rust-sources.tar.gz", "w:gz"
) as archive:
    for directory in sorted(root.iterdir()):
        notices = directory / "notices"
        if notices.is_dir():
            archive.add(notices, arcname=directory.name + "/notices")
    rust_added = False
    for directory in sorted(root.rglob("third-party-sources")):
        for source in sorted(directory.iterdir()):
            if source.is_dir():
                if source.name == "rust-crates" and not rust_added:
                    archive.add(source, arcname="rust-crates")
                    rust_added = True
                continue
            if source.stat().st_size >= 2_000_000_000:
                raise RuntimeError(
                    f"Source asset exceeds GitHub's asset size limit: {source}"
                )
            target = release / source.name
            if target.exists():
                with source.open("rb") as left, target.open("rb") as right:
                    if (
                        hashlib.file_digest(left, "sha256").digest()
                        != hashlib.file_digest(right, "sha256").digest()
                    ):
                        # Platform-specific source manifests can have the same
                        # filename but different contents. Retain both.
                        target = release / f"{directory.name}-{source.name}"
                        if target.exists():
                            raise RuntimeError(f"Conflicting source asset: {target}")
                        shutil.copy2(source, target)
            else:
                shutil.copy2(source, target)
