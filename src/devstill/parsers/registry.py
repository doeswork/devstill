from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from devstill.parsers.base import (
    FALLBACK_PARSER_VERSION,
    JAVASCRIPT_FAMILY_PARSER_VERSION,
    PYTHON_PARSER_VERSION,
    ParseResult,
)

ParserFunction = Callable[[Path, Path], ParseResult]


@dataclass(frozen=True, slots=True)
class ParserSpec:
    name: str
    language: str
    version: str
    extensions: frozenset[str]
    basenames: frozenset[str] = frozenset()
    shebangs: frozenset[str] = frozenset()
    structural: bool = True

    def parse(self, path: Path, repo_root: Path) -> ParseResult:
        if self.name == "python_ast":
            from devstill.parsers.python_ast import parse_python

            return parse_python(path, repo_root)
        if self.name == "javascript_tree_sitter":
            from devstill.parsers.javascript_tree_sitter import parse_javascript_family

            return parse_javascript_family(path, repo_root)
        if self.name == "plain_text":
            from devstill.parsers.plain_text import parse_plain_text

            return parse_plain_text(path, repo_root)
        from devstill.parsers.tree_sitter_languages import parse_tree_sitter_language

        return parse_tree_sitter_language(path, repo_root, self.language)


PARSER_SPECS = (
    ParserSpec(
        "python_ast",
        "python",
        PYTHON_PARSER_VERSION,
        frozenset({".py"}),
        shebangs=frozenset({"python"}),
    ),
    ParserSpec(
        "javascript_tree_sitter",
        "javascript",
        JAVASCRIPT_FAMILY_PARSER_VERSION,
        frozenset({".js", ".jsx", ".ts", ".tsx"}),
    ),
    ParserSpec(
        "tree_sitter_go",
        "go",
        "tree-sitter-go:1",
        frozenset({".go"}),
    ),
    ParserSpec(
        "tree_sitter_rust",
        "rust",
        "tree-sitter-rust:1",
        frozenset({".rs"}),
    ),
    ParserSpec(
        "tree_sitter_ruby",
        "ruby",
        "tree-sitter-ruby:1",
        frozenset({".rb", ".rake", ".gemspec"}),
        frozenset({"Gemfile", "Rakefile", "Guardfile", "Vagrantfile"}),
        frozenset({"ruby"}),
    ),
    ParserSpec(
        "tree_sitter_sql",
        "sql",
        "tree-sitter-sql:1",
        frozenset({".sql"}),
    ),
    ParserSpec(
        "tree_sitter_bash",
        "bash",
        "tree-sitter-bash:1",
        frozenset({".sh", ".bash", ".zsh"}),
        frozenset({".bashrc", ".bash_profile", ".profile"}),
        frozenset({"sh", "bash", "zsh"}),
    ),
    ParserSpec(
        "tree_sitter_html",
        "html",
        "tree-sitter-html:1",
        frozenset({".html", ".htm"}),
    ),
    ParserSpec(
        "tree_sitter_css",
        "css",
        "tree-sitter-css:1",
        frozenset({".css"}),
    ),
    ParserSpec(
        "tree_sitter_java",
        "java",
        "tree-sitter-java:1",
        frozenset({".java"}),
    ),
)

FALLBACK_SPEC = ParserSpec(
    "plain_text",
    "text",
    FALLBACK_PARSER_VERSION,
    frozenset(),
    structural=False,
)


def _shebang(path: Path) -> str | None:
    try:
        with path.open("rb") as handle:
            first = handle.readline(256)
    except OSError:
        return None
    if not first.startswith(b"#!"):
        return None
    text = first.decode("ascii", errors="ignore").casefold()
    for value in ("python", "ruby", "bash", "zsh", "sh"):
        if value in text:
            return value
    return None


def parser_spec_for(path: Path) -> ParserSpec:
    suffix = path.suffix.casefold()
    for spec in PARSER_SPECS:
        if suffix in spec.extensions or path.name in spec.basenames:
            return spec
    detected = _shebang(path)
    if detected:
        for spec in PARSER_SPECS:
            if detected in spec.shebangs:
                return spec
    return FALLBACK_SPEC


def language_for(path: Path) -> str:
    spec = parser_spec_for(path)
    if spec is not FALLBACK_SPEC:
        if path.suffix.casefold() in {".js", ".jsx", ".ts", ".tsx"}:
            return path.suffix.casefold().lstrip(".")
        return spec.language
    return path.suffix.casefold().lstrip(".") or "text"
