"""Exercise real model inference in a frozen executable, with a hang deadline."""

from __future__ import annotations

import argparse
from pathlib import Path
import subprocess


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("executable", type=Path)
    args = parser.parse_args()
    try:
        result = subprocess.run(
            [str(args.executable.resolve()), "_desktop-model-test"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=90,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"Frozen model initialization or inference hung for 90 seconds: "
            f"{args.executable}\nOutput before timeout: {exc.stdout!r}\n"
            f"Errors before timeout: {exc.stderr!r}"
        ) from exc
    print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="")
    if result.returncode != 0:
        raise RuntimeError(f"Frozen model smoke test failed: exit {result.returncode}")
    if "Bundled model inference passed." not in result.stdout:
        raise RuntimeError("Frozen model smoke test did not execute inference")


if __name__ == "__main__":
    main()
