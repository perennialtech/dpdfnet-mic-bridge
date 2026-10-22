"""Resolve application-owned model files without upstream auto-downloads."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from .registry import DEFAULT_MODEL, model_native_sample_rate

ASSETS = Path(__file__).resolve().parents[1] / "assets"


def default_manifest() -> dict:
    return json.loads((ASSETS / "models" / "default.json").read_text(encoding="utf-8"))


def verify_model(path: Path, manifest: dict) -> None:
    if not path.is_file() or path.stat().st_size != manifest["size"]:
        raise RuntimeError(f"Model file is missing or has the wrong size: {path}")
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    if digest != manifest["sha256"]:
        raise RuntimeError(f"Model checksum does not match: {path}")


def resolve_local_model(model: str) -> Path:
    model_native_sample_rate(model)
    if model == DEFAULT_MODEL:
        manifest = default_manifest()
        path = ASSETS / "models" / model / manifest["filename"]
        try:
            verify_model(path, manifest)
        except RuntimeError as exc:
            raise RuntimeError(
                "The bundled default model is missing or damaged. Reinstall the application. "
                "Source builds must run `python packaging/stage_model.py` before installation."
            ) from exc
        return path

    # Optional models are an explicit advanced, local-only feature. They never
    # turn an offline launch into an implicit network request.
    directory = os.environ.get("DPDFNET_MODEL_DIR")
    if directory:
        path = Path(directory).expanduser() / f"{model}.onnx"
        if path.is_file() and path.stat().st_size:
            return path.resolve()
    raise RuntimeError(
        f"Optional model '{model}' is not installed. Use the bundled default model, "
        "or set DPDFNET_MODEL_DIR to a directory containing your ONNX file."
    )


def load_enhancer(model: str = DEFAULT_MODEL, *, verbose: bool = False):
    path = resolve_local_model(model)
    import dpdfnet

    return dpdfnet.StreamEnhancer(model=model, onnx_path=path, verbose=verbose)
