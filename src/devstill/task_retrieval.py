"""Bounded source-reference expansion; no model calls or recursive traversal."""

from __future__ import annotations

import re

from devstill.context import is_test_chunk
from devstill.models import SearchResult
from devstill.retrieval_policy import matches_retrieval_exclude


def retrieve_task_candidates(engine, query: str, *, expand: bool = True):
    base = engine.query(query, limit=12)
    store = getattr(engine, "store", None)
    if not expand or store is None:
        return base
    allowed = [
        p
        for p in store.retrieval_paths()
        if not matches_retrieval_exclude(p, engine.retrieval_exclude)
    ]
    fingerprints = store.get_files()
    found = []
    seen = {r.chunk.id for r in base}
    # Only the original three anchors contribute edges: expansion never recurses.
    for anchor in base[:3]:
        name = anchor.chunk.name
        if not name or len(name) < 4:
            continue
        ids = [i for i, _ in store.search_fts(name, limit=24, allowed_paths=allowed)]
        chunks = store.get_chunks_by_ids(ids)
        for chunk in chunks.values():
            if chunk.id in seen or chunk.path == anchor.chunk.path:
                continue
            # A lexical reference is evidence of a relationship, not proof of
            # dynamic call resolution. Keep that distinction in provenance.
            if not re.search(r"\b" + re.escape(name) + r"\s*\(", chunk.code):
                continue
            if matches_retrieval_exclude(chunk.path, engine.retrieval_exclude):
                continue
            seen.add(chunk.id)
            result = SearchResult(
                chunk,
                anchor.final_score * 0.8,
                {"reference": 1.0, "lexical": 1.0},
                fingerprints[chunk.path].content_hash,
            )
            found.append(result)
            if len(found) == 12:
                break
        if len(found) == 12:
            break
    # Preserve precise implementation anchors. Prefer a complementary test over
    # another enclosing implementation, without feeding benchmark role labels.
    found.sort(key=lambda r: (not is_test_chunk(r.chunk), -r.final_score))
    return [*base[:3], *found[:2], *base[3:], *found[2:]]
