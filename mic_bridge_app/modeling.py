"""
Model and sample-rate policy.

Version 0 intentionally avoids live resampling. This module is the single place
where that rule is enforced so future edits do not scatter the policy.
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


def resolve_stream_sample_rate(
    model_name: str,
    samplerate: int | None,
    *,
    context: str,
) -> int:
    model_sr = model_native_sample_rate(model_name)
    sample_rate = model_sr if samplerate is None else int(samplerate)

    if sample_rate != model_sr:
        raise ValueError(
            f"Version 0 does not do live resampling.\n"
            f"Model '{model_name}' expects {model_sr} Hz, but --samplerate={sample_rate} was requested for {context}.\n"
            f"Use --samplerate {model_sr} or omit --samplerate."
        )

    return sample_rate
