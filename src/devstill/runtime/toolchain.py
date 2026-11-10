"""Per-run toolchain detection for the Quick loop: Python or JavaScript/TypeScript.

Detection is static and cheap: file extensions of the retrieved context,
repository markers (package.json, pyproject.toml), installed tool binaries in
node_modules/.bin and a light reading of the test runner's config file.
Project code is never executed.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

JS_SUFFIXES = {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".mts", ".cts"}
PY_SUFFIXES = {".py", ".pyi"}
# A Python project, even when it also ships a package.json (Django admin,
# Sphinx themes); requirements*.txt counts too.
PYTHON_MARKERS = (
    "pyproject.toml",
    "setup.py",
    "setup.cfg",
    "manage.py",
    "Pipfile",
    "tox.ini",
)
# Installed dependencies: linked into workspaces, never content.
DEPENDENCY_DIRS = ("node_modules",)
# Never scanned for tests or samples.
SKIP_DIRS = {
    "node_modules",
    ".git",
    ".devstill",
    "dist",
    "build",
    "coverage",
    "__pycache__",
    ".next",
    ".turbo",
    ".venv",
    "venv",
}
JS_TEST_FILE = re.compile(r"\.(test|spec)\.[cm]?[jt]sx?$")
SCRATCH_PREFIX = "__devstill_check_"
# A numbered scratch module name: runs of the same check on the edited and
# the original code use different numbers, so messages compare without it.
SCRATCH_NAME = re.compile(re.escape(SCRATCH_PREFIX) + r"\d+")
# package.json script bodies that run tests, and ones that only type-check.
RUNS_TESTS = re.compile(
    r"(?:^|[\s;&|(/])(?:vitest|jest|mocha|ava|bun\s+test"
    r"|node\s+(?:-\S+\s+)*--test)(?![\w.:-])"
)
TYPE_CHECK_SCRIPT = re.compile(
    r"^\s*(?:\w+=\S*\s+)*(?:npx\s+)?(?:tsc|vue-tsc)(?![\w.-])[^;&|]*$"
)
VITEST_CONFIGS = tuple(
    f"{base}.config.{ext}"
    for base in ("vitest", "vite")
    for ext in ("ts", "mts", "cts", "js", "mjs", "cjs")
)
# Framework imports are not samples of how tests import the project.
FRAMEWORK_MODULES = re.compile(
    r"^(?:vitest|jest|@jest/|@vitest/|react(?:-dom)?(?:/|$)|@testing-library/|node:|"
    r"assert$|path$|fs$|util$|events$)"
)
MAX_SCANNED_FILES = 4000


@dataclass
class Toolchain:
    """What the loop needs to know about the project's language and tools."""

    language: str = "python"  # "python" or "js"
    runner: str | None = None  # "vitest" or "jest" (JS mode)
    runner_path: str | None = None  # repository-relative runner binary
    typescript: bool = False  # tsconfig.json and an installed tsc
    tsc_path: str | None = None
    test_dir: str = "tests"  # where scratch test modules go ("." = root)
    test_suffix: str = ".test.ts"
    globals: bool = False  # describe/it/expect need no import
    import_sample: list[str] = field(default_factory=list)
    react: bool = False
    testing_library: bool = False
    package: str | None = None
    dependencies: list[str] = field(default_factory=list)
    runner_version: str | None = None  # the installed runner's version
    # package.json scripts that run the test runner, and that only type-check.
    test_scripts: list[str] = field(default_factory=list)
    type_scripts: list[str] = field(default_factory=list)

    @property
    def js(self) -> bool:
        return self.language == "js"

    def scratch_name(self, number: int) -> str:
        """Repository-relative path of the scratch test module `number`."""
        name = f"{SCRATCH_PREFIX}{number}{self.test_suffix}"
        return name if self.test_dir in ("", ".") else f"{self.test_dir}/{name}"


def is_scratch(name: str) -> bool:
    return SCRATCH_PREFIX in PurePosixPath(name).name


def without_scratch_number(text):
    """`text` with numbered scratch module names made equal."""
    return SCRATCH_NAME.sub(SCRATCH_PREFIX + "N", text) if text else text


def _language(path):
    suffix = PurePosixPath(path).suffix.lower()
    if suffix in JS_SUFFIXES:
        return "js"
    return "python" if suffix in PY_SUFFIXES else None


def python_project(repo: Path) -> bool:
    if any((repo / marker).exists() for marker in PYTHON_MARKERS):
        return True
    try:
        return any(repo.glob("requirements*.txt"))
    except OSError:
        return False


