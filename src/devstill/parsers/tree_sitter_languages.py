from __future__ import annotations

import importlib
import re
from pathlib import Path

from tree_sitter import Language, Node, Parser

from devstill.hashing import hash_text
from devstill.models import ParseError, SourceChunk
from devstill.parsers.base import ParseResult
from devstill.paths import relative_repo_path

_GRAMMAR_MODULES = {
    "go": "tree_sitter_go",
    "rust": "tree_sitter_rust",
    "ruby": "tree_sitter_ruby",
    "sql": "tree_sitter_sql",
    "bash": "tree_sitter_bash",
    "html": "tree_sitter_html",
    "css": "tree_sitter_css",
    "java": "tree_sitter_java",
}
_COMMENT_PREFIXES = ("//", "#", "--", "/*", "*", "<!--")


def _language(language: str) -> Language:
    module = importlib.import_module(_GRAMMAR_MODULES[language])
    return Language(module.language())


def _first_error(node: Node) -> Node | None:
    if node.type in {"ERROR", "MISSING"} or node.is_missing:
        return node
    for child in node.children:
        if child.has_error or child.is_missing:
            found = _first_error(child)
            if found is not None:
                return found
    return None


def _html_structure_error(node: Node, source: bytes) -> Node | None:
    void_tags = {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }
    optional_end_tags = {
        "body",
        "colgroup",
        "dd",
        "dt",
        "head",
        "html",
        "li",
        "option",
        "p",
        "tbody",
        "td",
        "tfoot",
        "th",
        "thead",
        "tr",
    }
    if node.type == "element":
        start = next(
            (child for child in node.named_children if child.type == "start_tag"),
            None,
        )
        end = next(
            (child for child in node.named_children if child.type == "end_tag"),
            None,
        )
        if start is not None:
            start_name = next(
                (child for child in start.named_children if child.type == "tag_name"),
                None,
            )
            tag = (
                source[start_name.start_byte : start_name.end_byte]
                .decode("utf-8")
                .casefold()
                if start_name is not None
                else ""
            )
            if end is None and tag not in void_tags | optional_end_tags:
                return node
            if end is not None:
                end_name = next(
                    (child for child in end.named_children if child.type == "tag_name"),
                    None,
                )
                closing = (
                    source[end_name.start_byte : end_name.end_byte]
                    .decode("utf-8")
                    .casefold()
                    if end_name is not None
                    else ""
                )
                if tag != closing:
                    return node
    for child in node.named_children:
        found = _html_structure_error(child, source)
        if found is not None:
            return found
    return None


