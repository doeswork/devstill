from __future__ import annotations

import re
from pathlib import Path

import tree_sitter_javascript
import tree_sitter_typescript
from tree_sitter import Language, Node, Parser

from devstill.hashing import hash_text
from devstill.models import ParseError, SourceChunk
from devstill.parsers.base import ParseResult
from devstill.paths import relative_repo_path

_FUNCTION_NODES = frozenset(
    {
        "function_declaration",
        "generator_function_declaration",
    }
)
_CLASS_NODES = frozenset({"class_declaration", "abstract_class_declaration"})
_VARIABLE_DECLARATIONS = frozenset({"lexical_declaration", "variable_declaration"})
_TYPE_KINDS = {
    "type_alias_declaration": "type_alias",
    "interface_declaration": "interface",
    "enum_declaration": "enum",
}
_CALLABLE_NODES = frozenset({"arrow_function", "function_expression"})
_CALLABLE_WRAPPERS = frozenset(
    {
        "forwardRef",
        "memo",
        "React.forwardRef",
        "React.memo",
        "React.useCallback",
        "useCallback",
    }
)
_EXPRESSION_WRAPPERS = frozenset(
    {
        "as_expression",
        "parenthesized_expression",
        "satisfies_expression",
        "type_assertion",
    }
)
_SECTION_COMMENT_RE = re.compile(
    r"^\s*(?://+|/\*+|\*+)\s*(?:[-=#_*]{3,}|copyright\b|license\b)",
    re.IGNORECASE,
)


def _language_for(suffix: str) -> Language:
    if suffix == ".ts":
        return Language(tree_sitter_typescript.language_typescript())
    if suffix == ".tsx":
        return Language(tree_sitter_typescript.language_tsx())
    return Language(tree_sitter_javascript.language())


