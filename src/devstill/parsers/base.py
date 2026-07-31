from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from devstill.models import ChunkPolicy, FileQuality, ParseError, SourceChunk

PYTHON_PARSER_VERSION = "python-ast:2"
JAVASCRIPT_FAMILY_PARSER_VERSION = "tree-sitter-js-family:2"
FALLBACK_PARSER_VERSION = "plain-text-bounded:2"
PARSER_VERSION = PYTHON_PARSER_VERSION
JAVASCRIPT_FAMILY_EXTENSIONS = frozenset({".js", ".jsx", ".ts", ".tsx"})


@dataclass(slots=True)
class ParseResult:
    chunks: list[SourceChunk] = field(default_factory=list)
    errors: list[ParseError] = field(default_factory=list)
    skipped: bool = False
    binary: bool = False
    parser: str = "unknown"
    quality: FileQuality | None = None


def parser_version_for(path: Path) -> str:
    from devstill.parsers.registry import parser_spec_for

    return parser_spec_for(path).version


def parse_source_file(
    path: Path,
    repo_root: Path,
    *,
    policy: ChunkPolicy | None = None,
    index_noisy_files: bool = False,
) -> ParseResult:
    from devstill.parsers.pipeline import parse_with_policy

    return parse_with_policy(
        path,
        repo_root,
        policy or ChunkPolicy(),
        index_noisy_files=index_noisy_files,
    )