def detect(repo, records=(), path=None) -> Toolchain:
    """Python unless the request is about JS/TS code in a package.json project.

    The primary signal is the languages of the retrieved (non-support)
    source files, in ranking order; with no code retrieved, repository
    markers decide. A Python project that also ships a package.json switches
    only when the top-ranked file is JS/TS, JS/TS files clearly dominate and
    a JS test runner is installed: otherwise its Python tools and test guard
    stay. `path` is the PATH used to find node (default: this process's PATH).
    """
    repo = Path(repo)
    if not (repo / "package.json").is_file():
        return Toolchain()
    primary = [
        r for r in records if not (r.get("support") or r.get("alternate"))
    ] or list(records)
    ordered = dict.fromkeys(str(r.get("path", "")) for r in primary)
    languages = [found for found in map(_language, ordered) if found]
    js, py = languages.count("js"), languages.count("python")
    top = languages[0] if languages else None
    if python_project(repo):
        if top != "js" or js <= 2 * py:
            return Toolchain()
        toolchain = detect_js(repo, path)
        return toolchain if toolchain.runner else Toolchain()
    if py > js or (py == js and top == "python"):
        return Toolchain()
    return detect_js(repo, path)


def _package(repo):
    try:
        data = json.loads((repo / "package.json").read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def detect_js(repo: Path, path=None) -> Toolchain:
    manifest = _package(repo)
    deps = {}
    for key in ("dependencies", "devDependencies", "peerDependencies"):
        if isinstance(manifest.get(key), dict):
            deps.update(manifest[key])
    modules = repo / "node_modules"
    installed = modules.is_dir()
    node = shutil.which("node", path=path) is not None
    binaries = modules / ".bin"

    def binary(name):
        return (
            f"node_modules/.bin/{name}"
            if node and installed and (binaries / name).exists()
            else None
        )

    scripts = (
        manifest.get("scripts") if isinstance(manifest.get("scripts"), dict) else {}
    )
    test_script = str(scripts.get("test", ""))

    def version(name):
        try:
            data = json.loads((modules / name / "package.json").read_text())
        except (OSError, ValueError):
            return None
        found = data.get("version") if isinstance(data, dict) else None
        return found if isinstance(found, str) else None

    vitest, jest = binary("vitest"), binary("jest")
    runner = runner_path = None
    if vitest and not (jest and "jest" in test_script and "vitest" not in test_script):
        runner, runner_path = "vitest", vitest
    elif jest:
        runner, runner_path = "jest", jest
    tsc = binary("tsc") if (repo / "tsconfig.json").is_file() else None
    typescript = (repo / "tsconfig.json").is_file() or "typescript" in deps
    tests = test_files(repo)
    config = vitest_config(repo) if runner == "vitest" else {}
    directory, suffix = scratch_location(
        repo, tests, config, typescript=typescript, react="react" in deps
    )
    name = manifest.get("name") if isinstance(manifest.get("name"), str) else None
    return Toolchain(
        language="js",
        runner=runner,
        runner_path=runner_path,
        typescript=tsc is not None,
        tsc_path=tsc,
        test_dir=directory,
        test_suffix=suffix,
        globals=bool(config.get("globals")) or runner == "jest",
        import_sample=import_sample(repo, tests, name),
        react="react" in deps,
        testing_library="@testing-library/react" in deps,
        package=name,
        dependencies=[d for d in DEPENDENCY_DIRS if (repo / d).is_dir()],
        runner_version=version(runner) if runner else None,
        test_scripts=sorted(
            key
            for key, body in scripts.items()
            if isinstance(body, str) and RUNS_TESTS.search(body)
        ),
        type_scripts=sorted(
            key
            for key, body in scripts.items()
            if isinstance(body, str) and TYPE_CHECK_SCRIPT.match(body)
        ),
    )


def test_files(repo: Path, limit=MAX_SCANNED_FILES) -> list[str]:
    """Repository-relative JS/TS test files (dependency and build dirs skipped)."""
    found, seen = [], 0
    for current, directories, names in os.walk(repo):
        directories[:] = sorted(
            d for d in directories if d not in SKIP_DIRS and not d.startswith(".")
        )
        base = Path(current).relative_to(repo)
        for name in sorted(names):
            seen += 1
            if JS_TEST_FILE.search(name) and SCRATCH_PREFIX not in name:
                found.append((base / name).as_posix())
        if seen > limit:
            break
    return found


# --- vitest config ------------------------------------------------------------


def _strip_comments(text):
    out, index, quote = [], 0, None
    while index < len(text):
        char = text[index]
        if quote:
            out.append(char)
            if char == "\\" and index + 1 < len(text):
                out.append(text[index + 1])
                index += 2
                continue
            if char == quote:
                quote = None
        elif char in "'\"`":
            quote = char
            out.append(char)
        elif text.startswith("//", index):
            end = text.find("\n", index)
            index = len(text) if end == -1 else end
            continue
        elif text.startswith("/*", index):
            end = text.find("*/", index + 2)
            index = len(text) if end == -1 else end + 2
            continue
        else:
            out.append(char)
        index += 1
    return "".join(out)


def _top_level(block):
    """The text of an object literal body at brace depth 0 (strings kept)."""
    out, depth, quote, index = [], 0, None, 0
    while index < len(block):
        char = block[index]
        if quote:
            if depth == 0:
                out.append(char)
            if char == "\\" and index + 1 < len(block):
                if depth == 0:
                    out.append(block[index + 1])
                index += 2
                continue
            if char == quote:
                quote = None
        elif char in "'\"`":
            quote = char
            if depth == 0:
                out.append(char)
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
        elif depth == 0:
            out.append(char)
        index += 1
    return "".join(out)


def _object_after(text, pattern):
    """Body of the first object literal following `pattern` (None if absent)."""
    match = re.search(pattern, text)
    if not match:
        return None
    start = text.find("{", match.end() - 1)
    if start == -1:
        return None
    depth, quote, index = 0, None, start
    while index < len(text):
        char = text[index]
        if quote:
            if char == "\\":
                index += 2
                continue
            if char == quote:
                quote = None
        elif char in "'\"`":
            quote = char
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start + 1 : index]
        index += 1
    return None


