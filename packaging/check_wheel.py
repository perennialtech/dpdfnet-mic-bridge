"""Check that the bridge wheel contains its application and audio extension."""

from pathlib import Path
import sys
from zipfile import ZipFile


def main():
    wheels = list(Path(sys.argv[1] if len(sys.argv) > 1 else "dist").glob("*.whl"))
    if len(wheels) != 1:
        raise RuntimeError("Expected exactly one release wheel")
    with ZipFile(wheels[0]) as wheel:
        names = wheel.namelist()
        if "dpdfnet_mic_bridge/__main__.py" not in names or not any(
            name.startswith("dpdfnet_mic_bridge/_native_audio.")
            and name.endswith((".so", ".pyd"))
            for name in names
        ):
            raise RuntimeError(
                "Bridge wheel is missing the application or audio extension"
            )
    print(f"Wheel contents verified: {wheels[0].name}")


if __name__ == "__main__":
    main()
