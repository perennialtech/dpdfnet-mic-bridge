"""DPDFNet stream-enhancer construction."""

from __future__ import annotations


def _first_line(text: str | None) -> str | None:
    if text is None:
        return None

    stripped = text.strip()
    if not stripped:
        return None

    line = stripped.splitlines()[0]
    if len(line) > 180:
        return line[:177] + "..."
    return line


def build_stream_enhancer(dpdfnet_mod, *, model_name: str, verbose: bool):
    try:
        return dpdfnet_mod.StreamEnhancer(model=model_name, verbose=verbose)
    except Exception as exc:
        detail = _first_line(str(exc)) or type(exc).__name__
        raise RuntimeError(
            f"Could not load DPDFNet model '{model_name}'.\n"
            "If the model has not been downloaded yet, run:\n"
            f"  dpdfnet download {model_name}\n"
            f"Details: {detail}"
        ) from exc