_STRING = re.compile(r"""(['"`])((?:(?!\1)[^\\]|\\.)*)\1""")


def parse_vitest_config(text: str) -> dict:
    """dir, include and globals from a vitest config's `test` block, if simple."""
    block = _object_after(_strip_comments(text), r"\btest\s*:\s*\{")
    if block is None:
        return {}
    top = _top_level(block)
    found = {}
    match = re.search(r"\bdir\s*:\s*(['\"`])([^'\"`]+)\1", top)
    if match:
        found["dir"] = match.group(2)
    match = re.search(r"\binclude\s*:\s*\[([^\]]*)\]", top)
    if match:
        patterns = [m.group(2) for m in _STRING.finditer(match.group(1))]
        if patterns:
            found["include"] = patterns
    if re.search(r"\bglobals\s*:\s*true\b", top):
        found["globals"] = True
    return found


def vitest_config(repo: Path) -> dict:
    for name in VITEST_CONFIGS:
        path = repo / name
        if path.is_file():
            try:
                return parse_vitest_config(path.read_text(errors="ignore"))
            except (OSError, RecursionError):
                return {}
    return {}


def glob_regex(pattern: str):
    """A regex for a test include glob (**, *, ?, {a,b}, [..], ?(a|b)), or None."""
    out, index = [], 0
    while index < len(pattern):
        char = pattern[index]
        if pattern.startswith("**/", index):
            out.append("(?:.*/)?")
            index += 3
            continue
        if pattern.startswith("**", index):
            out.append(".*")
            index += 2
            continue
        if char in "?+@" and pattern.startswith("(", index + 1):
            end = pattern.find(")", index)
            if end == -1:
                return None
            body = "|".join(re.escape(p) for p in pattern[index + 2 : end].split("|"))
            out.append(f"(?:{body})" + {"?": "?", "+": "+", "@": ""}[char])
            index = end + 1
            continue
        if char == "!" and pattern.startswith("(", index + 1):
            return None
        if char == "*":
            out.append("[^/]*")
        elif char == "?":
            out.append("[^/]")
        elif char == "{":
            end = pattern.find("}", index)
            if end == -1:
                return None
            body = "|".join(re.escape(p) for p in pattern[index + 1 : end].split(","))
            out.append(f"(?:{body})")
            index = end + 1
            continue
        elif char == "[":
            end = pattern.find("]", index)
            if end == -1:
                return None
            out.append(pattern[index : end + 1])
            index = end + 1
            continue
        else:
            out.append(re.escape(char))
        index += 1
    try:
        return re.compile("".join(out) + r"\Z")
    except re.error:
        return None


VITEST_INCLUDE = "**/*.{test,spec}.?(c|m)[jt]s?(x)"


