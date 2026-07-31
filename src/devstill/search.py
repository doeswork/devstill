from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any

from rapidfuzz import fuzz, process

from devstill.config import DevstillConfig, resolve_index_path, resolve_model_path
from devstill.embeddings import EmbeddingBackend
from devstill.models import SearchResult
from devstill.paths import IndexPaths
from devstill.storage import ChromaStore, SQLiteStore

RRF_K = 60.0
WEIGHTS = {
    "exact": 6.0,
    "prefix": 4.0,
    "path": 3.0,
    "fuzzy": 2.0,
    "lexical": 2.0,
    "semantic": 2.0,
}


class HybridSearch:
    def __init__(
        self,
        config_path: Path,
        config: DevstillConfig,
        embedder: Any | None = None,
        chroma_store: Any | None = None,
    ) -> None:
        paths = IndexPaths.from_chroma_path(resolve_index_path(config_path, config))
        if not paths.sqlite.is_file():
            raise FileNotFoundError(
                f"No Devstill index found at {paths.root}. Run `devstill index`."
            )
        self.store = SQLiteStore(paths.sqlite)
        self.embedder = embedder or EmbeddingBackend(
            resolve_model_path(config),
            max_sequence_length=config.embedding.max_sequence_length,
            batch_size=config.embedding.batch_size,
            normalize=config.embedding.normalize_embeddings,
        )
        self.chroma = chroma_store or ChromaStore(paths.chroma, config.index.collection)

    def close(self) -> None:
        self.store.close()

    def query(
        self,
        query: str,
        limit: int = 10,
        kind: str | None = None,
        path: str | None = None,
        exact_only: bool = False,
        semantic_only: bool = False,
        lexical_only: bool = False,
    ) -> list[SearchResult]:
        if semantic_only and lexical_only:
            raise ValueError(
                "--semantic-only and --lexical-only cannot be used together"
            )
        rankings: dict[str, list[tuple[str, float]]] = {}
        candidate_limit = max(limit * 10, 50)
        if not semantic_only:
            exact = self.store.find_symbol_exact(query, candidate_limit)
            rankings["exact"] = [(chunk_id, 1.0) for chunk_id in exact]
            if not exact_only:
                prefix = self.store.find_symbol_prefix(query, candidate_limit)
                rankings["prefix"] = [(chunk_id, 1.0) for chunk_id in prefix]
                path_ids = self.store.search_paths(query, candidate_limit)
                rankings["path"] = [(chunk_id, 1.0) for chunk_id in path_ids]
                fts = self.store.search_fts(query, candidate_limit)
                rankings["lexical"] = [
                    (chunk_id, 1.0 / (1.0 + abs(rank))) for chunk_id, rank in fts
                ]
                symbols = self.store.list_symbol_chunks()
                choices = {
                    chunk.id: " ".join(
                        value
                        for value in (chunk.name, chunk.qualname, chunk.path)
                        if value
                    )
                    for chunk in symbols
                }
                fuzzy_values = process.extract(
                    query,
                    choices,
                    scorer=fuzz.WRatio,
                    limit=candidate_limit,
                    score_cutoff=45,
                )
                rankings["fuzzy"] = [
                    (str(chunk_id), float(score) / 100.0)
                    for _, score, chunk_id in fuzzy_values
                ]
        if not exact_only and not lexical_only:
            vector = self.embedder.encode([query])[0]
            rankings["semantic"] = self.chroma.query_embedding(vector, candidate_limit)

        final_scores: defaultdict[str, float] = defaultdict(float)
        components: defaultdict[str, dict[str, float]] = defaultdict(dict)
        for channel, values in rankings.items():
            for rank, (chunk_id, raw_score) in enumerate(values, start=1):
                components[chunk_id][channel] = raw_score
                final_scores[chunk_id] += WEIGHTS[channel] / (RRF_K + rank)
                if channel == "exact":
                    final_scores[chunk_id] += 1.0
        chunks = self.store.get_chunks_by_ids(final_scores)
        ordered = sorted(
            (
                SearchResult(chunks[chunk_id], score, components[chunk_id])
                for chunk_id, score in final_scores.items()
                if chunk_id in chunks
            ),
            key=lambda result: (
                -result.final_score,
                result.chunk.path,
                result.chunk.start_line,
            ),
        )
        selected: list[SearchResult] = []
        per_file: defaultdict[str, int] = defaultdict(int)
        seen_ranges: set[tuple[str, int, int]] = set()
        for result in ordered:
            chunk = result.chunk
            if kind and chunk.kind != kind:
                continue
            if path and not chunk.path.startswith(path):
                continue
            source_range = (chunk.path, chunk.start_line, chunk.end_line)
            if source_range in seen_ranges or per_file[chunk.path] >= 3:
                continue
            selected.append(result)
            seen_ranges.add(source_range)
            per_file[chunk.path] += 1
            if len(selected) >= limit:
                break
        return selected
