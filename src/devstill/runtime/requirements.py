"""Conservative delivery requirements extracted locally from explicit Python requests.

This is not a natural-language correctness verifier. Ambiguous prose remains the
model's responsibility; only unambiguous locations and exports are enforced here.
"""

import re
from pathlib import Path

IDENT = r"[A-Za-z_]\w*"
MODULE = rf"{IDENT}(?:\.{IDENT})*"


def delivery_requirements(repo, query):
    # Recognize installed source roots without importing project code.
    def locate(module):
        relative = Path(*module.split("."))
        for root in (Path("src"), Path(".")):
            package = repo / root / module.split(".")[0] / "__init__.py"
            if package.is_file():
                target = root / relative
                return target.as_posix(), (root / module.split(".")[0]).as_posix()
        return None

    modules, exports, files = [], [], []
    for match in re.finditer(
        r"\b(?:create|add)\s+(?:(?:a|the|new)\s+)*(?:file\s+)?`?((?:[\w.-]+/)*[\w-]+\.(?:py|tsx?|jsx?|go|rs|rb|java|json|css|html))`?(?![\w./])",
        query,
        re.IGNORECASE,
    ):
        if ".." not in Path(match[1]).parts:
            files.append(match[1])
    for match in re.finditer(
        rf"\b(?:create|add)\s+(?:(?:a|the|new)\s+)*(?:module\s+)?`?({MODULE}\.{IDENT})`?",
        query,
        re.IGNORECASE,
    ):
        name = match[1]
        found = locate(name)
        if found and not name.endswith(".py"):
            modules.append({"module": name, "path": found[0] + ".py"})
    functions = re.findall(rf"\b({IDENT})\s*\([^\n)]*\)\s*->", query)
    for match in re.finditer(
        rf"\bexport\s+`?({IDENT}|the function)`?\s+from\s+(?:the\s+)?`?({MODULE})`?\s+package\s+root\b",
        query,
        re.IGNORECASE,
    ):
        symbol, package = match[1], match[2]
        if symbol.lower() == "the function":
            if len(set(functions)) != 1:
                continue
            symbol = functions[0]
        found = locate(package)
        if found:
            exports.append(
                {"module": package, "name": symbol, "path": found[0] + "/__init__.py"}
            )
    return {"modules": modules, "exports": exports, "files": files}


def delivery_checks(requirements):
    """Immutable checks, supplied by the runtime rather than the edit generator."""
    lines = ["import importlib", "from pathlib import Path"]
    for name in requirements.get("files", []):
        lines.append(f"assert Path({name!r}).is_file(), 'Missing requested file'")
    for item in requirements["modules"]:
        lines += [
            f"assert Path({item['path']!r}).is_file(), 'Missing requested module file'",
            f"importlib.import_module({item['module']!r})",
        ]
    for item in requirements["exports"]:
        lines.append(
            f"assert hasattr(importlib.import_module({item['module']!r}), {item['name']!r}), 'Missing requested export {item['name']}'"
        )
        for module in requirements["modules"]:
            if module["module"].startswith(item["module"] + "."):
                lines.append(
                    f"assert getattr(importlib.import_module({item['module']!r}), {item['name']!r}) is getattr(importlib.import_module({module['module']!r}), {item['name']!r}), 'Export must reference requested module'"
                )
    return [["python", "-c", "\n".join(lines)]] if len(lines) > 2 else []
