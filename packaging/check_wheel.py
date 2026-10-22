"""Check that the installed release wheel contains the pinned model assets."""

import hashlib
import json
from pathlib import Path
import sys
from zipfile import ZipFile


def main():
    wheels = list(Path(sys.argv[1] if len(sys.argv) > 1 else "dist").glob("*.whl"))
    if len(wheels) != 1:
        raise RuntimeError("Expected exactly one release wheel")
    with ZipFile(wheels[0]) as wheel:
        prefix = "dpdfnet_mic_bridge/assets/models/"
        manifest = json.loads(wheel.read(prefix + "default.json"))
        weights = wheel.read(prefix + manifest["name"] + "/" + manifest["filename"])
        if (
            len(weights) != manifest["size"]
            or hashlib.sha256(weights).hexdigest() != manifest["sha256"]
        ):
            raise RuntimeError("Wheel model integrity check failed")
        card = wheel.read(prefix + "MODEL_CARD.md")
        if hashlib.sha256(card).hexdigest() != manifest["card_sha256"]:
            raise RuntimeError("Wheel model license card check failed")
    print(f"Wheel assets verified: {wheels[0].name}")


if __name__ == "__main__":
    main()