def scratch_location(repo, tests, config, *, typescript, react):
    """(directory, suffix) for scratch test modules the runner will discover."""
    suffixes = Counter()
    for name in tests:
        match = JS_TEST_FILE.search(name)
        suffixes[match.group(0)] += 1
    infix = (
        ".spec"
        if suffixes
        and sum(n for s, n in suffixes.items() if s.startswith(".spec"))
        > sum(n for s, n in suffixes.items() if s.startswith(".test"))
        else ".test"
    )
    if any(s.endswith(".tsx") for s in suffixes) or (react and typescript):
        ext = ".tsx"
    elif typescript or any(s.endswith(".ts") for s in suffixes):
        ext = ".ts"
    elif any(s.endswith(".jsx") for s in suffixes):
        ext = ".jsx"
    else:
        ext = ".js"
    candidates = [infix + ext] + [s for s, _ in suffixes.most_common()]
    for pattern in config.get("include") or []:
        tail = pattern.rsplit("*", 1)[-1]
        if tail.startswith(".") and not re.search(r"[*?{\[(]", tail):
            candidates.append(tail)  # e.g. src/**/*.spec.ts -> .spec.ts
    base = str(config.get("dir", "")).strip("./") if config.get("dir") else ""
    patterns = [glob_regex(p) for p in config.get("include") or [VITEST_INCLUDE]]
    directories = []
    if base:
        directories.append(base)
    for pattern in config.get("include") or []:
        static = []
        for part in pattern.split("/"):
            if re.search(r"[*?{\[(]", part):
                break
            static.append(part)
        if static:
            directories.append("/".join([base, *static] if base else static))
    counts = Counter(str(PurePosixPath(name).parent) for name in tests)
    directories += [d for d, _ in counts.most_common()]
    directories += ["tests", "test", "__tests__", "."]
    for directory in dict.fromkeys(directories):
        if directory not in (".", "") and not (repo / directory).is_dir():
            continue
        for suffix in dict.fromkeys(candidates):
            name = f"{directory}/{SCRATCH_PREFIX}1{suffix}".removeprefix("./")
            relative = name
            if base:
                if not (name + "/").startswith(base + "/"):
                    continue
                relative = name[len(base) + 1 :]
            if all(p is None for p in patterns) or any(
                p is not None and p.match(relative) for p in patterns
            ):
                return directory or ".", suffix
    return (counts.most_common(1)[0][0] if counts else "."), candidates[0]


# --- how existing tests import the project ------------------------------------

# The import clause never contains quotes, so a match cannot run past a
# side-effect import (`import './setup'`) into the next statement.
_IMPORT = re.compile(
    r"^import\s+(?:type\s+)?[^;'\"]*?\bfrom\s+(['\"])([^'\"]+)\1;?"
    r"|^import\s+(['\"])([^'\"]+)\3;?",
    re.MULTILINE,
)


def import_lines(text: str) -> list[tuple[str, str]]:
    """(module, one-line statement) for each import at the top of a module."""
    head = "\n".join(text.split("\n")[:80])
    found = []
    for match in _IMPORT.finditer(head):
        module = match.group(2) or match.group(4)
        statement = " ".join(match.group(0).split())
        statement = re.sub(r"\{\s+", "{ ", re.sub(r",?\s+\}", " }", statement))
        if len(statement) <= 160:
            found.append((module, statement))
    return found


def import_sample(repo: Path, tests, package=None, limit=6) -> list[str]:
    """How existing tests import the project: one statement per imported
    module (the most used one, value imports before type-only ones), most
    used modules first, after the test framework import."""
    project, framework = {}, Counter()
    for name in tests[:60]:
        try:
            text = (repo / name).read_text(errors="ignore")
        except OSError:
            continue
        for module, statement in import_lines(text):
            if " from " not in statement:
                continue  # side-effect imports (setup files) import nothing
            if module in ("vitest", "@jest/globals"):
                framework[statement] += 1
            elif FRAMEWORK_MODULES.search(module):
                continue
            elif module.startswith((".", "@/", "~/", "src/")) or (
                package and (module == package or module.startswith(package + "/"))
            ):
                if module.startswith(".") and "test" in module.lower():
                    continue  # test helpers
                project.setdefault(module, Counter())[statement] += 1

    def best(statements):
        return min(
            statements.items(),
            key=lambda item: (item[0].startswith("import type"), -item[1]),
        )[0]

    modules = sorted(
        project.items(),
        key=lambda item: (
            all(s.startswith("import type") for s in item[1]),
            -sum(item[1].values()),
        ),
    )
    sample = [best(statements) for _, statements in modules[:limit]]
    if framework:
        sample.insert(0, framework.most_common(1)[0][0])
    return sample


def link_dependencies(repo, work, names=DEPENDENCY_DIRS):
    """Link installed dependency directories into a workspace copy.

    The link points at the resolved original (a symlinked node_modules is
    followed once), so tools see the very same install; nothing ever copies
    or edits through it.
    """
    linked = []
    for name in names:
        source = Path(repo) / name
        target = Path(work) / name
        if not source.is_dir() or target.exists() or target.is_symlink():
            continue
        target.symlink_to(source.resolve(), target_is_directory=True)
        linked.append(name)
    return linked
