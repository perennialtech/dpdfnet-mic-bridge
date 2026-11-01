import hashlib
from types import SimpleNamespace
import sys

import pytest

from dpdfnet_mic_bridge.models import loading
from dpdfnet_mic_bridge.models.registry import DEFAULT_MODEL


def test_model_verifies_size_and_digest(tmp_path):
    path = tmp_path / "model.onnx"
    path.write_bytes(b"model")
    manifest = {"size": 5, "sha256": hashlib.sha256(b"model").hexdigest()}
    loading.verify_model(path, manifest)
    path.write_bytes(b"other")
    with pytest.raises(RuntimeError, match="checksum"):
        loading.verify_model(path, manifest)
    path.unlink()
    with pytest.raises(RuntimeError, match="missing"):
        loading.verify_model(path, manifest)


def test_missing_default_is_a_packaging_error_not_a_download(monkeypatch, tmp_path):
    monkeypatch.setattr(loading, "ASSETS", tmp_path)
    monkeypatch.setattr(
        loading, "default_manifest", lambda: {"filename": "model.onnx", "size": 1}
    )
    with pytest.raises(RuntimeError, match="bundled default model"):
        loading.resolve_local_model(DEFAULT_MODEL)


def test_load_passes_supported_explicit_onnx_path(monkeypatch, tmp_path):
    path = tmp_path / "model.onnx"
    monkeypatch.setattr(loading, "resolve_local_model", lambda model: path)
    calls = []
    monkeypatch.setitem(
        sys.modules,
        "dpdfnet",
        SimpleNamespace(StreamEnhancer=lambda **kwargs: calls.append(kwargs)),
    )
    loading.load_enhancer()
    assert calls == [{"model": DEFAULT_MODEL, "onnx_path": path, "verbose": False}]


def test_optional_models_are_explicit_local_only(monkeypatch, tmp_path):
    monkeypatch.setenv("DPDFNET_MODEL_DIR", str(tmp_path))
    with pytest.raises(RuntimeError, match="Optional model"):
        loading.resolve_local_model("dpdfnet2")
    (tmp_path / "dpdfnet2.onnx").write_bytes(b"local")
    assert loading.resolve_local_model("dpdfnet2") == tmp_path / "dpdfnet2.onnx"


def test_desktop_model_smoke_invokes_inference(monkeypatch, capsys):
    import numpy as np

    from dpdfnet_mic_bridge import desktop_process

    seen = []

    class Enhancer:
        def process(self, samples, sample_rate):
            seen.append((samples.shape, samples.dtype, sample_rate))
            return np.ones(480, dtype=np.float32)

    monkeypatch.setattr(loading, "load_enhancer", lambda: Enhancer())
    assert desktop_process.internal_command(["_desktop-model-test"]) == 0
    assert seen == [((4800,), np.dtype("float32"), 48000)]
    assert "Bundled model inference passed." in capsys.readouterr().out


def test_desktop_model_smoke_rejects_invalid_output(monkeypatch):
    import numpy as np

    from dpdfnet_mic_bridge import desktop_process

    class Enhancer:
        def process(self, samples, sample_rate):
            return np.array([float("nan")], dtype=np.float32)

    monkeypatch.setattr(loading, "load_enhancer", lambda: Enhancer())
    with pytest.raises(RuntimeError, match="no finite audio samples"):
        desktop_process.internal_command(["_desktop-model-test"])