class JavaScriptChunker:
    def __init__(
        self,
        path: Path,
        repo_root: Path,
        source: str,
        source_bytes: bytes,
        root: Node,
    ) -> None:
        self.path = path
        self.relative = relative_repo_path(repo_root, path)
        self.language = path.suffix.lower().lstrip(".")
        self.source = source
        self.source_bytes = source_bytes
        self.lines = source.splitlines(keepends=True)
        self.root = root
        self.chunks: list[SourceChunk] = []
        self.ids: dict[str, int] = {}

    def run(self) -> list[SourceChunk]:
        module = self._add(
            node=self.root,
            boundary=self.root,
            kind="module",
            name=self.path.stem,
            qualname=self.path.stem,
            parent_id=None,
            code=self.source,
            metadata={"searchable": False},
        )
        self._visit_scope(self.root, [], module.id, "module")
        return self.chunks

    def _text(self, node: Node) -> str:
        return self.source_bytes[node.start_byte : node.end_byte].decode("utf-8")

    def _unique_id(self, base: str) -> str:
        count = self.ids.get(base, 0) + 1
        self.ids[base] = count
        return base if count == 1 else f"{base}::{count}"

    @staticmethod
    def _end_line(node: Node) -> int:
        if node.end_point.column == 0 and node.end_point.row > node.start_point.row:
            return node.end_point.row
        return node.end_point.row + 1

    def _leading_comments(
        self, start_line: int, indentation: int
    ) -> tuple[str | None, dict[str, object]]:
        cursor = start_line - 2
        blank_count = 0
        collected: list[str] = []
        in_block = False
        while cursor >= 0:
            raw = self.lines[cursor].rstrip("\r\n")
            stripped = raw.lstrip()
            current_indentation = len(raw) - len(stripped)
            if not stripped:
                blank_count += 1
                if blank_count > 1 or collected:
                    break
                cursor -= 1
                continue
            if current_indentation != indentation:
                break
            if in_block:
                collected.append(raw)
                if "/*" in stripped:
                    in_block = False
                cursor -= 1
                continue
            if stripped.startswith("//"):
                collected.append(raw)
                cursor -= 1
                continue
            if stripped.endswith("*/"):
                collected.append(raw)
                if "/*" not in stripped:
                    in_block = True
                cursor -= 1
                continue
            break
        collected.reverse()
        if (
            in_block
            or not collected
            or any(_SECTION_COMMENT_RE.match(line) for line in collected)
        ):
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
        node: Node,
        boundary: Node,
        kind: str,
        name: str | None,
        qualname: str | None,
        parent_id: str | None,
        code: str | None = None,
        metadata: dict[str, object] | None = None,
        start_line: int | None = None,
        end_line: int | None = None,
    ) -> SourceChunk:
        first_line = start_line or boundary.start_point.row + 1
        final_line = end_line or self._end_line(boundary)
        comments, comment_meta = self._leading_comments(
            first_line, boundary.start_point.column
        )
        chunk_code = code if code is not None else self._text(boundary)
        body = node.child_by_field_name("body")
        meta = {
            "parser": "tree_sitter",
            "grammar": self.language,
            "node_type": node.type,
            "split_points": (
                [child.start_point.row + 1 for child in body.named_children]
                if body is not None
                else []
            ),
            **comment_meta,
            **(metadata or {}),
        }
        base = f"{self.relative}::{qualname or name or first_line}::{kind}"
        chunk = SourceChunk(
            id=self._unique_id(base),
            path=self.relative,
            filename=self.path.name,
            language=self.language,
            kind=kind,
            name=name,
            qualname=qualname,
            parent_id=parent_id,
            start_line=first_line,
            end_line=final_line,
            code=chunk_code,
            leading_comments=comments,
            content_hash=hash_text((comments or "") + "\n" + chunk_code),
            parser_metadata=meta,
        )
        self.chunks.append(chunk)
        return chunk

    def _metadata(self, boundary: Node, node: Node) -> dict[str, object]:
        prefix = self.source_bytes[boundary.start_byte : node.start_byte].decode(
            "utf-8"
        )
        decorators = list(
            dict.fromkeys(
                self._text(child)
                for parent in (boundary, node)
                for child in parent.named_children
                if child.type == "decorator"
            )
        )
        return {
            "exported": boundary.type == "export_statement",
            "default_export": bool(
                boundary.type == "export_statement"
                and re.search(r"\bdefault\b", prefix)
            ),
            "decorators": decorators,
        }

    def _name(self, node: Node) -> str | None:
        name = node.child_by_field_name("name")
        return self._text(name) if name is not None else None

    def _is_async(self, node: Node) -> bool:
        return any(child.type == "async" for child in node.children)

    def _callable_value(self, value: Node | None) -> tuple[Node | None, str | None]:
        if value is None:
            return None, None
        while value.type in _EXPRESSION_WRAPPERS and value.named_child_count:
            value = value.named_children[0]
        if value.type in _CALLABLE_NODES:
            return value, None
        if value.type != "call_expression":
            return None, None
        function = value.child_by_field_name("function")
        wrapper = self._text(function) if function is not None else ""
        if wrapper not in _CALLABLE_WRAPPERS:
            return None, None
        arguments = value.child_by_field_name("arguments")
        if arguments is None:
            return None, None
        for child in arguments.named_children:
            candidate = child
            while (
                candidate.type in _EXPRESSION_WRAPPERS and candidate.named_child_count
            ):
                candidate = candidate.named_children[0]
            if candidate.type in _CALLABLE_NODES:
                return candidate, wrapper
        return None, None

    def _function_kind(
        self, parent_kind: str, is_async: bool, is_generator: bool
    ) -> str:
        if parent_kind == "class":
            return "async_method" if is_async else "method"
        if parent_kind != "module":
            return "nested_function"
        if is_generator:
            return "generator_function"
        return "async_function" if is_async else "function"

    def _visit_function(
        self,
        node: Node,
        boundary: Node,
        parents: list[str],
        parent_id: str,
        parent_kind: str,
    ) -> None:
        name = self._name(node)
        if not name:
            return
        qualname = ".".join([*parents, name])
        is_async = self._is_async(node)
        is_generator = node.type == "generator_function_declaration"
        metadata = {
            **self._metadata(boundary, node),
            "async": is_async,
            "generator": is_generator,
        }
        chunk = self._add(
            node,
            boundary,
            self._function_kind(parent_kind, is_async, is_generator),
            name,
            qualname,
            parent_id,
            metadata=metadata,
        )
        body = node.child_by_field_name("body")
        if body is not None:
            self._visit_scope(body, [*parents, name], chunk.id, "function")

    def _visit_method(
        self,
        node: Node,
        parents: list[str],
        parent_id: str,
        decorators: list[Node] | None = None,
    ) -> None:
        name = self._name(node)
        if not name:
            return
        qualname = ".".join([*parents, name])
        is_async = self._is_async(node)
        decorators = decorators or []
        code = (
            self.source_bytes[decorators[0].start_byte : node.end_byte].decode("utf-8")
            if decorators
            else self._text(node)
        )
        chunk = self._add(
            node,
            node,
            "async_method" if is_async else "method",
            name,
            qualname,
            parent_id,
            code=code,
            metadata={
                "async": is_async,
                "generator": "*" in self._text(node).split("(", 1)[0],
                "decorators": [self._text(value) for value in decorators],
            },
            start_line=(decorators[0].start_point.row + 1 if decorators else None),
        )
        body = node.child_by_field_name("body")
        if body is not None:
            self._visit_scope(body, [*parents, name], chunk.id, "function")

    def _visit_class(
        self,
        node: Node,
        boundary: Node,
        parents: list[str],
        parent_id: str,
    ) -> None:
        name = self._name(node)
        if not name:
            return
        qualname = ".".join([*parents, name])
        chunk = self._add(
            node,
            boundary,
            "class",
            name,
            qualname,
            parent_id,
            metadata=self._metadata(boundary, node),
        )
        body = node.child_by_field_name("body")
        if body is None:
            return
        decorators: list[Node] = []
        for child in body.named_children:
            if child.type == "decorator":
                decorators.append(child)
                continue
            if child.type == "method_definition":
                self._visit_method(child, [*parents, name], chunk.id, decorators)
            elif child.type in {
                "field_definition",
                "public_field_definition",
            }:
                self._visit_class_field(child, [*parents, name], chunk.id)
            decorators = []

    def _visit_class_field(
        self, node: Node, parents: list[str], parent_id: str
    ) -> None:
        name_node = node.child_by_field_name("property") or node.child_by_field_name(
            "name"
        )
        value = node.child_by_field_name("value")
        callable_node, wrapper = self._callable_value(value)
        if name_node is None or callable_node is None:
            return
        name = self._text(name_node)
        qualname = ".".join([*parents, name])
        is_async = self._is_async(callable_node)
        chunk = self._add(
            node,
            node,
            "async_method" if is_async else "method",
            name,
            qualname,
            parent_id,
            metadata={"async": is_async, "wrapper": wrapper},
        )
        body = callable_node.child_by_field_name("body")
        if body is not None and body.type == "statement_block":
            self._visit_scope(body, [*parents, name], chunk.id, "function")

    def _visit_variable_declaration(
        self,
        node: Node,
        boundary: Node,
        parents: list[str],
        parent_id: str,
        parent_kind: str,
    ) -> None:
        declarators = [
            child
            for child in node.named_children
            if child.type == "variable_declarator"
        ]
        if len(declarators) == 1:
            declarator = declarators[0]
            name_node = declarator.child_by_field_name("name")
            value = declarator.child_by_field_name("value")
            callable_node, wrapper = self._callable_value(value)
            if (
                name_node is not None
                and name_node.type in {"identifier", "property_identifier"}
                and callable_node is not None
            ):
                name = self._text(name_node)
                qualname = ".".join([*parents, name])
                is_async = self._is_async(callable_node)
                metadata = {
                    **self._metadata(boundary, node),
                    "async": is_async,
                    "declaration": node.children[0].type,
                    "wrapper": wrapper,
                }
                chunk = self._add(
                    node,
                    boundary,
                    self._function_kind(parent_kind, is_async, False),
                    name,
                    qualname,
                    parent_id,
                    metadata=metadata,
                )
                body = callable_node.child_by_field_name("body")
                if body is not None and body.type == "statement_block":
                    self._visit_scope(body, [*parents, name], chunk.id, "function")
                return
        if parent_kind != "module":
            return
        names = []
        for declarator in declarators:
            name_node = declarator.child_by_field_name("name")
            if name_node is not None:
                names.append(self._text(name_node))
        if not names:
            return
        name = ",".join(names)
        qualname = ".".join([*parents, name])
        kind = (
            "constant"
            if all(
                re.fullmatch(r"[A-Z][A-Z0-9_]*", value) is not None for value in names
            )
            else "assignment"
        )
        self._add(
            node,
            boundary,
            kind,
            name,
            qualname,
            parent_id,
            metadata={
                **self._metadata(boundary, node),
                "targets": names,
                "declaration": node.children[0].type,
            },
        )

    def _visit_type(
        self,
        node: Node,
        boundary: Node,
        parents: list[str],
        parent_id: str,
    ) -> None:
        name = self._name(node)
        if not name:
            return
        self._add(
            node,
            boundary,
            _TYPE_KINDS[node.type],
            name,
            ".".join([*parents, name]),
            parent_id,
            metadata=self._metadata(boundary, node),
        )

    def _visit_export(
        self,
        node: Node,
        parents: list[str],
        parent_id: str,
        parent_kind: str,
    ) -> bool:
        for child in node.named_children:
            if child.type in _FUNCTION_NODES:
                self._visit_function(child, node, parents, parent_id, parent_kind)
                return True
            if child.type in _CLASS_NODES:
                self._visit_class(child, node, parents, parent_id)
                return True
            if child.type in _VARIABLE_DECLARATIONS:
                self._visit_variable_declaration(
                    child, node, parents, parent_id, parent_kind
                )
                return True
            if child.type in _TYPE_KINDS:
                self._visit_type(child, node, parents, parent_id)
                return True
        if not re.match(r"^\s*export\s+default\b", self._text(node)):
            return False
        value = node.child_by_field_name("value")
        if value is None or value.type in {"identifier", "type_identifier"}:
            return False
        name = "default_export"
        self._add(
            node,
            node,
            "assignment",
            name,
            ".".join([*parents, name]),
            parent_id,
            metadata={
                "exported": True,
                "default_export": True,
                "targets": [name],
            },
        )
        return True

    def _visit_expression(
        self,
        node: Node,
        parents: list[str],
        parent_id: str,
    ) -> None:
        expression = node.named_children[0] if node.named_child_count else None
        if expression is None or expression.type == "string":
            return
        name = f"expression@{node.start_point.row + 1}"
        kind = "expression"
        metadata: dict[str, object] = {}
        if expression.type == "assignment_expression":
            target = expression.child_by_field_name("left")
            if target is not None:
                name = self._text(target)
                kind = "assignment"
                metadata["targets"] = [name]
        self._add(
            node,
            node,
            kind,
            name,
            ".".join([*parents, name]),
            parent_id,
            metadata=metadata,
        )

    def _visit_nested_blocks(
        self,
        node: Node,
        parents: list[str],
        parent_id: str,
        parent_kind: str,
    ) -> None:
        if node.type in _CALLABLE_NODES or node.type in _FUNCTION_NODES:
            return
        for child in node.named_children:
            if child.type == "statement_block":
                self._visit_scope(child, parents, parent_id, parent_kind)
            elif child.type not in _CALLABLE_NODES:
                self._visit_nested_blocks(child, parents, parent_id, parent_kind)

    def _visit_scope(
        self,
        container: Node,
        parents: list[str],
        parent_id: str,
        parent_kind: str,
    ) -> None:
        children = list(container.named_children)
        index = 0
        while index < len(children):
            node = children[index]
            if node.type == "import_statement" and parent_kind == "module":
                imports = [node]
                cursor = index + 1
                while cursor < len(children):
                    candidate = children[cursor]
                    if candidate.type == "comment":
                        cursor += 1
                        continue
                    if candidate.type != "import_statement":
                        break
                    imports.append(candidate)
                    cursor += 1
                first, last = imports[0], imports[-1]
                boundary_code = self.source_bytes[
                    first.start_byte : last.end_byte
                ].decode("utf-8")
                self._add(
                    first,
                    first,
                    "import_group",
                    None,
                    ".".join([*parents, f"imports@{first.start_point.row + 1}"]),
                    parent_id,
                    code=boundary_code,
                    metadata={"import_count": len(imports)},
                    end_line=self._end_line(last),
                )
                index = cursor
                continue
            if node.type in _FUNCTION_NODES:
                self._visit_function(node, node, parents, parent_id, parent_kind)
            elif node.type in _CLASS_NODES:
                self._visit_class(node, node, parents, parent_id)
            elif node.type in _VARIABLE_DECLARATIONS:
                self._visit_variable_declaration(
                    node, node, parents, parent_id, parent_kind
                )
            elif node.type in _TYPE_KINDS:
                self._visit_type(node, node, parents, parent_id)
            elif node.type == "export_statement":
                self._visit_export(node, parents, parent_id, parent_kind)
            elif node.type == "expression_statement" and parent_kind == "module":
                self._visit_expression(node, parents, parent_id)
            elif node.type != "comment":
                self._visit_nested_blocks(node, parents, parent_id, parent_kind)
            index += 1


