from types import SimpleNamespace
import sys

import pytest

from dpdfnet_mic_bridge.models import loading
from dpdfnet_mic_bridge.models.registry import DEFAULT_MODEL, MODEL_SAMPLE_RATES


@pytest.mark.parametrize("model", MODEL_SAMPLE_RATES)
def test_load_uses_native_enhancer(monkeypatch, model):
    calls = []
    monkeypatch.setitem(
        sys.modules,
        "dpdfnet_native.stream",
        SimpleNamespace(StreamEnhancer=lambda **kwargs: calls.append(kwargs)),
    )
    loading.load_enhancer(model)
    assert calls == [{"model": model}]


def test_legacy_onnx_model_name_is_rejected():
    with pytest.raises(ValueError, match="Unsupported model 'dpdfnet2'"):
        loading.load_enhancer("dpdfnet2")


def test_desktop_model_smoke_invokes_inference(monkeypatch, capsys):
    import numpy as np

    from dpdfnet_mic_bridge import desktop_process

    seen = []

    class Enhancer:
        def __init__(self, model):
            self.model = model

        def process(self, samples, sample_rate):
            seen.append((self.model, samples.shape, samples.dtype, sample_rate))
            return np.ones(480, dtype=np.float32)

        def close(self):
            seen.append((self.model, "closed"))

    monkeypatch.setattr(loading, "load_enhancer", Enhancer)
    assert desktop_process.internal_command(["_desktop-model-test"]) == 0
    assert seen == [
        ("dpdfnet2_48khz_hr", (4800,), np.dtype("float32"), 48000),
        ("dpdfnet2_48khz_hr", "closed"),
        (DEFAULT_MODEL, (4800,), np.dtype("float32"), 48000),
        (DEFAULT_MODEL, "closed"),
    ]
    assert "Bundled model inference passed." in capsys.readouterr().out


def test_desktop_model_smoke_rejects_invalid_output(monkeypatch):
    import numpy as np

    from dpdfnet_mic_bridge import desktop_process

    class Enhancer:
        def process(self, samples, sample_rate):
            return np.array([float("nan")], dtype=np.float32)

        def close(self):
            pass

    monkeypatch.setattr(loading, "load_enhancer", lambda _: Enhancer())
    with pytest.raises(RuntimeError, match="no finite audio samples"):
        desktop_process.internal_command(["_desktop-model-test"])
