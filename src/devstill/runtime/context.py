"""Bounded Python support context for retrieved symbols and explicit deliveries."""

import ast
import re
from pathlib import PurePosixPath


def support_records(repo, records, requirements, guard):
    output, covered, parsed = [], {}, {}
    dependencies = set()

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

    def add(name, start, end, symbol, kind, import_hint=None):
        data = source(name)
        if data is None or end - start > 160:
            return
        if any(a <= start and end <= b for a, b in covered.get(name, [])):
            return
        lines, _ = data
        code = "\n".join(lines[start - 1 : end])
        size = len(code) + len(import_hint or "")
        if (
            size > 8000
            or sum(len(r["code"]) + len(r.get("import_hint", "")) for r in output)
            + size
            > 16000
        ):
            return
        record = {
            "path": name,
            "lines": [start, end],
            "symbol": symbol,
            "kind": kind,
            "code": code,
        }
        if import_hint:
            record["import_hint"] = import_hint
        output.append(record)
        covered.setdefault(name, []).append((start, end))

    def module_name(name):
        """Only infer imports for regular packages under the repo or src root."""
        path = PurePosixPath(name)
        parts = path.with_suffix("").parts
        root = PurePosixPath("src") if parts[0] == "src" else PurePosixPath(".")
        parts = parts[1:] if parts[0] == "src" else parts
        if not parts or not all(p.isidentifier() for p in parts):
            return None
        parent = path.parent
        while parent != root:
            if source((parent / "__init__.py").as_posix()) is None:
                return None
            parent = parent.parent
        return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)

    def resolve_module(module):
        path = module.replace(".", "/")
        candidates = [
            prefix + suffix
            for prefix in ("", "src/")
            for suffix in (path + ".py", path + "/__init__.py")
        ]
        matches = [
            name
            for name in candidates
            if source(name) is not None and module_name(name) == module
        ]
        return matches[0] if len(matches) == 1 else None

    def imports(tree):
        typing_names, checking_names = set(), set()
        for node in tree.body:
            if isinstance(node, ast.Import):
                typing_names.update(
                    a.asname or a.name for a in node.names if a.name == "typing"
                )
            elif (
                isinstance(node, ast.ImportFrom)
                and not node.level
                and node.module == "typing"
            ):
                checking_names.update(
                    a.asname or a.name for a in node.names if a.name == "TYPE_CHECKING"
                )
        result = []
        for node in tree.body:
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                result.append((node, False))
            elif isinstance(node, ast.If):
                test = node.test
                if (isinstance(test, ast.Name) and test.id in checking_names) or (
                    isinstance(test, ast.Attribute)
                    and test.attr == "TYPE_CHECKING"
                    and isinstance(test.value, ast.Name)
                    and test.value.id in typing_names
                ):
                    result.extend(
                        (child, True)
                        for child in node.body
                        if isinstance(child, (ast.Import, ast.ImportFrom))
                    )
        return result

    def imported_class(name, node, alias):
        if not isinstance(node, ast.ImportFrom):
            return None
        module = node.module or ""
        if node.level:
            owner = module_name(name)
            if not owner:
                return None
            package = owner.split(".")
            if PurePosixPath(name).name != "__init__.py":
                package.pop()
            if node.level > len(package):
                return None
            module = ".".join(
                package[: len(package) - node.level + 1] + ([module] if module else [])
            )
        target = resolve_module(module) if module else None
        if target is None:
            return None
        classes = {
            n.name: n for n in source(target)[1].body if isinstance(n, ast.ClassDef)
        }
        if alias.name not in classes:
            return None
        hint = f"from {module} import {alias.name}"
        if alias.asname:
            hint += f" as {alias.asname}"
        return target, classes[alias.name], classes, hint

    def constructor_dependencies(name, init):
        # One extra hop from a retrieved/inherited constructor, at most two
        # distinct classes. Do not recursively expand a dependency's annotations.
        args = init.args
        names = set()
        for arg in [
            *args.posonlyargs,
            *args.args,
            *args.kwonlyargs,
            args.vararg,
            args.kwarg,
        ]:
            annotation = arg.annotation if arg else None
            if isinstance(annotation, ast.Constant) and isinstance(
                annotation.value, str
            ):
                try:
                    annotation = ast.parse(annotation.value, mode="eval")
                except SyntaxError:
                    continue
            if annotation:
                names.update(
                    n.id for n in ast.walk(annotation) if isinstance(n, ast.Name)
                )
        bindings = {}
        for node, conditional in imports(source(name)[1]):
            for alias in node.names:
                bindings.setdefault(alias.asname or alias.name, []).append(
                    (node, alias, conditional)
                )
        for local, matches in bindings.items():
            if local not in names or len(matches) != 1 or len(dependencies) >= 2:
                continue
            node, alias, conditional = matches[0]
            resolved = imported_class(name, node, alias)
            if resolved is None:
                continue
            target, cls, _, hint = resolved
            if (target, cls.name) in dependencies:
                continue
            dependencies.add((target, cls.name))
            if conditional:
                add(name, node.lineno, node.end_lineno, local, "type_checking_import")
            add(target, cls.lineno, cls.lineno, cls.name, "dependency_class", hint)
            dependency_init = next(
                (
                    n
                    for n in cls.body
                    if isinstance(n, ast.FunctionDef) and n.name == "__init__"
                ),
                None,
            )
            if dependency_init:
                add(
                    target,
                    dependency_init.lineno,
                    max(dependency_init.lineno, dependency_init.body[0].lineno - 1),
                    cls.name + ".__init__",
                    "constructor_signature",
                )

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
            constructor_dependencies(name, init)
        else:
            for base in cls.bases:
                if not isinstance(base, ast.Name):
                    continue
                if base.id in classes:
                    constructor(name, classes[base.id], classes, seen)
                    continue
                # Follow verified local imports; never execute project imports.
                _, tree = source(name)
                for node, _ in imports(tree):
                    for alias in node.names:
                        if (alias.asname or alias.name) != base.id:
                            continue
                        resolved = imported_class(name, node, alias)
                        if resolved:
                            target, base_cls, others, _ = resolved
                            constructor(target, base_cls, others, seen)

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
