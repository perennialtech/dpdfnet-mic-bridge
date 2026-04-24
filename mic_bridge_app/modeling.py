"""
Model and sample-rate policy.

Version 0 intentionally avoids live resampling. The selected model determines
the stream rate; there is no sample-rate override flag.
"""

from __future__ import annotations

from .constants import MODEL_SAMPLE_RATES


def model_native_sample_rate(model_name: str) -> int:
    try:
        return MODEL_SAMPLE_RATES[model_name]
    except KeyError as exc:
        supported = ", ".join(sorted(MODEL_SAMPLE_RATES))
        raise ValueError(
            f"Unsupported model '{model_name}'. Supported models: {supported}"
        ) from exc
