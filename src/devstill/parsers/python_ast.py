from __future__ import annotations

import ast
import re
from pathlib import Path

from devstill.hashing import hash_text
from devstill.models import ParseError, SourceChunk
from devstill.parsers.base import ParseResult
from devstill.paths import relative_repo_path

_SECTION_RE = re.compile(
    r"^\s*#\s*(?:[-=#_*]{3,}|copyright|license|!/|coding[:=])", re.IGNORECASE
)


class PythonChunker:
    def __init__(
        self, path: Path, repo_root: Path, source: str, tree: ast.Module
    ) -> None:
        self.path = path
        self.relative = relative_repo_path(repo_root, path)
        self.source = source
        self.lines = source.splitlines(keepends=True)
        self.tree = tree
        self.chunks: list[SourceChunk] = []
        self.ids: dict[str, int] = {}

    def run(self) -> list[SourceChunk]:
        module = self._add(
            node=self.tree,
            kind="module",
            name=self.path.stem,
            qualname=self.path.stem,
            parent_id=None,
            start_line=1,
            end_line=max(1, len(self.source.splitlines())),
            code=self.source,
            metadata={"parser": "python_ast", "searchable": False},
        )
        self._visit_body(self.tree.body, [], module.id, "module")
        return self.chunks

    def _unique_id(self, base: str) -> str:
        count = self.ids.get(base, 0) + 1
        self.ids[base] = count
        return base if count == 1 else f"{base}::{count}"

    def _offset(self, line: int, column: int) -> int:
        prefix = (
            self.lines[line - 1]
            .encode("utf-8")[:column]
            .decode("utf-8", errors="ignore")
        )
        return sum(len(value) for value in self.lines[: line - 1]) + len(prefix)

    def _segment(self, node: ast.AST, start_line: int | None = None) -> str:
        node_start = start_line or getattr(node, "lineno", 1)
        start_column = getattr(node, "col_offset", 0)
        end_line = getattr(node, "end_lineno", node_start)
        end_column = getattr(node, "end_col_offset", len(self.lines[end_line - 1]))
        return self.source[
            self._offset(node_start, start_column) : self._offset(end_line, end_column)
        ]

    def _leading_comments(
        self, start_line: int, indentation: int
    ) -> tuple[str | None, dict[str, object]]:
        cursor = start_line - 2
        blank_count = 0
        collected: list[str] = []
        while cursor >= 0:
            raw = self.lines[cursor].rstrip("\r\n")
            if not raw.strip():
                blank_count += 1
                if blank_count > 1 or collected:
                    break
                cursor -= 1
                continue
            stripped = raw.lstrip()
            if not stripped.startswith("#") or len(raw) - len(stripped) != indentation:
                break
            collected.append(raw)
            cursor -= 1
        collected.reverse()
        if not collected or any(_SECTION_RE.match(line) for line in collected):
            return None, {
                "comment_attachment_reason": "no descriptive adjacent comment",
                "comment_attachment_confidence": 0.0,
            }
        return "\n".join(collected), {
            "comment_attachment_reason": "adjacent comment with matching indentation",
            "comment_attachment_confidence": 1.0,
        }

    def _add(
        self,
        node: ast.AST,
        kind: str,
        name: str | None,
        qualname: str | None,
        parent_id: str | None,
        start_line: int | None = None,
        end_line: int | None = None,
        code: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> SourceChunk:
        node_line = getattr(node, "lineno", 1)
        decorators = getattr(node, "decorator_list", [])
        first_line = start_line or min(
            [node_line, *(item.lineno for item in decorators)]
        )
        final_line = end_line or getattr(node, "end_lineno", first_line)
        comments, comment_meta = self._leading_comments(
            first_line, getattr(node, "col_offset", 0)
        )
        body = getattr(node, "body", [])
        split_points = [value.lineno for value in body if isinstance(value, ast.stmt)]
        meta = {
            "parser": "python_ast",
            "split_points": split_points,
            **comment_meta,
            **(metadata or {}),
        }
        base = f"{self.relative}::{qualname or name or first_line}::{kind}"
        chunk = SourceChunk(
            id=self._unique_id(base),
            path=self.relative,
            filename=self.path.name,
            language="python",
            kind=kind,
            name=name,
            qualname=qualname,
            parent_id=parent_id,
            start_line=first_line,
            end_line=final_line,
            code=code if code is not None else self._segment(node, first_line),
            leading_comments=comments,
            content_hash="",
            parser_metadata=meta,
        )
        chunk.content_hash = hash_text((comments or "") + "\n" + chunk.code)
        self.chunks.append(chunk)
        return chunk

    def _visit_body(
        self,
        body: list[ast.stmt],
        parents: list[str],
        parent_id: str,
        parent_kind: str,
    ) -> None:
        index = 0
        while index < len(body):
            node = body[index]
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                group = [node]
                while index + 1 < len(body) and isinstance(
                    body[index + 1], (ast.Import, ast.ImportFrom)
                ):
                    index += 1
                    group.append(body[index])
                first, last = group[0], group[-1]
                code = self.source[
                    self._offset(first.lineno, first.col_offset) : self._offset(
                        last.end_lineno or last.lineno, last.end_col_offset or 0
                    )
                ]
                self._add(
                    first,
                    "import_group",
                    None,
                    ".".join([*parents, f"imports@{first.lineno}"]),
                    parent_id,
                    end_line=last.end_lineno,
                    code=code,
                    metadata={"import_count": len(group)},
                )
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qualname = ".".join([*parents, node.name])
                if parent_kind == "class":
                    kind = (
                        "async_method"
                        if isinstance(node, ast.AsyncFunctionDef)
                        else "method"
                    )
                elif parent_kind in {
                    "function",
                    "async_function",
                    "method",
                    "async_method",
                    "nested_function",
                }:
                    kind = "nested_function"
                else:
                    kind = (
                        "async_function"
                        if isinstance(node, ast.AsyncFunctionDef)
                        else "function"
                    )
                chunk = self._add(
                    node,
                    kind,
                    node.name,
                    qualname,
                    parent_id,
                    metadata={
                        "decorators": [
                            ast.unparse(item) for item in node.decorator_list
                        ],
                        "async": isinstance(node, ast.AsyncFunctionDef),
                    },
                )
                self._visit_body(node.body, [*parents, node.name], chunk.id, kind)
            elif isinstance(node, ast.ClassDef):
                qualname = ".".join([*parents, node.name])
                chunk = self._add(
                    node,
                    "class",
                    node.name,
                    qualname,
                    parent_id,
                    metadata={
                        "decorators": [
                            ast.unparse(item) for item in node.decorator_list
                        ],
                        "bases": [ast.unparse(item) for item in node.bases],
                    },
                )
                self._visit_body(node.body, [*parents, node.name], chunk.id, "class")
            elif isinstance(node, (ast.Assign, ast.AnnAssign)) and parent_kind in {
                "module",
                "class",
            }:
                targets = (
                    node.targets if isinstance(node, ast.Assign) else [node.target]
                )
                names = [ast.unparse(target) for target in targets]
                name = ",".join(names)
                qualname = ".".join([*parents, name])
                self._add(
                    node,
                    "constant"
                    if all(value.isupper() for value in names)
                    else "assignment",
                    name,
                    qualname,
                    parent_id,
                    metadata={"targets": names},
                )
            index += 1


def parse_python(path: Path, repo_root: Path) -> ParseResult:
    relative = relative_repo_path(repo_root, path)
    try:
        source = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        return ParseResult(
            errors=[ParseError(relative, f"File is not valid UTF-8: {exc}")],
            skipped=True,
        )
    try:
        tree = ast.parse(source, filename=relative, type_comments=True)
    except SyntaxError as exc:
        return ParseResult(
            errors=[ParseError(relative, exc.msg, exc.lineno, exc.offset)],
        )
    return ParseResult(chunks=PythonChunker(path, repo_root, source, tree).run())
