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
    # The suffix without JSX (".test.ts" for ".test.tsx") when the runner
    # discovers it in test_dir too: scratch modules without JSX use it, so
    # TypeScript generics like `<T>(x: T) => x` parse.
    plain_suffix: str | None = None

    @property
    def js(self) -> bool:
        return self.language == "js"

    def scratch_name(self, number: int, suffix: str | None = None) -> str:
        """Repository-relative path of the scratch test module `number`."""
        name = f"{SCRATCH_PREFIX}{number}{suffix or self.test_suffix}"
        return name if self.test_dir in ("", ".") else f"{self.test_dir}/{name}"

    def scratch_suffix(self, code: str) -> str:
        """The scratch module suffix for `code`: the plain one unless it has JSX."""
        if self.plain_suffix and not has_jsx(code):
            return self.plain_suffix
        return self.test_suffix


# A closing tag (</div>, </>) or a self-closing element: TypeScript generics
# and comparisons never contain either.
_JSX = re.compile(r"</\s*[A-Za-z_$]?[\w$.:-]*\s*>|/>")


def has_jsx(code: str) -> bool:
    return _JSX.search(code or "") is not None


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
        plain_suffix=plain_suffix(config, directory, suffix),
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
    # Test files may run in different environments (or projects) by their
    # path or suffix: a scratch module keeps the suffix the tests use.
    if re.search(r"\b(?:environmentMatchGlobs|projects|workspace)\s*:", top):
        found["environments"] = True
    return found


# Vitest workspace/projects files (one config per group of test files).
VITEST_WORKSPACES = tuple(
    f"vitest.{base}.{ext}"
    for base in ("workspace", "projects")
    for ext in ("ts", "mts", "cts", "js", "mjs", "cjs", "json")
)


def vitest_config(repo: Path) -> dict:
    found = {}
    for name in VITEST_CONFIGS:
        path = repo / name
        if path.is_file():
            try:
                found = parse_vitest_config(path.read_text(errors="ignore"))
            except (OSError, RecursionError):
                found = {}
            break
    if any((repo / name).is_file() for name in VITEST_WORKSPACES):
        found = dict(found, environments=True)
    return found


def read_jsonc(path):
    """A JSON file that may have comments and trailing commas (tsconfig),
    or None."""
    try:
        text = _strip_comments(Path(path).read_text(errors="ignore"))
        data = json.loads(re.sub(r",(\s*[}\]])", r"\1", text))
    except (OSError, ValueError, RecursionError):
        return None
    return data if isinstance(data, dict) else None


def tsconfig_for(repo: Path, name: str):
    """The tsconfig (repository-relative) that type-checks the file `name`:
    tsconfig.json, or for a solution-style one (no files of its own, only
    references, as Vite's templates have) the referenced config whose
    files/include cover `name`; None when none does."""
    root = Path(repo)
    data = read_jsonc(root / "tsconfig.json")
    references = (data or {}).get("references")
    if (
        not data
        or not isinstance(references, list)
        or not references
        or not ("files" in data or "include" in data)
        or data.get("files")
        or data.get("include")
    ):
        return "tsconfig.json"
    for reference in references:
        target = reference.get("path") if isinstance(reference, dict) else None
        if not isinstance(target, str):
            continue
        path = root / target
        if path.is_dir():
            path = path / "tsconfig.json"
        config = read_jsonc(path)
        if config is None:
            continue
        try:
            base = path.parent.resolve().relative_to(root.resolve()).as_posix()
        except (OSError, ValueError):
            continue
        prefix = "" if base == "." else base + "/"
        if not name.startswith(prefix):
            continue
        relative = name[len(prefix) :]
        files = [str(f).removeprefix("./") for f in config.get("files") or []]
        include = config.get("include")
        if include is None:
            include = [] if "files" in config else ["**/*"]
        patterns = []
        for pattern in include:
            pattern = str(pattern).removeprefix("./").rstrip("/")
            last = pattern.rsplit("/", 1)[-1]
            if not GLOB_CHARS.search(pattern) and "." not in last:
                pattern += "/**/*"  # a directory: everything under it
            patterns.append(glob_regex(pattern))
        if relative in files or any(
            p is not None and p.match(relative) for p in patterns
        ):
            return path.relative_to(root).as_posix()
    return None


GLOB_CHARS = re.compile(r"[*?{\[]")


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
            if _discovered(directory, suffix, base, patterns):
                return directory or ".", suffix
    return (counts.most_common(1)[0][0] if counts else "."), candidates[0]


def _discovered(directory, suffix, base, patterns):
    """The runner finds a scratch module with `suffix` in `directory`
    (`base`: the vitest `dir`; `patterns`: include regexes)."""
    name = f"{directory}/{SCRATCH_PREFIX}1{suffix}".removeprefix("./")
    relative = name
    if base:
        if not (name + "/").startswith(base + "/"):
            return False
        relative = name[len(base) + 1 :]
    return all(p is None for p in patterns) or any(
        p is not None and p.match(relative) for p in patterns
    )


def plain_suffix(config, directory, suffix):
    """`suffix` without its JSX "x" (.test.tsx -> .test.ts) when the runner
    discovers that in `directory` too, else None (also when test files may
    run in environments chosen by their path or suffix: a .test.ts module
    could then run without the DOM a .test.tsx one gets)."""
    if not suffix.endswith(("tsx", "jsx")) or config.get("environments"):
        return None
    base = str(config.get("dir", "")).strip("./") if config.get("dir") else ""
    patterns = [glob_regex(p) for p in config.get("include") or [VITEST_INCLUDE]]
    plain = suffix[:-1]
    return plain if _discovered(directory, plain, base, patterns) else None


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
