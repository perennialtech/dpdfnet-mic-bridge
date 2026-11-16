"""DPDFNet model registry and sample-rate policy."""

from __future__ import annotations

MODEL_SAMPLE_RATES: dict[str, int] = {
    "dpdfnet2_48khz_hr": 48000,
    "dpdfnet8_48khz_hr": 48000,
}

DEFAULT_MODEL = "dpdfnet8_48khz_hr"


def model_native_sample_rate(model_name: str) -> int:
    try:
        return MODEL_SAMPLE_RATES[model_name]
    except KeyError as exc:
        supported = ", ".join(sorted(MODEL_SAMPLE_RATES))
        raise ValueError(
            f"Unsupported model '{model_name}'. Supported models: {supported}"
        ) from exc
