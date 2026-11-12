from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any

from rapidfuzz import fuzz, process

from devstill.config import DevstillConfig, resolve_index_path, resolve_model_path
from devstill.embeddings import EmbeddingBackend
from devstill.models import SearchResult
from devstill.paths import IndexPaths
from devstill.retrieval_policy import (
    is_overload,
    matches_retrieval_exclude,
    query_references,
    requests_signatures,
)
from devstill.storage import LazyChromaStore, SQLiteStore

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
        self.vectors = config.index.vectors
        self.retrieval_exclude = config.retrieval.exclude
        self.embedder = embedder or EmbeddingBackend(
            resolve_model_path(config),
            max_sequence_length=config.embedding.max_sequence_length,
            batch_size=config.embedding.batch_size,
            normalize=config.embedding.normalize_embeddings,
            device=config.embedding.device,
        )
        self.chroma = chroma_store or LazyChromaStore(
            paths.chroma, config.index.collection
        )
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
        if not self.vectors:
            if semantic_only:
                raise ValueError("This index has no vectors (index.vectors is false)")
            # A lexical-only index answers every query without embeddings.
            lexical_only = not exact_only
        candidate_limit = self._candidate_limit(limit)
        allowed_paths = None
        if path or self.retrieval_exclude:
            allowed_paths = [
                p
                for p in self.store.retrieval_paths()
                if (not path or p.startswith(path))
                and not matches_retrieval_exclude(p, self.retrieval_exclude)
            ]
            if not allowed_paths:
                return [[] for _ in queries]
        where_parts = []
        if allowed_paths is not None:
            where_parts.append({"path": {"$in": allowed_paths}})
        if kind:
            where_parts.append({"kind": kind})
        where = (
            ({"$and": where_parts} if len(where_parts) > 1 else where_parts[0])
            if where_parts
            else None
        )
        vector_options = {"where": where} if where else {}
        semantic_groups: list[list[tuple[str, float]] | None] = [None] * len(queries)
        if not exact_only and not lexical_only:
            vectors = self.embedder.encode(queries)
            batch_query = getattr(self.chroma, "query_embeddings", None)
            if callable(batch_query):
                semantic_groups = batch_query(
                    vectors, candidate_limit, **vector_options
                )
            else:
                semantic_groups = [
                    self.chroma.query_embedding(
                        vector, candidate_limit, **vector_options
                    )
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
                allowed_paths=allowed_paths,
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
        allowed_paths: list[str] | None,
    ) -> list[SearchResult]:
        rankings: dict[str, list[tuple[str, float]]] = {}
        scope = {"allowed_paths": allowed_paths, "kind": kind}
        if not semantic_only:
            exact = self.store.find_symbol_exact(query, candidate_limit, **scope)
            if not exact_only:
                for reference in query_references(query):
                    exact.extend(
                        self.store.find_symbol_exact(
                            reference, candidate_limit, **scope
                        )
                    )
            exact = list(dict.fromkeys(exact))
            rankings["exact"] = [(chunk_id, 1.0) for chunk_id in exact]
            if not exact_only:
                prefix = self.store.find_symbol_prefix(query, candidate_limit, **scope)
                rankings["prefix"] = [(chunk_id, 1.0) for chunk_id in prefix]
                path_ids = self.store.search_paths(query, candidate_limit, **scope)
                rankings["path"] = [(chunk_id, 1.0) for chunk_id in path_ids]
                fts = self.store.search_fts(query, candidate_limit, **scope)
                rankings["lexical"] = [
                    (chunk_id, 1.0 / (1.0 + abs(rank))) for chunk_id, rank in fts
                ]
                choices = self._fuzzy_choices() if len(query.split()) <= 3 else {}
                if choices and (allowed_paths is not None or kind):
                    eligible = self.store.eligible_chunk_ids(**scope)
                    choices = {
                        key: value for key, value in choices.items() if key in eligible
                    }
                fuzzy_values = (
                    process.extract(
                        query,
                        choices,
                        scorer=fuzz.WRatio,
                        limit=candidate_limit,
                        score_cutoff=45,
                    )
                    if len(query.split()) <= 3
                    else []
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
        if not requests_signatures(query):
            resolved = []
            files = {}
            for result in ordered:
                chunk = result.chunk
                if is_overload(chunk):
                    if chunk.path not in files:
                        files[chunk.path] = self.store.list_chunks_for_file(chunk.path)
                    implementations = [
                        c
                        for c in files[chunk.path]
                        if c.qualname == chunk.qualname
                        and c.kind == chunk.kind
                        and c.language == chunk.language
                        and not is_overload(c)
                        and c.parser_metadata.get("searchable", True)
                    ]
                    # Ambiguous conditional definitions remain visible for review.
                    if len(implementations) == 1:
                        result = SearchResult(
                            implementations[0], result.final_score, dict(result.scores)
                        )
                resolved.append(result)
            ordered = resolved
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
        # Fingerprints describe the indexed file, not normalized parser snippets.
        get_files = getattr(self.store, "get_files", None)
        files = get_files() if callable(get_files) else {}
        for result in selected:
            record = files.get(result.chunk.path)
            if record is not None:
                result.source_hash = record.content_hash
        return selected
