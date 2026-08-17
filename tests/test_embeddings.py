from __future__ import annotations

import sys
import types
import warnings

import numpy as np
import pytest

from devstill import embeddings
from devstill.embeddings import EmbeddingBackend


def test_embedding_load_suppresses_only_optional_onnx_warning(
    monkeypatch, tmp_path
) -> None:
    class WarningModel:
        def __init__(self, *_args, **_kwargs):
            warnings.warn(
                "optimum is not installed. To use OnnxConfig and BertOnnxConfig, "
                "make sure that `optimum` package is installed",
                UserWarning,
                stacklevel=2,
            )
            warnings.warn("keep this model warning", UserWarning, stacklevel=2)
            self.max_seq_length = None

    module = types.SimpleNamespace(SentenceTransformer=WarningModel)
    monkeypatch.setitem(sys.modules, "sentence_transformers", module)
    backend = EmbeddingBackend(tmp_path)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        backend.warm()

    messages = [str(item.message) for item in caught]
    assert messages == ["keep this model warning"]


def test_embedding_backend_forces_offline_cpu_and_restores_order(
    monkeypatch, tmp_path
) -> None:
    captured = {}

    class FakeModel:
        def __init__(self, path, **kwargs):
            captured["path"] = path
            captured.update(kwargs)
            self.max_seq_length = None

        def encode(self, texts, **kwargs):
            captured["texts"] = texts
            captured["encode"] = kwargs
            return np.array([[len(text), 1.0] for text in texts], dtype=float)

    module = types.SimpleNamespace(SentenceTransformer=FakeModel)
    monkeypatch.setitem(sys.modules, "sentence_transformers", module)
    backend = EmbeddingBackend(tmp_path, max_sequence_length=1024, batch_size=4)
    assert backend.state == "cold"
    loading_states = []
    backend.warm(lambda: loading_states.append(backend.state))
    result = backend.encode(["longer", "x"])

    assert captured["local_files_only"] is True
    assert captured["device"] == "cpu"
    assert captured["texts"] == ["x", "longer"]
    assert result == [[6.0, 1.0], [1.0, 1.0]]
    assert loading_states == ["loading"]
    assert backend.state == "warm"
    assert backend.is_loaded
    backend.close()
    assert backend.state == "cold"
    assert not backend.is_loaded


def test_embedding_warm_failure_returns_to_cold_and_can_retry(
    monkeypatch, tmp_path
) -> None:
    attempts = 0

    class FlakyModel:
        def __init__(self, *_args, **_kwargs):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise OSError("load failed")
            self.max_seq_length = None

    module = types.SimpleNamespace(SentenceTransformer=FlakyModel)
    monkeypatch.setitem(sys.modules, "sentence_transformers", module)
    backend = EmbeddingBackend(tmp_path)

    try:
        backend.warm()
    except RuntimeError:
        pass
    else:
        raise AssertionError("first warm should fail")

    assert backend.state == "cold"
    backend.warm()
    assert backend.state == "warm"
    assert attempts == 2


def test_embedding_progress_uses_groups_and_restores_original_order(
    monkeypatch, tmp_path
) -> None:
    calls: list[tuple[list[str], dict]] = []

    class FakeModel:
        def __init__(self, *_args, **_kwargs):
            self.max_seq_length = None

        def encode(self, texts, **kwargs):
            calls.append((list(texts), kwargs))
            return np.array(
                [[int(text.split(":", 1)[0]), 1.0] for text in texts],
                dtype=float,
            )

    module = types.SimpleNamespace(SentenceTransformer=FakeModel)
    monkeypatch.setitem(sys.modules, "sentence_transformers", module)
    backend = EmbeddingBackend(tmp_path, batch_size=8)
    texts = [f"{index}:{'x' * (130 - index)}" for index in range(130)]
    progress: list[tuple[int, int]] = []

    result = backend.encode(
        texts,
        show_progress=True,
        progress=lambda current, total: progress.append((current, total)),
    )

    assert [len(texts) for texts, _kwargs in calls] == [64, 64, 2]
    assert progress == [(64, 130), (128, 130), (130, 130)]
    assert [int(vector[0]) for vector in result] == list(range(130))
    assert all(kwargs["show_progress_bar"] is False for _texts, kwargs in calls)


def test_embedding_backend_passes_resolved_cuda_device(monkeypatch, tmp_path) -> None:
    captured: dict[str, object] = {}

    class FakeModel:
        def __init__(self, _path, **kwargs):
            captured.update(kwargs)
            self.max_seq_length = None

    monkeypatch.setattr(
        embeddings,
        "inspect_device",
        lambda _requested: types.SimpleNamespace(resolved="cuda"),
    )
    monkeypatch.setitem(
        sys.modules,
        "sentence_transformers",
        types.SimpleNamespace(SentenceTransformer=FakeModel),
    )

    backend = EmbeddingBackend(tmp_path, device="auto")
    backend.warm()

    assert backend.device == "cuda"
    assert captured["device"] == "cuda"


def test_cuda_oom_has_actionable_batch_size_guidance(monkeypatch, tmp_path) -> None:
    class OomModel:
        def __init__(self, *_args, **_kwargs):
            self.max_seq_length = None

        def encode(self, *_args, **_kwargs):
            raise RuntimeError("CUDA out of memory")

    monkeypatch.setattr(
        embeddings,
        "inspect_device",
        lambda _requested: types.SimpleNamespace(resolved="cuda"),
    )
    monkeypatch.setitem(
        sys.modules,
        "sentence_transformers",
        types.SimpleNamespace(SentenceTransformer=OomModel),
    )
    backend = EmbeddingBackend(tmp_path, batch_size=32, device="cuda")

    with pytest.raises(RuntimeError, match="below 32"):
        backend.encode(["one"])
