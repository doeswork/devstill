from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from devstill.models import ParseError, SourceChunk

PARSER_VERSION = "1"
JAVASCRIPT_FAMILY_PARSER_VERSION = "tree-sitter-js-family:1"
JAVASCRIPT_FAMILY_EXTENSIONS = frozenset({".js", ".jsx", ".ts", ".tsx"})


@dataclass(slots=True)
class ParseResult:
    chunks: list[SourceChunk] = field(default_factory=list)
    errors: list[ParseError] = field(default_factory=list)
    skipped: bool = False


def parser_version_for(path: Path) -> str:
    if path.suffix.lower() in JAVASCRIPT_FAMILY_EXTENSIONS:
        return JAVASCRIPT_FAMILY_PARSER_VERSION
    return PARSER_VERSION


def parse_source_file(path: Path, repo_root: Path) -> ParseResult:
    suffix = path.suffix.lower()
    if suffix == ".py":
        from devstill.parsers.python_ast import parse_python

        return parse_python(path, repo_root)
    if suffix in JAVASCRIPT_FAMILY_EXTENSIONS:
        from devstill.parsers.javascript_tree_sitter import parse_javascript_family

        return parse_javascript_family(path, repo_root)
    from devstill.parsers.plain_text import parse_plain_text

    return parse_plain_text(path, repo_root)
