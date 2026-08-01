from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from devstill.models import ChunkPolicy, FileQuality, ParseError, SourceChunk

PYTHON_PARSER_VERSION = "python-ast:2"
JAVASCRIPT_FAMILY_PARSER_VERSION = "tree-sitter-js-family:2"
FALLBACK_PARSER_VERSION = "plain-text-bounded:2"
MARKUP_TEXT_PARSER_VERSION = "markup-text:1"
PROSE_TEXT_PARSER_VERSION = "prose-text:1"
CONFIG_TEXT_PARSER_VERSION = "config-text:1"
PARSER_VERSION = PYTHON_PARSER_VERSION
JAVASCRIPT_FAMILY_EXTENSIONS = frozenset({".js", ".jsx", ".ts", ".tsx"})

if TYPE_CHECKING:
    from devstill.content_policy import ContentDecision


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
    include_ignored: list[str] | None = None,
    decision: ContentDecision | None = None,
) -> ParseResult:
    from devstill.parsers.pipeline import parse_with_policy

    return parse_with_policy(
        path,
        repo_root,
        policy or ChunkPolicy(),
        index_noisy_files=index_noisy_files,
        include_ignored=include_ignored or [],
        decision=decision,
    )
