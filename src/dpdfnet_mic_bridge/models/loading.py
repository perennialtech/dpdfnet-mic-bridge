"""Load the fork's bundled native DPDFNet models."""

from __future__ import annotations

from .registry import DEFAULT_MODEL, model_native_sample_rate


def load_enhancer(model: str = DEFAULT_MODEL, *, verbose: bool = False):
    model_native_sample_rate(model)
    from dpdfnet_native.stream import StreamEnhancer

    enhancer = StreamEnhancer(model=model)
    if verbose:
        print(
            f"Native DPDFNet model {model} runs at {enhancer.sample_rate} Hz "
            f"with {enhancer.hop_size} samples per hop."
        )
    return enhancer
