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


@dataclass(slots=True)
class IndexSummary:
    repository: str
    discovered_files: int = 0
    unchanged_files: int = 0
    parsed_files: int = 0
    deleted_files: int = 0
    canonical_chunks: int = 0
    search_units_embedded: int = 0
    parse_errors: int = 0
    index_path: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class SearchResult:
    chunk: SourceChunk
    final_score: float
    scores: dict[str, float] = field(default_factory=dict)

    def to_dict(self, include_scores: bool = True) -> dict[str, Any]:
        data = self.chunk.to_dict()
        data["final_score"] = self.final_score
        if include_scores:
            data["scores"] = self.scores
        return data
