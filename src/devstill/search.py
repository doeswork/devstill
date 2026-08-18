from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any

from rapidfuzz import fuzz, process

from devstill.config import DevstillConfig, resolve_index_path, resolve_model_path
from devstill.embeddings import EmbeddingBackend
from devstill.models import SearchResult
from devstill.paths import IndexPaths
from devstill.retrieval_policy import matches_retrieval_exclude
from devstill.storage import ChromaStore, SQLiteStore

RRF_K = 60.0
MIN_CANDIDATES_PER_CHANNEL = 50
MAX_CANDIDATES_PER_CHANNEL = 80
CANDIDATE_MULTIPLIER = 4
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
        if paths.dirty_marker.exists():
            raise RuntimeError(
                f"Devstill index recovery is required at {paths.root}. "
                "Run `devstill index` before searching."
            )
        if not paths.sqlite.is_file():
            raise FileNotFoundError(
                f"No Devstill index found at {paths.root}. Run `devstill index`."
            )
        self.store = SQLiteStore(paths.sqlite)
        self.retrieval_exclude = config.retrieval.exclude
        self.embedder = embedder or EmbeddingBackend(
            resolve_model_path(config),
            max_sequence_length=config.embedding.max_sequence_length,
            batch_size=config.embedding.batch_size,
            normalize=config.embedding.normalize_embeddings,
            device=config.embedding.device,
        )
        self.chroma = chroma_store or ChromaStore(paths.chroma, config.index.collection)
        self._symbol_choices: dict[str, str] | None = None

    def close(self) -> None:
        self.store.close()

    def _fuzzy_choices(self) -> dict[str, str]:
        if self._symbol_choices is None:
            symbols = self.store.list_symbol_chunks()
            self._symbol_choices = {
                chunk.id: " ".join(
                    value for value in (chunk.name, chunk.qualname, chunk.path) if value
                )
                for chunk in symbols
            }
        return self._symbol_choices

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
        return self.query_many(
            [query],
            limit=limit,
            kind=kind,
            path=path,
            exact_only=exact_only,
            semantic_only=semantic_only,
            lexical_only=lexical_only,
        )[0]

    @staticmethod
    def _candidate_limit(limit: int) -> int:
        if limit < 1:
            raise ValueError("search limit must be at least 1")
        bounded = min(
            MAX_CANDIDATES_PER_CHANNEL,
            max(MIN_CANDIDATES_PER_CHANNEL, limit * CANDIDATE_MULTIPLIER),
        )
        return max(limit, bounded)

    def query_many(
        self,
        queries: list[str],
        limit: int = 10,
        kind: str | None = None,
        path: str | None = None,
        exact_only: bool = False,
        semantic_only: bool = False,
        lexical_only: bool = False,
    ) -> list[list[SearchResult]]:
        """Run several searches while batching their CPU query embeddings."""
        if not queries:
            return []
        if semantic_only and lexical_only:
            raise ValueError(
                "--semantic-only and --lexical-only cannot be used together"
            )
        candidate_limit = self._candidate_limit(limit)
        semantic_groups: list[list[tuple[str, float]] | None] = [None] * len(queries)
        if not exact_only and not lexical_only:
            vectors = self.embedder.encode(queries)
            batch_query = getattr(self.chroma, "query_embeddings", None)
            if callable(batch_query):
                semantic_groups = batch_query(vectors, candidate_limit)
            else:
                semantic_groups = [
                    self.chroma.query_embedding(vector, candidate_limit)
                    for vector in vectors
                ]
        return [
            self._rank_query(
                query,
                limit=limit,
                candidate_limit=candidate_limit,
                semantic_values=semantic_groups[index],
                kind=kind,
                path=path,
                exact_only=exact_only,
                semantic_only=semantic_only,
                lexical_only=lexical_only,
            )
            for index, query in enumerate(queries)
        ]

    def _rank_query(
        self,
        query: str,
        *,
        limit: int,
        candidate_limit: int,
        semantic_values: list[tuple[str, float]] | None,
        kind: str | None,
        path: str | None,
        exact_only: bool,
        semantic_only: bool,
        lexical_only: bool,
    ) -> list[SearchResult]:
        rankings: dict[str, list[tuple[str, float]]] = {}
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
                fuzzy_values = process.extract(
                    query,
                    self._fuzzy_choices(),
                    scorer=fuzz.WRatio,
                    limit=candidate_limit,
                    score_cutoff=45,
                )
                rankings["fuzzy"] = [
                    (str(chunk_id), float(score) / 100.0)
                    for _, score, chunk_id in fuzzy_values
                ]
        if semantic_values is not None:
            rankings["semantic"] = semantic_values

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
            if matches_retrieval_exclude(chunk.path, self.retrieval_exclude):
                continue
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
