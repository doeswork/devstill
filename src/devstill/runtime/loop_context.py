"""Compact, faithful first-turn context for the Quick agent loop.

Retrieved records are rendered as whole-line excerpts of the current workspace
files (merged per file), each with the module import path and a short outline
of the file, plus pointers to related tests. Outlines name the real classes and
functions so verification code does not have to guess APIs.
"""

from __future__ import annotations

import ast
import difflib
import re
from pathlib import Path, PurePosixPath

FENCES = {
    ".py": "python",
    ".js": "javascript",
    ".ts": "typescript",
    ".md": "markdown",
    ".toml": "toml",
    ".json": "json",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".rst": "rst",
    ".cfg": "ini",
    ".ini": "ini",
    ".sh": "bash",
    ".rs": "rust",
}
SUPPORT_KINDS = {
    "imports",
    "type_checking_import",
    "dependency_class",
    "class_signature",
    "package_exports",
    "constructor",
    "base_class",
}


def module_name(path: str) -> str | None:
    parts = list(PurePosixPath(path).with_suffix("").parts)
    if not path.endswith(".py") or not parts:
        return None
    if parts[0] in {"src", "lib"}:
        parts = parts[1:]
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    if not parts or not all(p.isidentifier() for p in parts):
        return None
    return ".".join(parts)


