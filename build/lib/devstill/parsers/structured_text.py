from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from devstill.hashing import hash_text
from devstill.models import ParseError, SourceChunk
from devstill.parsers.base import ParseResult
from devstill.paths import relative_repo_path

_MARKDOWN_HEADING = re.compile(r"^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$")
_ASCIIDOC_HEADING = re.compile(r"^(={1,6})\s+(.+?)\s*$")
_RST_UNDERLINE = re.compile(r"^\s*([=\-~^\"'`:+*#<>_])\1{2,}\s*$")
_SECTION_HEADER = re.compile(r"^\s*\[+([^\]]+)\]+\s*(?:[#;].*)?$")
_YAML_KEY = re.compile(r"^([A-Za-z0-9_.\-]+)\s*:")
_MAKE_TARGET = re.compile(r"^([^\s:#=][^:#=]*):(?:\s|$)")
_DOCKER_INSTRUCTION = re.compile(
    r"^\s*(FROM|RUN|CMD|LABEL|EXPOSE|ENV|ADD|COPY|ENTRYPOINT|VOLUME|USER|WORKDIR|ARG|ONBUILD|STOPSIGNAL|HEALTHCHECK|SHELL)\b",
    re.IGNORECASE,
)
_CONFIG_KEY = re.compile(r"^\s*([A-Za-z0-9_.\-]+)\s*(?:=|:)")


@dataclass(frozen=True, slots=True)
class _Section:
    start: int
    end: int
    name: str
    qualname: str | None = None


def _normalize_jsonc(source: str) -> str:
    characters = list(source)
    index = 0
    in_string = False
    escaped = False
    while index < len(characters):
        character = characters[index]
        if escaped:
            escaped = False
            index += 1
            continue
        if in_string and character == "\\":
            escaped = True
            index += 1
            continue
        if character == '"':
            in_string = not in_string
            index += 1
            continue
        if not in_string and character == "/" and index + 1 < len(characters):
            following = characters[index + 1]
            if following == "/":
                while index < len(characters) and characters[index] not in "\r\n":
                    characters[index] = " "
                    index += 1
                continue
            if following == "*":
                characters[index] = characters[index + 1] = " "
                index += 2
                closed = False
                while index + 1 < len(characters):
                    if characters[index] == "*" and characters[index + 1] == "/":
                        characters[index] = characters[index + 1] = " "
                        index += 2
                        closed = True
                        break
                    if characters[index] not in "\r\n":
                        characters[index] = " "
                    index += 1
                if not closed:
                    raise ValueError("unterminated block comment")
                continue
        index += 1

    normalized = "".join(characters)
    characters = list(normalized)
    in_string = False
    escaped = False
    for index, character in enumerate(characters):
        if escaped:
            escaped = False
            continue
        if in_string and character == "\\":
            escaped = True
            continue
        if character == '"':
            in_string = not in_string
            continue
        if in_string or character != ",":
            continue
        lookahead = index + 1
        while lookahead < len(characters) and characters[lookahead].isspace():
            lookahead += 1
        if lookahead < len(characters) and characters[lookahead] in "}]":
            characters[index] = " "
    return "".join(characters)