def _syntax_errors(root: Node, relative: str) -> list[ParseError]:
    errors: list[ParseError] = []
    seen: set[tuple[str, int, int]] = set()

    def visit(node: Node) -> None:
        if node.type == "ERROR" or node.is_missing:
            line = node.start_point.row + 1
            column = node.start_point.column + 1
            message = (
                f"Missing {node.type}"
                if node.is_missing
                else "Tree-sitter syntax error"
            )
            key = (message, line, column)
            if key not in seen:
                seen.add(key)
                errors.append(ParseError(relative, message, line, column))
        for child in node.named_children:
            visit(child)

    visit(root)
    if not errors:
        errors.append(ParseError(relative, "Tree-sitter syntax error"))
    return errors


def parse_javascript_family(path: Path, repo_root: Path) -> ParseResult:
    relative = relative_repo_path(repo_root, path)
    raw = path.read_bytes()
    if b"\0" in raw:
        return ParseResult(skipped=True)
    try:
        source = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        return ParseResult(
            errors=[ParseError(relative, f"File is not valid UTF-8: {exc}")],
            skipped=True,
        )
    parser = Parser(_language_for(path.suffix.lower()))
    root = parser.parse(raw).root_node
    if root.has_error:
        return ParseResult(errors=_syntax_errors(root, relative))
    return ParseResult(
        chunks=JavaScriptChunker(path, repo_root, source, raw, root).run()
    )
