from __future__ import annotations

from devstill.context import build_search_unit
from devstill.storage.chroma_store import ChromaStore
from tests.test_sqlite_store import sample_chunk


def test_chroma_persists_queries_and_deletes(tmp_path) -> None:
    path = tmp_path / "chroma"
    chunk = sample_chunk()
    unit = build_search_unit(chunk)
    store = ChromaStore(path, "test_collection")
    store.upsert_search_units([unit], [[1.0, 0.0]], {chunk.id: chunk})
    assert store.query_embedding([1.0, 0.0], 1)[0][0] == chunk.id
    reopened = ChromaStore(path, "test_collection")
    assert reopened.query_embedding([1.0, 0.0], 1)[0][0] == chunk.id
    reopened.delete_search_units([unit.id])
    assert reopened.query_embedding([1.0, 0.0], 1) == []