class TreeSitterChunker:
    def __init__(
        self,
        path: Path,
        repo_root: Path,
        source: str,
        source_bytes: bytes,
        root: Node,
        language: str,
    ) -> None:
        self.path = path
        self.relative = relative_repo_path(repo_root, path)
        self.source = source
        self.source_bytes = source_bytes
        self.lines = source.splitlines(keepends=True)
        self.root = root
        self.language = language
        self.chunks: list[SourceChunk] = []
        self.ids: dict[str, int] = {}

    def run(self) -> list[SourceChunk]:
        module = self.add(
            self.root,
            "module",
            self.path.stem,
            self.path.stem,
            None,
            code=self.source,
            metadata={"searchable": False},
        )
        visitor = getattr(self, f"visit_{self.language}")
        visitor(self.root, [], module.id)
        return self.chunks

    def text(self, node: Node | None) -> str:
        if node is None:
            return ""
        return self.source_bytes[node.start_byte : node.end_byte].decode("utf-8")

    @staticmethod
    def end_line(node: Node) -> int:
        if node.end_point.column == 0 and node.end_point.row > node.start_point.row:
            return node.end_point.row
        return node.end_point.row + 1

    def unique_id(self, base: str) -> str:
        count = self.ids.get(base, 0) + 1
        self.ids[base] = count
        return base if count == 1 else f"{base}::{count}"

    def leading_comments(self, node: Node) -> str | None:
        cursor = node.start_point.row - 1
        indentation = node.start_point.column
        collected: list[str] = []
        while cursor >= 0:
            raw = self.lines[cursor].rstrip("\r\n")
            stripped = raw.lstrip()
            if not stripped:
                if collected:
                    break
                cursor -= 1
                continue
            if len(raw) - len(stripped) != indentation:
                break
            if not stripped.startswith(_COMMENT_PREFIXES):
                break
            collected.append(raw)
            cursor -= 1
        collected.reverse()
        return "\n".join(collected) or None

    def add(
        self,
        node: Node,
        kind: str,
        name: str | None,
        qualname: str | None,
        parent_id: str | None,
        *,
        boundary: Node | None = None,
        start_node: Node | None = None,
        code: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> SourceChunk:
        boundary = boundary or node
        first = start_node or boundary
        chunk_code = (
            code
            if code is not None
            else self.source_bytes[first.start_byte : boundary.end_byte].decode("utf-8")
        )
        start_line = first.start_point.row + 1
        end_line = self.end_line(boundary)
        body = node.child_by_field_name("body")
        split_points = (
            [child.start_point.row + 1 for child in body.named_children]
            if body is not None
            else []
        )
        chunk = SourceChunk(
            id=self.unique_id(
                f"{self.relative}::{qualname or name or start_line}::{kind}"
            ),
            path=self.relative,
            filename=self.path.name,
            language=self.language,
            kind=kind,
            name=name,
            qualname=qualname,
            parent_id=parent_id,
            start_line=start_line,
            end_line=end_line,
            code=chunk_code,
            leading_comments=self.leading_comments(first),
            content_hash=hash_text(chunk_code),
            parser_metadata={
                "parser": "tree_sitter",
                "grammar": self.language,
                "node_type": node.type,
                "split_points": split_points,
                **(metadata or {}),
            },
        )
        self.chunks.append(chunk)
        return chunk

    def name(self, node: Node, *fields: str) -> str | None:
        for field in (*fields, "name"):
            value = node.child_by_field_name(field)
            if value is not None:
                return self.text(value)
        for child in node.named_children:
            if child.type in {
                "identifier",
                "type_identifier",
                "constant",
                "simple_symbol",
            }:
                return self.text(child)
        return None

    def descendant_text(
        self,
        node: Node | None,
        node_types: set[str],
    ) -> str | None:
        if node is None:
            return None
        if node.type in node_types:
            return self.text(node)
        for child in node.named_children:
            value = self.descendant_text(child, node_types)
            if value:
                return value
        return None

    @staticmethod
    def qualify(parents: list[str], name: str) -> str:
        return ".".join([*parents, name])

    def add_named(
        self,
        node: Node,
        kind: str,
        parents: list[str],
        parent_id: str,
        *,
        name: str | None = None,
        boundary: Node | None = None,
        start_node: Node | None = None,
        metadata: dict[str, object] | None = None,
    ) -> SourceChunk | None:
        value = name or self.name(node)
        if not value:
            return None
        return self.add(
            node,
            kind,
            value,
            self.qualify(parents, value),
            parent_id,
            boundary=boundary,
            start_node=start_node,
            metadata=metadata,
        )

    # Go
    def visit_go(self, container: Node, parents: list[str], parent_id: str) -> None:
        for node in container.named_children:
            if node.type == "import_declaration":
                self.add(
                    node,
                    "import_group",
                    None,
                    self.qualify(parents, f"imports@{node.start_point.row + 1}"),
                    parent_id,
                )
            elif node.type == "type_declaration":
                spec = next(
                    (
                        child
                        for child in node.named_children
                        if child.type == "type_spec"
                    ),
                    None,
                )
                if spec is None:
                    continue
                type_node = spec.child_by_field_name("type")
                kind = {
                    "struct_type": "struct",
                    "interface_type": "interface",
                }.get(type_node.type if type_node else "", "type")
                self.add_named(node, kind, parents, parent_id, name=self.name(spec))
            elif node.type in {"function_declaration", "method_declaration"}:
                name = self.name(node)
                method_parents = parents
                kind = "function"
                if node.type == "method_declaration":
                    kind = "method"
                    receiver = node.child_by_field_name("receiver")
                    declaration = (
                        next(iter(receiver.named_children), None)
                        if receiver is not None
                        else None
                    )
                    receiver_type = (
                        declaration.child_by_field_name("type")
                        if declaration is not None
                        else None
                    )
                    owner = self.descendant_text(
                        receiver_type,
                        {"type_identifier"},
                    )
                    if owner:
                        method_parents = [*parents, owner]
                self.add_named(node, kind, method_parents, parent_id, name=name)
            elif node.type in {"const_declaration", "var_declaration"}:
                names = re.findall(
                    r"\b[A-Za-z_]\w*\b",
                    self.text(node).split("=", 1)[0],
                )[1:]
                name = ",".join(names) or f"{node.type}@{node.start_point.row + 1}"
                self.add_named(
                    node,
                    "constant" if node.type.startswith("const") else "variable",
                    parents,
                    parent_id,
                    name=name,
                )

    # Rust
    def visit_rust(self, container: Node, parents: list[str], parent_id: str) -> None:
        kinds = {
            "use_declaration": "use",
            "mod_item": "module",
            "struct_item": "struct",
            "enum_item": "enum",
            "trait_item": "trait",
            "type_item": "type",
            "const_item": "constant",
            "static_item": "static",
            "macro_definition": "macro",
            "macro_invocation": "macro",
        }
        pending_attributes: list[Node] = []
        for node in container.named_children:
            if node.type in {"attribute_item", "inner_attribute_item"}:
                pending_attributes.append(node)
                continue
            start_node = pending_attributes[0] if pending_attributes else None
            metadata = (
                {"attributes": [self.text(value) for value in pending_attributes]}
                if pending_attributes
                else None
            )
            pending_attributes = []
            if node.type == "impl_item":
                owner = self.text(node.child_by_field_name("type")) or "impl"
                chunk = self.add_named(
                    node,
                    "impl",
                    parents,
                    parent_id,
                    name=owner,
                    start_node=start_node,
                    metadata=metadata,
                )
                body = node.child_by_field_name("body")
                if chunk is not None and body is not None:
                    method_attributes: list[Node] = []
                    for child in body.named_children:
                        if child.type in {"attribute_item", "inner_attribute_item"}:
                            method_attributes.append(child)
                            continue
                        if child.type == "function_item":
                            self.add_named(
                                child,
                                "method",
                                [*parents, owner],
                                chunk.id,
                                start_node=(
                                    method_attributes[0] if method_attributes else None
                                ),
                                metadata=(
                                    {
                                        "attributes": [
                                            self.text(value)
                                            for value in method_attributes
                                        ]
                                    }
                                    if method_attributes
                                    else None
                                ),
                            )
                        method_attributes = []
                continue
            if node.type == "function_item":
                self.add_named(
                    node,
                    "function",
                    parents,
                    parent_id,
                    start_node=start_node,
                    metadata=metadata,
                )
                continue
            kind = kinds.get(node.type)
            if kind is None:
                continue
            if node.type == "use_declaration":
                self.add(
                    node,
                    kind,
                    None,
                    self.qualify(parents, f"use@{node.start_point.row + 1}"),
                    parent_id,
                    start_node=start_node,
                    metadata=metadata,
                )
                continue
            chunk = self.add_named(
                node,
                kind,
                parents,
                parent_id,
                start_node=start_node,
                metadata=metadata,
            )
            if node.type in {"mod_item", "trait_item"} and chunk is not None:
                body = node.child_by_field_name("body")
                if body is not None:
                    child_parents = [*parents, chunk.name or ""]
                    self.visit_rust(body, child_parents, chunk.id)
                    if node.type == "trait_item":
                        for child in body.named_children:
                            if child.type == "function_signature_item":
                                self.add_named(
                                    child,
                                    "method",
                                    child_parents,
                                    chunk.id,
                                )

    # Ruby
    def visit_ruby(self, container: Node, parents: list[str], parent_id: str) -> None:
        for node in container.named_children:
            if node.type == "call" and re.match(
                r"^\s*(?:require|require_relative|load)\b", self.text(node)
            ):
                self.add(
                    node,
                    "require",
                    None,
                    self.qualify(parents, f"require@{node.start_point.row + 1}"),
                    parent_id,
                )
                continue
            if node.type in {"module", "class"}:
                chunk = self.add_named(node, node.type, parents, parent_id)
                body = node.child_by_field_name("body")
                if chunk is not None and body is not None:
                    self.visit_ruby(body, [*parents, chunk.name or ""], chunk.id)
                continue
            if node.type in {"method", "singleton_method"}:
                name = self.name(node, "method")
                self.add_named(
                    node,
                    "singleton_method" if node.type == "singleton_method" else "method",
                    parents,
                    parent_id,
                    name=name,
                )
                continue
            if node.type in {"assignment", "operator_assignment"}:
                left = node.child_by_field_name("left")
                right = node.child_by_field_name("right")
                name = self.text(left)
                value = self.text(right)
                if not name:
                    continue
                if "lambda" in value or re.search(r"(?:->|proc)\s*(?:\(|\{|\b)", value):
                    kind = "lambda"
                elif name.split("::")[-1].isupper():
                    kind = "constant"
                else:
                    continue
                self.add_named(node, kind, parents, parent_id, name=name)

    # SQL
    def visit_sql(self, container: Node, parents: list[str], parent_id: str) -> None:
        del parents
        for node in container.named_children:
            text = self.text(node)
            normalized = re.sub(
                r"^\s*(?:--[^\n]*\n|/\*.*?\*/\s*)*",
                "",
                text,
                flags=re.DOTALL,
            )
            match = re.match(
                r"(?is)^\s*create\s+(?:or\s+replace\s+)?"
                r"(table|view|index|function|procedure)\s+(?:if\s+not\s+exists\s+)?"
                r"([`\"\[\]\w.]+)",
                normalized,
            )
            if match:
                kind = match.group(1).casefold()
                name = match.group(2).strip('`"[]')
            else:
                command = re.match(r"(?is)^\s*([a-z]+)", normalized)
                keyword = command.group(1).casefold() if command else "statement"
                kind = (
                    "query"
                    if keyword in {"select", "insert", "update", "delete", "with"}
                    else "ddl"
                )
                name = f"{keyword}@{node.start_point.row + 1}"
            self.add(node, kind, name, name, parent_id)

    # Bash
    def visit_bash(self, container: Node, parents: list[str], parent_id: str) -> None:
        for node in container.named_children:
            if node.type == "function_definition":
                self.add_named(node, "function", parents, parent_id)
            elif node.type in {
                "variable_assignment",
                "declaration_command",
                "unset_command",
            }:
                text = self.text(node)
                match = re.search(r"\b([A-Za-z_]\w*)=", text)
                name = (
                    match.group(1)
                    if match
                    else f"assignment@{node.start_point.row + 1}"
                )
                self.add_named(node, "assignment", parents, parent_id, name=name)
            elif node.type not in {"comment"}:
                name = f"commands@{node.start_point.row + 1}"
                self.add_named(node, "command_group", parents, parent_id, name=name)

    # HTML
    def _html_tag(self, node: Node) -> tuple[str | None, str | None]:
        start = next(
            (
                child
                for child in node.named_children
                if child.type in {"start_tag", "self_closing_tag"}
            ),
            None,
        )
        if start is None:
            return None, None
        tag_node = next(
            (child for child in start.named_children if child.type == "tag_name"),
            None,
        )
        tag = self.text(tag_node).casefold() or None
        match = re.search(r"""\bid\s*=\s*["']([^"']+)["']""", self.text(start))
        return tag, match.group(1) if match else None

    def visit_html(self, container: Node, parents: list[str], parent_id: str) -> None:
        semantic = {
            "article",
            "aside",
            "body",
            "footer",
            "form",
            "head",
            "header",
            "main",
            "nav",
            "section",
            "template",
        }
        for node in container.named_children:
            if node.type not in {"element", "script_element", "style_element"}:
                self.visit_html(node, parents, parent_id)
                continue
            tag, element_id = self._html_tag(node)
            is_top = container.type in {"document", "fragment"}
            selected = bool(element_id or tag in semantic or is_top)
            next_parents = parents
            next_parent = parent_id
            if selected and tag:
                name = element_id or f"{tag}@{node.start_point.row + 1}"
                kind = "template" if tag == "template" else tag
                chunk = self.add_named(
                    node,
                    kind,
                    parents,
                    parent_id,
                    name=name,
                    metadata={"tag": tag, "element_id": element_id},
                )
                if chunk is not None:
                    next_parents = [*parents, name]
                    next_parent = chunk.id
            self.visit_html(node, next_parents, next_parent)

    # CSS
    def visit_css(self, container: Node, parents: list[str], parent_id: str) -> None:
        del parents
        for node in container.named_children:
            if node.type == "rule_set":
                selector = node.child_by_field_name("selectors")
                if selector is None and node.named_children:
                    selector = node.named_children[0]
                name = self.text(selector).strip() or f"rule@{node.start_point.row + 1}"
                self.add(node, "selector", name, name, parent_id)
            elif node.type != "comment":
                text = self.text(node).lstrip()
                match = re.match(r"@([\w-]+)(?:\s+([^{;]+))?", text)
                directive = match.group(1) if match else node.type
                detail = match.group(2).strip() if match and match.group(2) else ""
                kind = "keyframes" if "keyframes" in directive else "at_rule"
                name = f"@{directive}{(' ' + detail) if detail else ''}"
                self.add(node, kind, name, name, parent_id)

    # Java
    def visit_java(self, container: Node, parents: list[str], parent_id: str) -> None:
        type_kinds = {
            "class_declaration": "class",
            "interface_declaration": "interface",
            "record_declaration": "record",
            "enum_declaration": "enum",
            "annotation_type_declaration": "annotation",
        }
        for node in container.named_children:
            if node.type == "package_declaration":
                name = re.sub(r"^\s*package\s+|;\s*$", "", self.text(node))
                self.add_named(node, "package", parents, parent_id, name=name)
            elif node.type == "import_declaration":
                self.add(
                    node,
                    "import",
                    None,
                    self.qualify(parents, f"import@{node.start_point.row + 1}"),
                    parent_id,
                )
            elif node.type in type_kinds:
                chunk = self.add_named(node, type_kinds[node.type], parents, parent_id)
                body = node.child_by_field_name("body")
                if chunk is not None and body is not None:
                    self.visit_java(body, [*parents, chunk.name or ""], chunk.id)
            elif node.type in {"method_declaration", "constructor_declaration"}:
                self.add_named(
                    node,
                    "constructor"
                    if node.type == "constructor_declaration"
                    else "method",
                    parents,
                    parent_id,
                )
            elif node.type in {"field_declaration", "constant_declaration"}:
                declarators = [
                    child
                    for child in node.named_children
                    if child.type == "variable_declarator"
                ]
                for declarator in declarators:
                    name = self.name(declarator)
                    if not name:
                        continue
                    self.add_named(
                        node,
                        "field",
                        parents,
                        parent_id,
                        name=name,
                    )


def parse_tree_sitter_language(
    path: Path,
    repo_root: Path,
    language: str,
) -> ParseResult:
    relative = relative_repo_path(repo_root, path)
    raw = path.read_bytes()
    try:
        source = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        return ParseResult(
            errors=[ParseError(relative, f"File is not valid UTF-8: {exc}")],
            skipped=True,
        )
    tree = Parser(_language(language)).parse(raw)
    structure_error = (
        _html_structure_error(tree.root_node, raw) if language == "html" else None
    )
    if tree.root_node.has_error or structure_error is not None:
        error = _first_error(tree.root_node) or structure_error
        line = error.start_point.row + 1 if error is not None else None
        column = error.start_point.column + 1 if error is not None else None
        node_type = error.type if error is not None else "ERROR"
        return ParseResult(
            errors=[
                ParseError(
                    relative,
                    f"Tree-sitter {language} syntax error ({node_type})",
                    line,
                    column,
                )
            ]
        )
    chunks = TreeSitterChunker(
        path,
        repo_root,
        source,
        raw,
        tree.root_node,
        language,
    ).run()
    return ParseResult(chunks=chunks)