class _TextChunker:
    def __init__(self, path: Path, repo_root: Path, source: str, parser: str) -> None:
        self.path = path
        self.relative = relative_repo_path(repo_root, path)
        self.source = source
        self.lines = source.splitlines(keepends=True)
        self.parser = parser
        self.language = self._language()
        self.used_ids: dict[str, int] = {}

    def _language(self) -> str:
        name = self.path.name.casefold()
        suffix = self.path.suffix.casefold().lstrip(".")
        if name in {"dockerfile", ".dockerignore"} or name.startswith("dockerfile."):
            return "dockerfile"
        if name == "makefile" or name.startswith("makefile."):
            return "makefile"
        if name.startswith(".env"):
            return "dotenv"
        return suffix or ("markdown" if self.parser == "markup_text" else "text")

    def _unique_id(self, base: str) -> str:
        count = self.used_ids.get(base, 0) + 1
        self.used_ids[base] = count
        return base if count == 1 else f"{base}::{count}"

    def _code(self, start: int, end: int) -> str:
        return "".join(self.lines[start - 1 : end])

    def _split_points(self, start: int, end: int) -> list[int]:
        points: list[int] = []
        previous_blank = False
        for line_number in range(start, end + 1):
            blank = not self.lines[line_number - 1].strip()
            if previous_blank and not blank:
                points.append(line_number)
            previous_blank = blank
        return points

    def _chunk(
        self,
        *,
        kind: str,
        name: str | None,
        qualname: str | None,
        parent_id: str | None,
        start: int,
        end: int,
        searchable: bool = True,
    ) -> SourceChunk:
        code = self._code(start, end)
        base = f"{self.relative}::{qualname or name or start}::{kind}"
        return SourceChunk(
            id=self._unique_id(base),
            path=self.relative,
            filename=self.path.name,
            language=self.language,
            kind=kind,
            name=name,
            qualname=qualname,
            parent_id=parent_id,
            start_line=start,
            end_line=end,
            code=code,
            leading_comments=None,
            content_hash=hash_text(code),
            parser_metadata={
                "parser": self.parser,
                "content_category": (
                    "documentation"
                    if self.parser in {"markup_text", "prose_text"}
                    else "configuration"
                ),
                "searchable": searchable,
                "split_points": self._split_points(start, end),
            },
        )

    def run(self) -> list[SourceChunk]:
        end = max(1, len(self.source.splitlines()))
        parent = self._chunk(
            kind="document",
            name=self.path.name,
            qualname=None,
            parent_id=None,
            start=1,
            end=end,
            searchable=False,
        )
        if self.parser == "markup_text":
            sections = self._markup_sections(end)
            kind = "document_section"
        elif self.parser == "config_text":
            sections = self._config_sections(end)
            kind = "config_section"
        else:
            sections = self._prose_sections(end)
            kind = "document_section"
        return [
            parent,
            *[
                self._chunk(
                    kind=kind,
                    name=section.name,
                    qualname=section.qualname,
                    parent_id=parent.id,
                    start=section.start,
                    end=section.end,
                )
                for section in sections
                if self._code(section.start, section.end).strip()
            ],
        ]

    def _markup_sections(self, end: int) -> list[_Section]:
        headings: list[tuple[int, int, str]] = []
        fence: str | None = None
        for index, raw in enumerate(self.lines, start=1):
            stripped = raw.lstrip()
            marker = re.match(r"(`{3,}|~{3,})", stripped)
            if marker:
                current = marker.group(1)[0]
                if fence is None:
                    fence = current
                elif fence == current:
                    fence = None
                continue
            if fence:
                continue
            match = _MARKDOWN_HEADING.match(raw.rstrip("\r\n"))
            if match:
                headings.append((index, len(match.group(1)), match.group(2).strip()))
                continue
            match = _ASCIIDOC_HEADING.match(raw.rstrip("\r\n"))
            if match and self.path.suffix.casefold() in {".adoc", ".asciidoc"}:
                headings.append((index, len(match.group(1)), match.group(2).strip()))
                continue
            if index < len(self.lines) and _RST_UNDERLINE.match(
                self.lines[index].rstrip("\r\n")
            ):
                title = raw.strip()
                if title:
                    marker_char = self.lines[index].strip()[0]
                    level = {"=": 1, "-": 2, "~": 3}.get(marker_char, 4)
                    headings.append((index, level, title))
        if not headings:
            return [_Section(1, end, self.path.name)]
        sections: list[_Section] = []
        if headings[0][0] > 1:
            sections.append(_Section(1, headings[0][0] - 1, "preamble"))
        stack: list[tuple[int, str]] = []
        for ordinal, (start, level, title) in enumerate(headings):
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, title))
            section_end = (
                headings[ordinal + 1][0] - 1 if ordinal + 1 < len(headings) else end
            )
            sections.append(
                _Section(
                    start, section_end, title, " / ".join(value for _, value in stack)
                )
            )
        return sections

    def _prose_sections(self, end: int) -> list[_Section]:
        boundaries = [1]
        names: dict[int, str] = {}
        for index, raw in enumerate(self.lines, start=1):
            text = raw.strip()
            rst_heading = (
                index < len(self.lines)
                and text
                and _RST_UNDERLINE.match(self.lines[index].rstrip("\r\n"))
            )
            uppercase_heading = (
                text
                and len(text) <= 100
                and text == text.upper()
                and any(character.isalpha() for character in text)
            )
            if rst_heading or uppercase_heading:
                boundaries.append(index)
                names[index] = text
        starts = sorted(set(boundaries))
        if len(starts) == 1:
            return [_Section(1, end, self.path.name)]
        return [
            _Section(
                start,
                starts[index + 1] - 1 if index + 1 < len(starts) else end,
                names.get(start, "preamble"),
            )
            for index, start in enumerate(starts)
        ]

    def _config_sections(self, end: int) -> list[_Section]:
        name = self.path.name.casefold()
        suffix = self.path.suffix.casefold()
        if suffix in {".json", ".jsonc"}:
            return self._json_sections(end)
        starts: list[tuple[int, str]] = []
        for index, raw in enumerate(self.lines, start=1):
            text = raw.rstrip("\r\n")
            match = _SECTION_HEADER.match(text)
            if match:
                starts.append((index, match.group(1).strip()))
                continue
            if suffix in {".yaml", ".yml"}:
                match = _YAML_KEY.match(text)
                if match:
                    starts.append((index, match.group(1)))
            elif name == "dockerfile" or name.startswith("dockerfile."):
                match = _DOCKER_INSTRUCTION.match(text)
                if match:
                    starts.append((index, f"{match.group(1).upper()}@{index}"))
            elif name == "makefile" or name.startswith("makefile."):
                match = _MAKE_TARGET.match(text)
                if match:
                    starts.append((index, match.group(1).strip()))
        if not starts:
            label = self._first_config_label() or self.path.name
            return [_Section(1, end, label)]
        sections: list[_Section] = []
        if starts[0][0] > 1:
            sections.append(_Section(1, starts[0][0] - 1, "preamble"))
        sections.extend(
            _Section(
                start,
                starts[index + 1][0] - 1 if index + 1 < len(starts) else end,
                label,
            )
            for index, (start, label) in enumerate(starts)
        )
        return sections

    def _json_sections(self, end: int) -> list[_Section]:
        starts: list[tuple[int, str]] = []
        depth = 0
        in_string = False
        escaped = False
        normalized_lines = _normalize_jsonc(self.source).splitlines(keepends=True)
        for line_number, (raw, normalized) in enumerate(
            zip(self.lines, normalized_lines, strict=True), start=1
        ):
            depth_at_line = depth
            for character in normalized:
                if escaped:
                    escaped = False
                    continue
                if in_string and character == "\\":
                    escaped = True
                    continue
                if character == '"':
                    in_string = not in_string
                    continue
                if in_string:
                    continue
                if character in "[{":
                    depth += 1
                elif character in "]}":
                    depth = max(0, depth - 1)
            if depth_at_line == 1:
                match = re.match(r'^\s*"((?:[^"\\]|\\.)+)"\s*:', raw)
                if match:
                    starts.append((line_number, match.group(1)))
        if not starts:
            return [_Section(1, end, self.path.name)]
        return [
            _Section(
                start,
                starts[index + 1][0] - 1 if index + 1 < len(starts) else end,
                label,
            )
            for index, (start, label) in enumerate(starts)
        ]

    def _first_config_label(self) -> str | None:
        for raw in self.lines:
            stripped = raw.strip()
            if not stripped or stripped.startswith(("#", ";", "//")):
                continue
            match = _CONFIG_KEY.match(raw)
            return match.group(1) if match else stripped[:80]
        return None


