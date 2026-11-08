"""Bounded Python support context for retrieved symbols and explicit deliveries."""

import ast
import re
from pathlib import PurePosixPath


def support_records(repo, records, requirements, guard):
    output, covered, parsed = [], {}, {}

    def source(name):
        if name not in parsed:
            try:
                path = guard(name)
            except ValueError:
                # Optional support must obey exclusions without blocking an
                # otherwise eligible primary source excerpt.
                return None
            if (
                not path.is_file()
                or path.suffix != ".py"
                or path.stat().st_size > 512_000
            ):
                return None
            text = path.read_text()
            try:
                parsed[name] = (text.splitlines(), ast.parse(text))
            except SyntaxError:
                return None
        return parsed[name]

    def add(name, start, end, symbol, kind):
        data = source(name)
        if data is None or end - start > 160:
            return
        if any(a <= start and end <= b for a, b in covered.get(name, [])):
            return
        lines, _ = data
        code = "\n".join(lines[start - 1 : end])
        if len(code) > 8000 or sum(len(r["code"]) for r in output) + len(code) > 16000:
            return
        output.append(
            {
                "path": name,
                "lines": [start, end],
                "symbol": symbol,
                "kind": kind,
                "code": code,
            }
        )
        covered.setdefault(name, []).append((start, end))

    # Existing excerpt coverage prevents duplicate support text.
    for record in records:
        covered.setdefault(record["path"], []).append(tuple(record["lines"]))

    def constructor(name, cls, classes, seen):
        key = (name, cls.name)
        if key in seen or len(seen) >= 4:
            return
        seen.add(key)
        add(name, cls.lineno, cls.lineno, cls.name, "class_signature")
        init = next(
            (
                n
                for n in cls.body
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                and n.name == "__init__"
            ),
            None,
        )
        if init:
            add(
                name,
                init.lineno,
                init.end_lineno,
                cls.name + ".__init__",
                "constructor",
            )
        else:
            for base in cls.bases:
                if not isinstance(base, ast.Name):
                    continue
                if base.id in classes:
                    constructor(name, classes[base.id], classes, seen)
                    continue
                # Follow an explicit relative import once per ancestor; never
                # scan arbitrary installed packages or execute project imports.
                _, tree = source(name)
                for node in tree.body:
                    if not isinstance(node, ast.ImportFrom) or not node.level:
                        continue
                    for alias in node.names:
                        if (alias.asname or alias.name) != base.id:
                            continue
                        parent = PurePosixPath(name).parent
                        for _ in range(node.level - 1):
                            parent = parent.parent
                        module = parent.joinpath(*(node.module or "").split("."))
                        target = module.as_posix() + ".py"
                        imported = source(target)
                        if imported:
                            others = {
                                n.name: n
                                for n in imported[1].body
                                if isinstance(n, ast.ClassDef)
                            }
                            if alias.name in others:
                                constructor(target, others[alias.name], others, seen)

    for record in records[:5]:
        name = record["path"]
        data = source(name)
        if data is None:
            continue
        _lines, tree = data
        classes = {n.name: n for n in tree.body if isinstance(n, ast.ClassDef)}
        for cls in classes.values():
            if cls.lineno <= record["lines"][0] <= cls.end_lineno:
                constructor(name, cls, classes, set())
        # Imports make aliases and public entrypoints visible without unrelated functions.
        for node in tree.body:
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                add(name, node.lineno, node.end_lineno, "imports", "imports")
    for item in requirements["exports"]:
        data = source(item["path"])
        if data and len(data[0]) <= 160:
            add(
                item["path"],
                1,
                max(1, len(data[0])),
                item["module"],
                "requested_export",
            )
    for item in requirements["modules"]:
        # Empty new files are represented as delivery requirements, never fake evidence.
        parent = item["path"].rsplit("/", 1)[0] + "/__init__.py"
        data = source(parent)
        if data and len(data[0]) <= 160:
            add(parent, 1, max(1, len(data[0])), item["module"], "package_exports")
    return output


def focused_candidates(results, query):
    """Quick starts with one source area plus explicitly named targets.

    Generic lexical/semantic overlap is not evidence that an unrelated file is
    needed. The editor can request bounded reads if this initial focus is wrong.
    """
    from devstill.context import is_test_chunk, query_requests_tests

    pool = (
        results
        if query_requests_tests(query)
        else [r for r in results if not is_test_chunk(r.chunk)]
    )
    if not pool:
        return []
    primary = pool[0].chunk.path

    def named(result):
        chunk = result.chunk
        symbols = [chunk.qualname or chunk.name or ""]
        if "." in symbols[0]:
            symbols.append(symbols[0].rsplit(".", 1)[0])
        return chunk.path in query or any(
            symbol
            and re.search(r"(?<![\w.])" + re.escape(symbol) + r"(?![\w.])", query)
            for symbol in symbols
        )

    return [r for r in pool if r.chunk.path == primary or named(r)]
