from __future__ import annotations

import sys
import types

import numpy as np

from devstill.embeddings import EmbeddingBackend


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
    result = backend.encode(["longer", "x"])

    assert captured["local_files_only"] is True
    assert captured["device"] == "cpu"
    assert captured["texts"] == ["x", "longer"]
    assert result == [[6.0, 1.0], [1.0, 1.0]]
