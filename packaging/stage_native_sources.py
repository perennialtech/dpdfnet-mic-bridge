"""Download source packages matching Ubuntu's redistributed client libraries."""

from pathlib import Path
import subprocess

root = Path(__file__).resolve().parents[1]
destination = root / "build/third-party-sources"
destination.mkdir(parents=True, exist_ok=True)
versions = (root / "build/notices/native-package-versions.txt").read_text().splitlines()
packages = {tuple(line.split()[2:4]) for line in versions if line.strip()}
for source, version in sorted(packages):
    subprocess.run(
        ["apt-get", "source", "--download-only", f"{source}={version}"],
        cwd=destination,
        check=True,
    )