def parse_structured_text(
    path: Path,
    repo_root: Path,
    parser: str,
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
    if not source:
        return ParseResult()
    if parser == "config_text" and path.suffix.casefold() in {".json", ".jsonc"}:
        try:
            json.loads(source.lstrip("\ufeff"))
        except json.JSONDecodeError as exc:
            jsonc_conventions = bool(
                re.search(r"(?:^|\s)//|/\*|,\s*[}\]]", source, re.MULTILINE)
            )
            if path.suffix.casefold() != ".jsonc" and not jsonc_conventions:
                return ParseResult(
                    errors=[
                        ParseError(
                            relative,
                            f"Invalid JSON: {exc.msg}",
                            exc.lineno,
                            exc.colno,
                        )
                    ]
                )
            try:
                json.loads(_normalize_jsonc(source))
            except (json.JSONDecodeError, ValueError) as jsonc_exc:
                message = (
                    jsonc_exc.msg
                    if isinstance(jsonc_exc, json.JSONDecodeError)
                    else str(jsonc_exc)
                )
                line = getattr(jsonc_exc, "lineno", exc.lineno)
                column = getattr(jsonc_exc, "colno", exc.colno)
                return ParseResult(
                    errors=[
                        ParseError(
                            relative,
                            f"Invalid JSON/JSONC: {message}",
                            line,
                            column,
                        )
                    ]
                )
    return ParseResult(chunks=_TextChunker(path, repo_root, source, parser).run())