def outline(text: str, limit: int = 40) -> str:
    """One-line-per-symbol outline of a Python module (line numbers included)."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return ""
    items = []
    names = []
    for node in tree.body:
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and names
            and names[-1] == node.name
        ):
            # Collapse @overload stubs into the implementation's line.
            items.pop()
            names.pop()
        names.append(getattr(node, "name", None))
        if isinstance(node, ast.ClassDef):
            methods = [
                n.name
                for n in node.body
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            ]
            shown = ", ".join(methods[:14]) + (", ..." if len(methods) > 14 else "")
            items.append(
                f"L{node.lineno} class {node.name}" + (f": {shown}" if shown else "")
            )
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            items.append(f"L{node.lineno} def {node.name}")
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            names = [t.id for t in targets if isinstance(t, ast.Name)]
            if names and (names[0].isupper() or names[0] == "__all__"):
                items.append(f"L{node.lineno} {names[0]} = ...")
    if len(items) > limit:
        items = items[:limit] + [f"... {len(items) - limit} more"]
    return "\n".join(items)


def _merge(intervals, gap=15):
    merged = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1] + gap + 1:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return merged


def related_tests(work: Path, symbols, limit=3, max_lines=8):
    """Test files mentioning the primary symbols plus a few usage lines."""
    names = sorted({s for s in symbols if s and len(s) >= 3 and s.isidentifier()})
    if not names:
        return [], []
    pattern = re.compile(r"\b(" + "|".join(map(re.escape, names)) + r")\b")
    calls = re.compile(r"\b(" + "|".join(map(re.escape, names)) + r")\s*\(")
    counts, examples = [], []
    for directory in ("tests", "test"):
        root = work / directory
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            try:
                text = path.read_text(errors="ignore")
            except OSError:
                continue
            hits = len(pattern.findall(text))
            if not hits:
                continue
            name = path.relative_to(work).as_posix()
            counts.append((hits, name))
            for number, line in enumerate(text.split("\n"), 1):
                stripped = line.strip()
                if calls.search(stripped) and not stripped.startswith(("def ", "#")):
                    examples.append((name, number, stripped[:160]))
    counts.sort(key=lambda item: (-item[0], item[1]))
    top = {name for _, name in counts[:limit]}
    examples = [e for e in examples if e[0] in top][:max_lines]
    return counts[:limit], examples


def primary_symbols(records):
    """Distinctive names of the primary (non-support) excerpts."""
    names = []
    for record in records:
        if record.get("kind") in SUPPORT_KINDS or record.get("support"):
            continue
        if not record.get("symbol"):
            continue
        parts = str(record["symbol"]).split(".")
        # Owner classes are distinctive; short method names are not.
        names.extend(p for p in parts[:-1] if p[:1].isupper())
        if len(parts[-1]) >= 5 or parts[-1][:1].isupper():
            names.append(parts[-1])
    return names


def is_test_path(name: str) -> bool:
    path = PurePosixPath(name)
    return (
        any(part in {"tests", "test"} for part in path.parts[:-1])
        or path.name.startswith("test_")
        or path.name.endswith("_test.py")
    )


def changed_symbols(new_text: str, old_text: str) -> set[str]:
    """Names of functions/classes in new_text whose lines differ from old_text."""
    old_lines, new_lines = old_text.split("\n"), new_text.split("\n")
    changed = set()
    matcher = difflib.SequenceMatcher(None, old_lines, new_lines, autojunk=False)
    for tag, _, _, start, end in matcher.get_opcodes():
        if tag != "equal":
            changed.update(range(start + 1, max(end, start + 1) + 1))
    try:
        tree = ast.parse(new_text)
    except SyntaxError:
        return set()
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            last = node.end_lineno or node.lineno
            if any(node.lineno <= line <= last for line in changed):
                names.add(node.name)
    return {n for n in names if len(n) >= 3 and not n.startswith("__")}


def guard_test_files(work: Path, changed, original, limit=2):
    """Existing test files that exercise the functions/classes just changed."""
    names = set()
    for name in changed:
        if not name.endswith(".py") or is_test_path(name):
            continue
        try:
            new_text = (work / name).read_text()
        except (OSError, UnicodeDecodeError):
            continue
        raw = original(name)
        old_text = raw.decode("utf-8", errors="replace") if raw else ""
        names |= changed_symbols(new_text, old_text)
    if not names:
        return []
    counts, _ = related_tests(work, names)
    if not counts:
        return []
    top = counts[0][0]
    return [name for hits, name in counts[:limit] if hits * 2 >= top]


def build_sections(work: Path, records, budget_chars=16000):
    """Render records as merged whole-line excerpts within a character budget."""
    by_path: dict[str, list] = {}
    order = []
    for record in records:
        path = record["path"]
        if path not in by_path:
            by_path[path] = []
            order.append(path)
        start, end = record["lines"]
        by_path[path].append(
            (int(start), int(end), record.get("kind") in SUPPORT_KINDS)
        )

    sections = []
    used = 0
    for index, path in enumerate(order):
        target = work / path
        try:
            text = target.read_text()
        except (OSError, UnicodeDecodeError):
            continue
        lines = text.split("\n")
        if lines and lines[-1] == "":
            lines = lines[:-1]
        total = len(lines)
        intervals = _merge([(s, e) for s, e, _ in by_path[path]])
        only_support = all(support for _, _, support in by_path[path])
        complete = (
            len(intervals) == 1 and intervals[0][0] <= 1 and intervals[0][1] >= total
        )
        header = f"### {path}"
        module = module_name(path)
        if module:
            header += f" (import as {module})"
        header += f" — {total} lines"
        body = []
        if path.endswith(".py") and not complete and not only_support and index < 3:
            summary = outline(text)
            if summary:
                body.append("Outline:\n" + summary)
        fence = FENCES.get(PurePosixPath(path).suffix, "")
        for start, end in intervals:
            start, end = max(1, start), min(total, end)
            if start > end:
                continue
            code = "\n".join(lines[start - 1 : end])
            ticks = "```"
            while ticks in code:
                ticks += "`"
            label = "whole file" if complete else f"lines {start}-{end}"
            body.append(f"{label}:\n{ticks}{fence}\n{code}\n{ticks}")
        section = header + "\n" + "\n".join(body)
        if used + len(section) > budget_chars:
            if not sections:
                # Always keep the primary excerpt, trimmed to the budget.
                section = (
                    section[: max(2000, budget_chars)]
                    + "\n[truncated; read the file for more]"
                )
                sections.append(section)
                used += len(section)
            continue
        sections.append(section)
        used += len(section)
    tests, examples = related_tests(work, primary_symbols(records))
    if tests:
        text = "Related tests: " + ", ".join(
            f"{name} ({hits} mentions)" for hits, name in tests
        )
        if examples:
            text += "\nExisting usage in tests:\n" + "\n".join(
                f"{name}:{number}: {line}" for name, number, line in examples
            )
        sections.append(text)
    return sections
