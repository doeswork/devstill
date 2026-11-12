from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(slots=True)
class SourceChunk:
    id: str
    path: str
    filename: str
    language: str
    kind: str
    name: str | None
    qualname: str | None
    parent_id: str | None
    start_line: int
    end_line: int
    code: str
    leading_comments: str | None
    content_hash: str
    parser_metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class SearchUnit:
    id: str
    source_chunk_ids: list[str]
    embedding_text: str
    summary: str | None
    concepts: list[str]
    content_hash: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ParseError:
    path: str
    message: str
    line: int | None = None
    column: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class FileRecord:
    path: str
    content_hash: str
    size_bytes: int
    modified_ns: int
    language: str
    parser_version: str


@dataclass(slots=True, frozen=True)
class ChunkPolicy:
    max_chars: int = 8000
    max_lines: int = 400
    overlap_lines: int = 20


@dataclass(slots=True)
class FileQuality:
    path: str
    status: str
    parser: str
    reasons: list[str] = field(default_factory=list)
    canonical_chunks: int = 0
    searchable_chunks: int = 0
    max_chunk_chars: int = 0
    max_chunk_lines: int = 0
    fallback_segments: int = 0
    noisy: bool = False
    skipped: bool = False
    category: str = "unknown_text"
    disposition: str = "indexed"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class IndexSummary:
    repository: str
    discovered_files: int = 0
    unchanged_files: int = 0
    parsed_files: int = 0
    deleted_files: int = 0
    canonical_chunks: int = 0
    search_units_embedded: int = 0
    reconciled_search_units: int = 0
    parse_errors: int = 0
    eligible_files: int = 0
    code_files: int = 0
    documentation_files: int = 0
    configuration_files: int = 0
    good_files: int = 0
    meh_files: int = 0
    bad_files: int = 0
    ignored_files: int = 0
    noisy_files_skipped: int = 0
    empty_files_skipped: int = 0
    binary_files_skipped: int = 0
    index_path: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True, frozen=True)
class IndexFileProgress:
    total: int = 0
    checked: int = 0
    changed: int = 0
    unchanged: int = 0
    parsed: int = 0


@dataclass(slots=True, frozen=True)
class IndexProgress:
    stage: str
    message: str
    current: int | None = None
    total: int | None = None
    files: IndexFileProgress | None = None


@dataclass(slots=True)
class SearchResult:
    chunk: SourceChunk
    final_score: float
    scores: dict[str, float] = field(default_factory=dict)
    source_hash: str | None = None

    def to_dict(self, include_scores: bool = True) -> dict[str, Any]:
        data = self.chunk.to_dict()
        data["final_score"] = self.final_score
        if include_scores:
            data["scores"] = self.scores
        return data
