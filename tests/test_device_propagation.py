from __future__ import annotations

from devstill import indexer as indexer_module
from devstill import search as search_module
from devstill import tui_commands
from devstill.config import DevstillConfig


class FakeBackend:
    def __init__(self, _model_path, **kwargs) -> None:
        self.kwargs = kwargs
        self.state = "cold"
        self.device = kwargs["device"]

    def close(self) -> None:
        return None


def test_configured_device_reaches_index_search_and_tui_backends(
    monkeypatch,
    tmp_path,
) -> None:
    config_path = tmp_path / "devstill.json"
    config = DevstillConfig(embedding={"device": "cuda"})
    created: list[FakeBackend] = []

    def factory(*args, **kwargs):
        backend = FakeBackend(*args, **kwargs)
        created.append(backend)
        return backend

    monkeypatch.setattr(indexer_module, "EmbeddingBackend", factory)
    indexer = indexer_module.RepositoryIndexer(
        config_path,
        config,
        chroma_store=object(),
    )
    indexer.close()

    monkeypatch.setattr(search_module, "EmbeddingBackend", factory)
    search = search_module.HybridSearch(
        config_path,
        config,
        chroma_store=object(),
    )
    search.close()

    monkeypatch.setattr(tui_commands, "EmbeddingBackend", factory)
    runtime = tui_commands.TuiRetrievalRuntime(
        config_path,
        config,
        chroma_store=object(),
    )
    assert runtime.device == "cuda"
    runtime.close()

    assert len(created) == 3
    assert all(backend.kwargs["device"] == "cuda" for backend in created)
