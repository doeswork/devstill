"""JavaScript/TypeScript checks for the Quick loop.

* The `test` tool: a short test module from the model is written to a scratch
  file the project's runner discovers, run alone with a JSON reporter, and
  always deleted afterwards.
* JsGuard: background regression runs with vitest/jest and `tsc --noEmit`.
  Results are parsed from JSON reports (tests) and tsc's plain diagnostics
  (types), and compared with the same runs on a pristine copy of the sources.

Guard runs never touch the agent's workspace: baselines run in a pristine
copy and current-state runs in a mirror that is synced from the workspace's
changed files on every change. Scratch test files and the loop's
original-state swaps in the workspace are therefore never seen by a guard run.
"""

from __future__ import annotations

import difflib
import hashlib
import itertools
import json
import re
import shutil
import subprocess
import tempfile
import threading
import time
from collections import Counter
from pathlib import Path, PurePosixPath

from devstill.runtime.jssyntax import code_frame
from devstill.runtime.toolchain import (
    JS_SUFFIXES,
    SCRATCH_PREFIX,
    is_scratch,
    link_dependencies,
    tsconfig_for,
)
from devstill.runtime.tools import TestGuard, ToolError, ToolResult, _kill

ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
# Stack frames ("    at fn (file:1:2)", "    at /abs/file.ts:7:28",
# "    at new Promise (<anonymous>)"), not message text that starts with "at".
STACK_LINE = re.compile(
    r"^\s+at\s(?:.*:\d+(?::\d+)?\)?|.*\((?:<anonymous>|native)\))\s*$"
)
# vitest's own frames (" ❯ fn file:1:2", " ❯ file:1:2") and code frames
# ("    6|   expect(x)", "     |        ^").
VITEST_FRAME = re.compile(r"^\s*❯\s|^\s*\d+\s*\|\s|^\s*\d+\s*\|$|^\s*\|\s*\^")
NO_ASSERTION = "expected any number of assertion, but got none"
MESSAGE_CHARS = 1200
LOAD_FAILURE = "(module failed to load)"


def runner_env(base):
    """Environment for runner processes: no colors, no CI-only reporters."""
    env = dict(base)
    env["NO_COLOR"] = "1"
    env["FORCE_COLOR"] = "0"
    env.pop("GITHUB_ACTIONS", None)
    return env


def _major(version):
    match = re.match(r"\s*v?(\d+)\.", version or "")
    return int(match.group(1)) if match else None


class Vitest:
    """vitest argv. Coverage is always off: a project-wide threshold can never
    be met by one scratch module (or a related subset), and it is slow."""

    name = "vitest"

    def __init__(self, binary, version=None):
        self.binary = binary
        # --expect.requireAssertions exists since vitest 3; older versions get
        # the same rule from a footer appended to the scratch module.
        self.native_assertions = (_major(version) or 0) >= 3

    def _report(self, report):
        return [
            "--reporter=json",
            f"--outputFile.json={report}",
            "--coverage.enabled=false",
        ]

    def files(self, files, report, display=False, require_assertions=False):
        argv = [self.binary, "run", *files, *self._report(report)]
        if require_assertions and self.native_assertions:
            argv.append("--expect.requireAssertions")
        return argv + (["--reporter=default"] if display else [])

    def footer(self):
        """Code appended to a scratch module so that every test must run an
        expect (None when a command-line option does it). Imports are
        hoisted, aliases avoid clashes, and line numbers stay the same."""
        if self.native_assertions:
            return None
        return (
            "\nimport { beforeEach as __devstillBeforeEach, expect as "
            "__devstillExpect } from 'vitest'\n"
            "__devstillBeforeEach(() => { __devstillExpect.hasAssertions() })\n"
        )

    def related(self, sources, report):
        return [self.binary, "related", "--run", *sources, *self._report(report)]

    def suite(self, report):
        return [self.binary, "run", *self._report(report)]


class Jest:
    name = "jest"

    def __init__(self, binary, version=None):
        self.binary = binary

    def _base(self, report):
        return [
            self.binary,
            "--ci",
            "--json",
            f"--outputFile={report}",
            "--coverage=false",
        ]

    def files(self, files, report, display=False, require_assertions=False):
        return self._base(report) + ["--runTestsByPath", *files]

    def footer(self):
        return (
            "\nif (typeof beforeEach === 'function' && typeof expect !== 'undefined') "
            "beforeEach(() => { expect.hasAssertions() })\n"
        )

    def related(self, sources, report):
        return self._base(report) + ["--findRelatedTests", *sources]

    def suite(self, report):
        return self._base(report)


def runner_for(toolchain):
    if toolchain is None or not toolchain.runner_path:
        return None
    return {"vitest": Vitest, "jest": Jest}[toolchain.runner](
        toolchain.runner_path, getattr(toolchain, "runner_version", None)
    )


# --- report parsing (pure) -----------------------------------------------------


def clean_message(text, limit=MESSAGE_CHARS):
    """An assertion message without colors and stack frames."""
    lines = [
        line.rstrip()
        for line in ANSI.sub("", text or "").split("\n")
        if not (STACK_LINE.match(line) or VITEST_FRAME.match(line))
    ]
    while lines and not lines[-1].strip():
        lines.pop()
    while lines and not lines[0].strip():
        lines.pop(0)
    text = "\n".join(lines)
    return text if len(text) <= limit else text[: limit - 15] + "\n[... cut]"


def relative(path, root):
    path = str(path or "")
    try:
        return Path(path).resolve().relative_to(Path(root).resolve()).as_posix()
    except (ValueError, OSError):
        return path


def _frame_line(message, path):
    """First line number of `path` in a stack trace (None if absent)."""
    if not path:
        return None
    match = re.search(re.escape(str(path)) + r":(\d+):\d+", message or "")
    return int(match.group(1)) if match else None


_LOCATED = re.compile(r"(?P<path>[\w./@$+-]+\.[cm]?[jt]sx?):(?P<line>\d+):(?P<col>\d+)")
_SYNTAX = re.compile(r"Expected .+ but found|Unexpected|expected", re.IGNORECASE)


def load_frame(message, name, lines, root):
    """A code frame (with a caret) at the first location a load error names:
    the scratch module `name` (source `lines`) or a repository file."""
    for match in _LOCATED.finditer(message or ""):
        path, line = match.group("path"), int(match.group("line"))
        # esbuild (vite's transform) counts columns from 0.
        column = int(match.group("col")) + (1 if "ERROR:" in message else 0)
        if is_scratch(path):
            source, label = lines, "your test module"
        else:
            file = Path(root) / path
            if "node_modules" in PurePosixPath(path).parts or not file.is_file():
                continue
            try:
                source = file.read_text(errors="replace").split("\n")
            except OSError:
                continue
            label = path
        frame = code_frame(source, line, column)
        if not frame:
            continue
        hint = (
            "\n[an unclosed ( [ { or a missing comma above the marked line is the "
            "usual cause]"
            if _SYNTAX.search(message)
            else ""
        )
        return f"At {label} line {line}, column {column}:\n{frame}{hint}"
    return ""


_STACK_FRAME = re.compile(
    r"^\s*at\s+(?:(?P<fn>[^()]*?)\s+\()?(?:file://)?(?P<path>[^()\s]+?)"
    r":(?P<line>\d+):(?P<col>\d+)\)?\s*$"
)


def repo_frames(message, root, scratch, limit=2):
    """The innermost stack frames of a failure that point into repository
    source (not dependencies, not the test module), as one display line plus
    the source line where it was thrown; "" when there are none.

    Test-module frames inside the innermost source frame (a callback of the
    test that the source called) are skipped: the error still passed through
    repository source. Test-module frames after a source frame are its
    callers and end the search."""
    root = Path(root)
    prefixes = {str(root) + "/"}
    try:
        prefixes.add(str(root.resolve()) + "/")
    except OSError:
        pass
    frames = []
    callback = None  # the innermost test-module frame, when the source called it
    for raw in (message or "").split("\n"):
        match = _STACK_FRAME.match(raw)
        if not match:
            continue
        path = match.group("path")
        relative = next((path[len(p) :] for p in prefixes if path.startswith(p)), None)
        if relative is None:
            continue  # outside the repository (node internals, other trees)
        if is_scratch(relative) or relative == scratch:
            if frames:
                break  # reached the test module: the rest are its callers
            if callback is None:
                callback = int(match.group("line"))
            continue  # a test callback: keep looking for the source that called it
        if "node_modules" in PurePosixPath(relative).parts:
            continue
        frames.append(
            (relative, int(match.group("line")), (match.group("fn") or "").strip())
        )
        if len(frames) >= limit:
            break
    if not frames:
        return ""

    def label(frame):
        file, line, function = frame
        return f"{file}:{line}" + (f" ({function})" if function else "")

    if callback is not None:
        # Display only: where the source called back into the test module.
        text = f"thrown in your test module (check line {callback}), called from "
        text += label(frames[0])
    else:
        text = "thrown in " + label(frames[0])
        if len(frames) > 1:
            text += ", called from " + label(frames[1])
    try:
        source = (root / frames[0][0]).read_text(errors="replace").split("\n")
        line = source[frames[0][1] - 1].strip()[:160]
    except (OSError, IndexError):
        line = ""
    return text + (f"\n    {frames[0][1]} | {line}" if line else "")


def _status(value):
    if value == "passed":
        return "passed"
    if value == "failed":
        return "failed"
    return "skipped"


def parse_report(data, root):
    """Per-test outcomes from a jest-format JSON report (vitest writes the same).

    Returns {"tests": {id: "passed"|"failed"|"skipped"}, "failures": {id:
    {"message", "path"}}, "load_errors": {file: message}}, with
    id = "<file> > <describe ...> > <test>" (file relative to root). A module
    that failed to load gets the id "<file> > (module failed to load)".
    """
    tests, failures, load_errors = {}, {}, {}
    for module in (data or {}).get("testResults") or []:
        absolute = module.get("name") or module.get("testFilePath") or ""
        file = relative(absolute, root)
        results = module.get("assertionResults") or []
        for test in results:
            titles = [*(test.get("ancestorTitles") or []), test.get("title") or ""]
            base = f"{file} > " + " > ".join(t for t in titles if t)
            name, count = base, 1
            while name in tests:
                count += 1
                name = f"{base} [{count}]"
            tests[name] = _status(test.get("status"))
            if tests[name] == "failed":
                failures[name] = {
                    "message": "\n".join(test.get("failureMessages") or []),
                    "path": absolute,
                }
        failed = module.get("status") == "failed" and not any(
            tests.get(n) == "failed" for n in tests if n.startswith(f"{file} > ")
        )
        if failed and (module.get("message") or not results):
            name = f"{file} > {LOAD_FAILURE}"
            tests[name] = "failed"
            load_errors[file] = module.get("message") or "the module failed to load"
            failures[name] = {"message": load_errors[file], "path": absolute}
    return {"tests": tests, "failures": failures, "load_errors": load_errors}


_FAIL_HEADER = re.compile(r"^\s*FAIL\s+(?:\|[^|]*\|\s+)?(\S.*?)\s*$")
_LOCATION = re.compile(r"^\s*(?:❯|>)\s+(\S+?):(\d+):(\d+)")


def failure_details(text):
    """vitest default-reporter failure blocks: {"file > test": (message, location)}.

    Display only (diffs that the JSON report omits); results always come
    from the JSON report. Module failures are keyed by their file; location
    is the first (file, line) the block points at.
    """
    blocks = {}
    current, lines, location = None, [], None
    for line in ANSI.sub("", text or "").split("\n"):
        header = _FAIL_HEADER.match(line)
        if header:
            if current is not None:
                blocks.setdefault(current, (clean_message("\n".join(lines)), location))
            current, lines, location = header.group(1), [], None
            module = re.match(r"^(\S+) \[ \1 \]$", current)
            if module:
                current = module.group(1)
            continue
        if current is None:
            continue
        found = _LOCATION.match(line)
        if found or line.strip().startswith("⎯"):
            if found:
                location = (found.group(1), int(found.group(2)))
            blocks.setdefault(current, (clean_message("\n".join(lines)), location))
            current = None
            continue
        lines.append(line)
    if current is not None:
        blocks.setdefault(current, (clean_message("\n".join(lines)), location))
    return blocks


def console_output(text, limit=1500):
    """`stdout | ...` / `stderr | ...` blocks of vitest's default reporter."""
    out, keep = [], False
    for line in ANSI.sub("", text or "").split("\n"):
        if re.match(r"^(stdout|stderr) \| ", line):
            keep = True
            out.append(line)
            continue
        if keep:
            if not line.strip() or line.startswith("[exit code"):
                keep = False
                out.append("")
                continue
            out.append(line)
    shown = "\n".join(out).strip()
    if len(shown) > limit:
        shown = "[console output truncated]\n" + shown[-limit:]
    return shown


# The file may contain parentheses (Next.js route groups: app/(shop)/page.tsx).
_TSC = re.compile(
    r"^(?P<file>\S.*?)\((?P<line>\d+),(?P<col>\d+)\): error (?P<code>TS\d+): "
    r"(?P<message>.*)$"
)
_TSC_GLOBAL = re.compile(r"^error (?P<code>TS\d+): (?P<message>.*)$")


def parse_tsc(output, root=None):
    """Errors of `tsc --pretty false`: [{"file","line","col","code","message"}]."""
    errors = []
    prefix = (str(Path(root).resolve()) + "/") if root else None
    for raw in ANSI.sub("", output or "").split("\n"):
        line = raw.rstrip()
        match = _TSC.match(line) or _TSC_GLOBAL.match(line)
        if match:
            fields = match.groupdict()
            file = fields.get("file") or ""
            if prefix and file.startswith(prefix):
                file = file[len(prefix) :]
            errors.append(
                {
                    "file": file,
                    "line": int(fields.get("line") or 0),
                    "col": int(fields.get("col") or 0),
                    "code": fields["code"],
                    "message": fields["message"].strip(),
                }
            )
        elif errors and raw.startswith((" ", "\t")) and raw.strip():
            errors[-1]["message"] += "\n" + raw.strip()  # continued message
    return errors


def tsc_key(error, root=None):
    """Identity of a type error across edits: file, code and message (no lines)."""
    message = error["message"]
    if root:
        message = message.replace(str(Path(root).resolve()) + "/", "")
    return (error["file"], error["code"], message)


def excess_type_errors(before, now, before_root=None, now_root=None):
    """Errors in `now` that `before` does not account for.

    Identity is (file, code, message) without line numbers, counted: a
    second identical error in the same file is new. Of several identical
    errors, those at lines the old ones did not have are reported first.
    """
    remaining = Counter(tsc_key(e, before_root) for e in before)
    old_lines = {}
    for error in before:
        old_lines.setdefault(tsc_key(error, before_root), set()).add(error["line"])
    groups = {}
    for index, error in enumerate(now):
        groups.setdefault(tsc_key(error, now_root), []).append((index, error))
    fresh = []
    for key, errors in groups.items():
        excess = len(errors) - remaining[key]
        if excess > 0:
            ranked = sorted(
                errors, key=lambda item: item[1]["line"] in old_lines.get(key, ())
            )
            fresh.extend(ranked[:excess])
    return [error for _, error in sorted(fresh, key=lambda item: item[0])]


def tsc_text(error):
    location = (
        f"{error['file']}({error['line']},{error['col']}): " if error["file"] else ""
    )
    return f"{location}error {error['code']}: {error['message']}"


def tsc_brief(error, limit=300):
    """JS display: an error's location, code and head line plus its last
    elaboration line (the innermost mismatch), on one line."""
    location = (
        f"{error['file']}({error['line']},{error['col']}): " if error["file"] else ""
    )
    lines = [line.strip() for line in str(error["message"]).split("\n") if line.strip()]
    text = lines[0] if lines else ""
    if len(lines) > 1:
        text += " … " + lines[-1]
    if len(text) > limit:
        text = text[: limit - 5] + " [..]"
    return f"{location}error {error['code']}: {text}"


_QUOTED_NAME = re.compile(r"'([A-Za-z_$][\w$]*)'")
_IDENTIFIER = re.compile(r"[A-Za-z_$][\w$]*")
_DECLARED = re.compile(
    r"(?:\b(?:function\*?|class|interface|type|enum|const|let|var)\s+([A-Za-z_$][\w$]*))"
    # A member, property or parameter declaration: `name: Type`, `name?:`,
    # or a method `name(...) {` / `name<T>(...): T`; never a call statement
    # such as `set('a')`.
    r"|^\s*(?:readonly\s+)?(?:(?:public|private|protected|static|async|abstract)\s+)*"
    r"([A-Za-z_$][\w$]*)\??\s*(?::|(?:<[^>]*>\s*)?\(.*\)\s*[:{])"
)
_KEYWORDS = {
    "if", "for", "while", "switch", "return", "const", "let", "var", "new",
    "function", "typeof", "type", "string", "number", "boolean", "true",
    "false", "null", "undefined", "void", "unknown", "any", "never", "this",
    "export", "import", "from", "async", "await", "error", "Type", "Argument",
}  # fmt: skip


def _hunks(before, after):
    """Changed regions of a file: (old start, old lines, new start, new lines),
    1-based starts."""
    import difflib

    old, new = before.split("\n"), after.split("\n")
    found = []
    matcher = difflib.SequenceMatcher(None, old, new, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag != "equal":
            found.append((i1 + 1, old[i1:i2], j1 + 1, new[j1:j2]))
    return found


def trace_type_errors(errors, root, originals, changed=(), limit=2):
    """JS: for new type errors at lines the agent did not change, the changed
    declaration they most likely come from: an identifier of the error line
    or message declared in a changed region of a changed file. Display text
    ("" when nothing is found) and the (file, new start) of each hunk shown."""
    root = Path(root)
    regions = {}  # file -> its hunks

    def hunks_of(name):
        if name not in regions:
            try:
                after = (root / name).read_text(errors="replace")
                before = (originals(name) or b"").decode("utf-8", errors="replace")
            except (OSError, ValueError):
                regions[name] = []
                return regions[name]
            regions[name] = _hunks(before, after) if before != after else []
        return regions[name]

    shown, notes, kept = set(), [], []
    for error in errors:
        if len(notes) >= limit:
            break
        name = error.get("file") or ""
        if not name or not error.get("line"):
            continue
        try:
            source = (root / name).read_text(errors="replace").split("\n")
        except OSError:
            continue
        own = hunks_of(name) if originals is not None else []
        line = error["line"]
        if any(
            start <= line < start + max(1, len(lines)) for _, _, start, lines in own
        ):
            continue  # the agent changed this very line: the error is its own
        text = source[line - 1] if 0 < line <= len(source) else ""
        names = set(_QUOTED_NAME.findall(str(error["message"])))
        names |= {n for n in _IDENTIFIER.findall(text) if n not in _KEYWORDS}
        names = {n for n in names if len(n) >= 3}
        if not names:
            continue
        found = None
        for edited in [name, *sorted(set(changed) - {name})]:
            if is_scratch(edited) or PurePosixPath(edited).suffix not in JS_SUFFIXES:
                continue
            for hunk in hunks_of(edited):
                declared = set()
                for added in hunk[3]:
                    for match in _DECLARED.finditer(added):
                        declared.add(match.group(1) or match.group(2))
                hit = sorted(names & declared)
                if hit:
                    found = (edited, hunk, hit[0])
                    break
            if found:
                break
        if not found:
            continue
        where, (_, old_lines, new_start, new_lines), symbol = found
        if (where, new_start) in shown:
            notes.append(
                f"- {tsc_brief(error, 200)}\n  comes from the same change to "
                f"'{symbol}' ({where}:{new_start}) shown above."
            )
            continue
        shown.add((where, new_start))
        kept.append((where, new_start))

        def block(lines, sign):
            body = [f"{sign} {item}" for item in lines[:10]]
            if len(lines) > 10:
                body.append(f"{sign} [{len(lines) - 10} more lines]")
            return "\n".join(body) if body else f"{sign} (nothing)"

        notes.append(
            f"- {tsc_brief(error, 200)}\n  This error is at a line you did not change; "
            f"it comes from your change to '{symbol}' in {where} (line {new_start}): "
            "restore or adapt that change.\n  before:\n"
            + block(old_lines, "-")
            + "\n  after:\n"
            + block(new_lines, "+")
        )
    return "\n".join(notes), kept


_JEST_FILE = re.compile(r"^\s*FAIL\s+(\S+\.[cm]?[jt]sx?)\s*(?:\(.*\))?$")
_JEST_TEST = re.compile(r"^\s*●\s+(.+?)\s*$")


def failing_from_output(text):
    """Failing test ids printed by an agent's own vitest/jest command.

    Display parsing of a command the loop does not control; the ids match
    parse_report's ("<file> > <describe> > <test>").
    """
    found, jest_file = [], None
    for line in ANSI.sub("", text or "").split("\n"):
        header = _FAIL_HEADER.match(line)
        if header and " > " in header.group(1):
            found.append(header.group(1))
            continue
        jest = _JEST_FILE.match(line)
        if jest:
            jest_file = jest.group(1)
            continue
        test = _JEST_TEST.match(line)
        if test and jest_file:
            name = test.group(1)
            if name.startswith("Test suite failed to run"):
                found.append(f"{jest_file} > {LOAD_FAILURE}")
            else:
                found.append(f"{jest_file} > " + " > ".join(name.split(" › ")))
    return sorted(set(found))


def state_key(contents, original):
    """JS: identity of a workspace state from the changed files' bytes
    ({name: bytes or None}): scratch files and files equal to the original
    (`original(name)`) are left out, so equal states get equal keys."""
    items = []
    for name in sorted(contents):
        if is_scratch(name):
            continue
        data = contents[name]
        try:
            before = original(name)
        except (OSError, ValueError):
            before = None
        if data == before:
            continue
        items.append([name, None if data is None else hashlib.sha256(data).hexdigest()])
    return hashlib.sha256(json.dumps(items).encode()).hexdigest()[:16]


def js_diff(workspace, originals, keep=(), limit=3000):
    """JS: unified diff (1 line of context) of the changed files, cut only at
    hunk boundaries within `limit` characters; hunks starting at a (file,
    new line) in `keep` are always shown (a note counts those left out)."""
    keep = set(keep)
    hunks = []  # (name, new start, text)
    for name in list(workspace.changed):
        if is_scratch(name):
            continue
        path = workspace.root / name
        try:
            before = (originals(name) or b"").decode("utf-8", errors="replace")
            after = (
                path.read_bytes().decode("utf-8", errors="replace")
                if path.exists()
                else ""
            )
        except (OSError, ValueError):
            continue
        lines = list(
            difflib.unified_diff(
                before.splitlines(keepends=True),
                after.splitlines(keepends=True),
                fromfile="a/" + name,
                tofile="b/" + name,
                n=1,
            )
        )
        header, current, start = "".join(lines[:2]), [], None
        for line in lines[2:]:
            if line.startswith("@@"):
                if current:
                    hunks.append((name, start, header, "".join(current)))
                current = [line]
                match = re.match(r"@@ -\d+(?:,\d+)? \+(\d+)", line)
                start = int(match.group(1)) if match else 0
            else:
                current.append(line if line.endswith("\n") else line + "\n")
        if current:
            hunks.append((name, start, header, "".join(current)))
    shown, used, left, files = [], 0, 0, set()
    for name, start, header, text in hunks:
        wanted = any(
            name == file and start - 2 <= line <= start + text.count("\n")
            for file, line in keep
        )
        if name not in files:
            text = header + text
        if wanted or used + len(text) <= limit:
            shown.append(text)
            used += len(text)
            files.add(name)
        else:
            left += 1
    return "".join(shown) + (
        f"[{left} more changed region(s) not shown]\n" if left else ""
    )


# --- processes -------------------------------------------------------------------


def execute(argv, cwd, env, timeout, register=None, unregister=None):
    """Run argv; {"code","timed_out","output","seconds"} or None if refused."""
    started = time.perf_counter()
    with tempfile.TemporaryFile() as output:
        process = subprocess.Popen(
            argv,
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=output,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        if register is not None and not register(process):
            _kill(process)
            return None
        timed_out = False
        try:
            process.wait(timeout=max(0.1, timeout))
        except subprocess.TimeoutExpired:
            timed_out = True
        finally:
            _kill(process)
            if unregister is not None:
                unregister(process)
        output.seek(0)
        text = output.read(2_000_000).decode("utf-8", errors="replace")
    return {
        "code": process.returncode,
        "timed_out": timed_out,
        "output": text,
        "seconds": time.perf_counter() - started,
    }


def _read_report(path):
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


# --- the test tool ---------------------------------------------------------------


def run_test_module(
    workspace,
    toolchain,
    code,
    timeout,
    checkpoint=lambda: None,
    type_check=None,
    type_start=None,
):
    """Write `code` to a scratch test file, run it alone, always delete it.

    ok only if at least one test passed and nothing failed (a module that
    fails to load is a failure); a test that runs no expect fails. Coverage
    is off. meta["record"] holds JSON-native per-test results: {"tests":
    {name: status}, "failures": [{"name","message","line"}], "passed": n,
    "failed": n, "load_error": message or None, "assertions_required": True};
    meta["scratch"] is the module's path. Modules without JSX use the
    toolchain's plain suffix (.ts) when it has one. `type_check(name)`, if
    given, runs after a failing run whose module loaded (the file still
    exists) and returns text appended to the result. `type_start(name)`
    (JS mode) instead starts that type check alongside the run and returns
    a handle whose finish(keep) gives its text (keep False: stop it).
    """
    if not isinstance(code, str) or not code.strip():
        raise ToolError("code must not be empty")
    runner = runner_for(toolchain)
    if runner is None:
        raise ToolError("No supported test runner is installed; check with bash.")
    root = workspace.root
    directory = (
        root / toolchain.test_dir if toolchain.test_dir not in ("", ".") else root
    )
    scratch_suffix = getattr(toolchain, "scratch_suffix", None)
    suffix = scratch_suffix(code) if scratch_suffix else toolchain.test_suffix
    number = getattr(workspace, "_scratch_count", 0)
    while True:
        number += 1
        name = toolchain.scratch_name(number, suffix)
        if not (root / name).exists():
            break
    workspace._scratch_count = number
    path = root / name
    footer = runner.footer()
    source = code if code.endswith("\n") else code + "\n"
    # The first missing directory on the way (removed again afterwards).
    created_dir = next(
        (
            folder
            for folder in reversed([directory, *directory.parents])
            if folder.is_relative_to(root) and folder != root and not folder.exists()
        ),
        None,
    )
    typed = None
    pending = None
    with tempfile.TemporaryDirectory(prefix="devstill-test-") as folder:
        report = Path(folder) / "report.json"
        try:
            directory.mkdir(parents=True, exist_ok=True)
            path.write_text(source + (footer or ""))
            if type_start is not None:
                pending = type_start(name)
            result = workspace._run(
                [str(root / runner.binary)]
                + runner.files(
                    [name], str(report), display=True, require_assertions=True
                )[1:],
                timeout,
                checkpoint,
                ignore=is_scratch,
                env=runner_env(workspace.env),
            )
            wanted = (
                not result.ok
                and not result.meta.get("timed_out")
                and not _load_failed(_read_report(report))
            )
            if pending is not None:
                typed = pending.finish(wanted)
                pending = None
            elif type_check is not None and wanted:
                typed = type_check(name)
        finally:
            if pending is not None:
                pending.finish(False)
            path.unlink(missing_ok=True)
            for leftover in directory.glob(f"__snapshots__/{path.name}*"):
                leftover.unlink(missing_ok=True)
            snapshots = directory / "__snapshots__"
            if snapshots.is_dir() and not any(snapshots.iterdir()):
                snapshots.rmdir()
            if created_dir is not None:
                shutil.rmtree(created_dir, ignore_errors=True)
        data = _read_report(report)
    described = describe_test_run(
        result, data, root, name, code, assertions_required=True
    )
    if typed:
        described.text += "\n\n" + typed
    described.meta["scratch"] = name
    return described


_copy_numbers = itertools.count(1)
_copy_lock = threading.Lock()


def run_test_module_in(root, env, toolchain, code, timeout, register=None):
    """JS: run_test_module in another tree `root` (the pristine copy of the
    original sources) through execute(): the agent's workspace is never
    touched. The same record and text as run_test_module; meta["scratch"]
    is the module's name in that tree."""
    runner = runner_for(toolchain)
    if runner is None or not isinstance(code, str) or not code.strip():
        return None
    root = Path(root)
    directory = (
        root / toolchain.test_dir if toolchain.test_dir not in ("", ".") else root
    )
    scratch_suffix = getattr(toolchain, "scratch_suffix", None)
    suffix = scratch_suffix(code) if scratch_suffix else toolchain.test_suffix
    with _copy_lock:
        while True:
            name = toolchain.scratch_name(next(_copy_numbers), suffix)
            if not (root / name).exists():
                break
        path = root / name
        directory.mkdir(parents=True, exist_ok=True)
        source = code if code.endswith("\n") else code + "\n"
        path.write_text(source + (runner.footer() or ""))
    processes = []

    def track(process):
        processes.append(process)
        return register(process) if register is not None else True

    try:
        with tempfile.TemporaryDirectory(prefix="devstill-test-") as folder:
            report = Path(folder) / "report.json"
            run = execute(
                [str(root / runner.binary)]
                + runner.files(
                    [name], str(report), display=True, require_assertions=True
                )[1:],
                root,
                runner_env(env),
                timeout,
                track,
            )
            data = _read_report(report) if run and not run["timed_out"] else None
    finally:
        path.unlink(missing_ok=True)
        for leftover in directory.glob(f"__snapshots__/{path.name}*"):
            leftover.unlink(missing_ok=True)
    if run is None:
        return None
    text = run["output"][-OUTPUT_TAIL:]
    if run["timed_out"]:
        result = ToolResult(
            (text + "\n" if text else "") + f"[command timed out after {timeout:.0f}s]",
            ok=False,
            meta={"timed_out": True},
        )
    else:
        result = ToolResult(
            (text.rstrip("\n") + "\n" if text.strip() else "")
            + f"[exit code {run['code']}]",
            ok=run["code"] == 0,
        )
    described = describe_test_run(
        result, data, root, name, code, assertions_required=True
    )
    described.meta["scratch"] = name
    return described


OUTPUT_TAIL = 20000


class ScratchTypes:
    """A scratch module's type check started alongside its test run (JS)."""

    def __init__(self, workspace, toolchain, name, code, timeout=4.0, on_timeout=None):
        self.name, self.code, self.timeout = name, code, timeout
        self.on_timeout = on_timeout
        self.started = time.perf_counter()
        self.process = None
        self.run = None
        self.done = threading.Event()
        self.config = None
        root = Path(workspace.root)
        if (
            not toolchain.tsc_path
            or not (root / "tsconfig.json").is_file()
            or PurePosixPath(name).suffix not in (".ts", ".tsx", ".mts", ".cts")
        ):
            self.done.set()
            return
        project = tsconfig_for(root, name)
        if project is None:
            self.done.set()
            return
        self.root = root
        self.config = root / f"{SCRATCH_PREFIX}tsconfig.json"
        self.config.write_text(_scratch_tsconfig(project, name))
        argv = [
            str(root / toolchain.tsc_path),
            "-p",
            self.config.name,
            "--pretty",
            "false",
        ]
        env = runner_env(workspace.env)

        def register(process):
            self.process = process
            return True

        def work():
            try:
                # finish() stops it; this limit only bounds a forgotten run.
                self.run = execute(argv, root, env, timeout + self.HARD_LIMIT, register)
            finally:
                self.done.set()

        threading.Thread(target=work, daemon=True).start()

    # Seconds a run may go on beyond `timeout` while the test run lasts.
    HARD_LIMIT = 120.0

    def finish(self, keep):
        """Its text (None: no errors, or not kept: the run is stopped). The
        type check gets `timeout` seconds after the test run ends (finish is
        called then), as the serial check after a failing run would, so the
        test run's load never costs it its result; one that does not finish
        by then calls on_timeout() (none given: the text is "timeout")."""
        try:
            if not keep:
                if self.process is not None:
                    _kill(self.process)
                return None
            run = self.run if self.done.wait(timeout=self.timeout + 0.2) else None
            if self.config is None:
                return None
            if run is None or run["timed_out"]:
                if self.process is not None:
                    _kill(self.process)
                if self.on_timeout is not None:
                    self.on_timeout()
                    return None
                return "timeout"
            return _scratch_errors_text(run["output"], self.root, self.name, self.code)
        finally:
            if self.config is not None:
                self.done.wait(timeout=1.0)
                self.config.unlink(missing_ok=True)


def _load_failed(data):
    """The report shows a module that never ran its tests (or no report)."""
    if data is None:
        return True
    return any(
        module.get("status") == "failed" and not module.get("assertionResults")
        for module in data.get("testResults") or []
    )


# Runner globals a project tsconfig may not declare (vitest globals: true).
_RUNNER_GLOBALS = re.compile(
    r"Cannot find name '(?:describe|it|test|expect|vi|jest|beforeEach|afterEach|"
    r"beforeAll|afterAll|suite)'"
)


# Errors about the configuration, not the test: module resolution (the
# module loaded, so the runner resolves it), the JSX flag, options and
# rootDir (TS5xxx/TS6xxx, also unused names), declaration files, and
# runner globals the tsconfig may not declare (TS2582/TS2593).
_CONFIG_LEVEL = re.compile(r"TS(?:2307|2792|7016|17004|6142|2582|2593|[56]\d{3})$")


def scratch_type_errors(workspace, toolchain, name, code, timeout=4.0):
    """Type errors inside the scratch module `name` alone (tsc with the
    project's tsconfig, only that file as root), as text; None when there
    are none, it is not a TypeScript project, or tsc does not finish in
    `timeout` seconds (then "timeout")."""
    root = Path(workspace.root)
    if not toolchain.tsc_path or not (root / "tsconfig.json").is_file():
        return None
    if PurePosixPath(name).suffix not in (".ts", ".tsx", ".mts", ".cts"):
        return None
    # A solution-style tsconfig.json has no compiler options of its own:
    # extend the referenced config that covers the module (none: no check).
    project = tsconfig_for(root, name)
    if project is None:
        return None
    config = root / f"{SCRATCH_PREFIX}tsconfig.json"
    config.write_text(_scratch_tsconfig(project, name))
    try:
        run = execute(
            [str(root / toolchain.tsc_path), "-p", config.name, "--pretty", "false"],
            root,
            runner_env(workspace.env),
            timeout,
        )
    finally:
        config.unlink(missing_ok=True)
    if run is None or run["timed_out"]:
        return "timeout"
    return _scratch_errors_text(run["output"], root, name, code)


def _scratch_tsconfig(project, name):
    return json.dumps(
        {
            "extends": "./" + project,
            "compilerOptions": {
                "noEmit": True,
                "incremental": False,
                "composite": False,
            },
            "files": [name],
            "include": [],
        }
    )


def _scratch_errors_text(output, root, name, code):
    errors = [
        e
        for e in parse_tsc(output, root)
        if e["file"] == name
        and not _CONFIG_LEVEL.match(e["code"])
        and not _RUNNER_GLOBALS.search(e["message"])
    ]
    if not errors:
        return None
    lines = code.split("\n")
    shown = []
    for error in errors[:6]:
        source = (
            lines[error["line"] - 1].strip()[:120]
            if 0 < error["line"] <= len(lines)
            else ""
        )
        shown.append(
            f"- check line {error['line']}: {error['code']} {error['message'][:240]}"
            + (f"\n    {source}" if source else "")
        )
    more = f"\n[{len(errors) - 6} more]" if len(errors) > 6 else ""
    return (
        "Type errors in your test module (tsc): usually a bug in the test (a wrong "
        "setup, a value read with the wrong shape), unless the test uses types "
        "your change still has to declare:\n" + "\n".join(shown) + more
    )


def describe_test_run(result, data, root, name, code, assertions_required=False):
    """Model-facing text and the per-test record of one scratch module run.

    With `assertions_required` the runner failed every test that ran no
    expect, so each passed test asserted something.
    """
    timed_out = bool(result.meta.get("timed_out"))
    output = result.text
    if timed_out or data is None:
        text = output + (
            "\n[the test run did not finish; make the test smaller or faster]"
            if timed_out
            else "\n[the runner wrote no report: the test module did not run]"
        )
        return ToolResult(
            text,
            ok=False,
            changed=result.changed,
            meta={"timed_out": timed_out, "record": None},
        )
    parsed = parse_report(data, root)
    details = failure_details(output)
    lines = code.split("\n")
    prefix = f"{name} > "
    tests, failures = {}, []
    load_error = None
    shown_where = {}  # failure index -> display-only location details
    for test_id, status in parsed["tests"].items():
        short = test_id.removeprefix(prefix)
        if short == LOAD_FAILURE:
            info = parsed["failures"].get(test_id) or {}
            message, location = details.get(name, ("", None))
            load_error = clean_message(
                (info.get("message") or message).replace(str(root) + "/", "")
            )
            line = location[1] if location and location[0].endswith(name) else None
            shown_where[len(failures)] = load_frame(load_error, name, lines, root)
            failures.append({"name": LOAD_FAILURE, "message": load_error, "line": line})
            continue
        tests[short] = status
        if status == "failed":
            info = parsed["failures"].get(test_id) or {}
            message, location = details.get(test_id, ("", None))
            line = _frame_line(info.get("message"), info.get("path"))
            if line is None and location and location[0].endswith(name):
                line = location[1]
            text = clean_message(
                (message or info.get("message") or "").replace(str(root) + "/", "")
            )
            failure = {"name": short, "message": text, "line": line}
            if NO_ASSERTION in text:
                failure["message"] = (
                    "No expect ran in this test (an expect inside a callback that "
                    "never runs checks nothing): make it assert the behavior directly."
                )
            else:
                # Only display: line numbers in sources move with edits, and
                # records are compared across code versions. The record keeps
                # whether it was thrown inside repository source at all.
                where = repo_frames(info.get("message"), root, name)
                shown_where[len(failures)] = where
                if where:
                    failure["source"] = True
            failures.append(failure)
    passed = sum(1 for s in tests.values() if s == "passed")
    failed = sum(1 for s in tests.values() if s == "failed")
    record = {
        "tests": tests,
        "failures": failures,
        "passed": passed,
        "failed": failed,
        "load_error": load_error,
        "assertions_required": assertions_required,
    }
    ok = result.ok and passed > 0 and not failures
    parts = []
    console = console_output(output)
    if console:
        parts.append(console)
    for index, failure in enumerate(failures[:8]):
        where = shown_where.get(index)
        if failure["name"] == LOAD_FAILURE:
            folder = str(PurePosixPath(name).parent)
            block = (
                "The test module failed to load:\n"
                + failure["message"]
                + (f"\n{where}" if where else "")
                + f"\n[the module runs as {name}: relative imports start from "
                + ("the repository root" if folder == "." else folder + "/")
                + "]"
            )
            where = None
        else:
            block = f"FAIL {failure['name']}\n{failure['message']}"
        line = failure.get("line")
        if line and 0 < line <= len(lines):
            block += f"\n  at check line {line}: {lines[line - 1].strip()[:160]}"
        if where:
            block += "\n  " + where
        parts.append(block)
    if len(failures) > 8:
        parts.append(f"[{len(failures) - 8} more failing tests not shown]")
    if ok:
        parts.append(f"{passed} passed")
    elif not failures and passed == 0:
        parts.append(
            "No test ran: define at least one it()/test() with expect calls."
            if not tests
            else "No test passed (all skipped)."
        )
        if not result.ok:
            parts.append(clean_message(output, 1500))
    elif not failures:
        # Every test passed but the run failed (e.g. an unhandled error).
        parts.append(clean_message(output, 1500))
        parts.append(f"[{passed} passed, but the run failed]")
    else:
        parts.append(f"[{failed or len(failures)} failed, {passed} passed]")
    return ToolResult(
        "\n\n".join(p for p in parts if p),
        ok=ok,
        changed=result.changed,
        meta={"timed_out": False, "record": record},
    )


# --- the background guard -------------------------------------------------------


def _usable_tests(result):
    return result is not None and result.get("tests") is not None


def _usable_tsc(result):
    return result is not None and result.get("errors") is not None


def file_of(test_id):
    return test_id.split(" > ", 1)[0]


class JsGuard:
    """Re-run tests (vitest/jest) and the type check after each change.

    A failing test is a regression only if it passed on the pristine sources;
    a type error is new only if the pristine sources do not have it (same
    file, code and message; line numbers ignored). Anything that does not
    finish in time is unknown, never a regression.
    """

    isolated = True  # never runs in the agent's workspace
    # Run the whole suite when the pristine one took this long at most.
    SUITE_FAST = 20.0
    MAX_SHOWN = 10

    def __init__(self, workspace, toolchain, originals, timeout=30.0):
        self.workspace = workspace
        self.toolchain = toolchain
        self.originals = originals
        self.timeout = timeout
        self.runner = runner_for(toolchain)
        self.env = runner_env(workspace.env)
        self.pristine = None
        self.current = None
        self._ready = threading.Event()
        self._lock = threading.Lock()
        self._mirror = threading.Lock()
        self._generation = 0
        self._closed = False
        self._processes = set()  # current-state runs
        self._pristine_processes = set()  # baseline runs in the pristine copy
        self._runs = {}
        self._base = {
            "tests": {"done": threading.Event(), "result": None},
            "tsc": {"done": threading.Event(), "result": None},
        }
        self._reports = Path(tempfile.mkdtemp(prefix="devstill-reports-"))
        self._count = itertools.count()
        self.original = None  # pristine copy for the loop's original-code runs
        self._original_ready = threading.Event()
        # JS loop support: when the current generation started, the last
        # measured duration of each current-state run, the contents last
        # mirrored, and finished results per state (state_key).
        self._generation_started = None
        self._seconds = {}
        self._mirrored = None
        self._key = None
        self._records = {}

    diff = TestGuard.diff

    def js_diff(self, keep=(), limit=3000):
        """JS: the changes so far as a unified diff (1 line of context), cut
        only at hunk boundaries; hunks starting at a (file, new line) in
        `keep` are always shown."""
        return js_diff(self.workspace, self.originals, keep, limit)

    # process bookkeeping

    def _register(self, generation=None):
        """Track a started process; refuse it once closed or superseded.

        Runs tied to a generation (the current state) are killed by the next
        change; pristine runs (generation None) only by close().
        """

        def register(process):
            with self._lock:
                if self._closed or (
                    generation is not None and generation != self._generation
                ):
                    return False
                if generation is None:
                    self._pristine_processes.add(process)
                else:
                    self._processes.add(process)
                return True

        return register

    def _unregister(self, process):
        with self._lock:
            self._processes.discard(process)
            self._pristine_processes.discard(process)

    def _argv(self, root, argv):
        return [str(Path(root) / argv[0]), *argv[1:]]

    def _tests(self, root, make, timeout, generation=None):
        """Run a test command with a JSON report; parsed per-test results."""
        report = self._reports / f"report-{next(self._count)}.json"
        run = execute(
            self._argv(root, make(str(report))),
            root,
            self.env,
            timeout,
            self._register(generation),
            self._unregister,
        )
        if run is None:
            return None
        data = None if run["timed_out"] else _read_report(report)
        report.unlink(missing_ok=True)
        parsed = parse_report(data, root) if data is not None else None
        return {
            "tests": parsed["tests"] if parsed else None,
            "failures": {
                k: clean_message(v["message"].replace(str(root) + "/", ""), 600)
                for k, v in (parsed["failures"] if parsed else {}).items()
            },
            "seconds": run["seconds"],
            "timed_out": run["timed_out"],
            "output": run["output"][-2000:],
        }

    def _tsc(self, root, timeout, generation=None):
        argv = [self.toolchain.tsc_path, "--noEmit", "--pretty", "false"]
        run = execute(
            self._argv(root, argv),
            root,
            self.env,
            timeout,
            self._register(generation),
            self._unregister,
        )
        if run is None:
            return None
        errors = None if run["timed_out"] else parse_tsc(run["output"], root)
        if errors is not None and run["code"] != 0 and not errors:
            errors = None  # failed without diagnostics: unknown
        return {"errors": errors, "seconds": run["seconds"]}

    def _background(self, slot, work):
        def target():
            try:
                result = work()
            except Exception as exc:  # noqa: BLE001 - unknown, never a hang
                result = {"error": str(exc)}
            slot["result"] = result
            slot["done"].set()

        threading.Thread(target=target, daemon=True).start()

    # lifecycle

    def start(self, pristine):
        """Begin with a pristine copy of the sources (dependencies linked).

        Baselines (whole suite, type check) start there at once; a mirror
        copy for current-state runs is made next to it.
        """
        pristine = Path(pristine)
        with self._lock:
            if self._closed:
                return
        current = pristine.with_name(pristine.name + "-current")
        shutil.copytree(pristine, current, symlinks=True)
        self.pristine, self.current = pristine, current
        self._ready.set()
        # A second pristine copy where the loop runs test modules on the
        # original code, at the same time as runs on the edited code.
        original = pristine.with_name(pristine.name + "-original")
        try:
            shutil.copytree(pristine, original, symlinks=True)
        except OSError:
            shutil.rmtree(original, ignore_errors=True)
        else:
            self.original = original
            self._original_ready.set()
        if self.runner is not None:
            self._background(
                self._base["tests"],
                lambda: self._tests(pristine, self.runner.suite, self.timeout * 2),
            )
        else:
            self._base["tests"]["done"].set()
        if self.toolchain.tsc_path:
            self._background(
                self._base["tsc"], lambda: self._tsc(pristine, self.timeout * 2)
            )
        else:
            self._base["tsc"]["done"].set()

    def original_root(self, wait=0.0):
        """The pristine copy for original-code runs, None while not ready."""
        if self._original_ready.wait(timeout=max(0.0, wait)):
            return self.original
        return None

    def contents(self):
        """Bytes of every changed file in the workspace now (None: absent)."""
        found = {}
        for name in list(self.workspace.changed):
            path = self.workspace.root / name
            try:
                found[name] = path.read_bytes() if path.is_file() else None
            except OSError:
                found[name] = None
        return found

    def stale(self, contents=None):
        """The mirror was last synced from other contents than the
        workspace's now (a change the loop never reported)."""
        with self._lock:
            mirrored = self._mirrored
        if mirrored is None:
            return False
        return mirrored != (self.contents() if contents is None else contents)

    def key(self, contents):
        return state_key(contents, self.originals)

    def changed(self):
        """(Re)start current-state runs for the workspace as it is now."""
        names = list(self.workspace.changed)
        contents = self.contents()
        code = [
            n
            for n in names
            if PurePosixPath(n).suffix in JS_SUFFIXES or n.endswith(".json")
        ]
        key = self.key(contents)
        with self._lock:
            if self._closed:
                return
            self._generation += 1
            generation = self._generation
            stale = list(self._processes)
            runs = {}
            if self.runner is not None and code:
                runs["tests"] = {"done": threading.Event(), "result": None}
            if self.toolchain.tsc_path and code:
                runs["tsc"] = {"done": threading.Event(), "result": None}
            self._runs = runs
            self._generation_started = time.perf_counter()
            self._mirrored = contents
            self._key = key
            record = self._records.setdefault(
                key, {"expected": set(runs), "results": {}, "files": set(names)}
            )
            record["expected"] |= set(runs)
        for process in stale:
            _kill(process)
        if runs:
            threading.Thread(
                target=self._work, args=(generation, contents, runs, key), daemon=True
            ).start()

    def eta(self, parts=("tsc",)):
        """Seconds until the current state's runs in `parts` are expected to
        finish (0.0: finished or none), from the last measured duration of
        each (the pristine baseline's before any); None when unknown."""
        with self._lock:
            runs = dict(self._runs)
            started = self._generation_started
        waits = []
        for part in parts:
            run = runs.get(part)
            if run is None or run["done"].is_set():
                continue
            base = self._base.get(part)
            if base is None or not base["done"].is_set() or started is None:
                return None
            seconds = self._seconds.get(part)
            if seconds is None:
                seconds = (base["result"] or {}).get("seconds")
            if seconds is None:
                return None
            waits.append(started + seconds - time.perf_counter())
        return max([0.0, *waits])

    def _stale(self, generation):
        with self._lock:
            return self._closed or generation != self._generation

    def _work(self, generation, contents, runs, state=None):
        if not self._ready.wait(timeout=self.timeout):
            return
        with self._mirror:
            if self._stale(generation):
                return
            for name, data in contents.items():
                target = self.current / name
                if data is None:
                    target.unlink(missing_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(data)
            threads = []
            for key, run in runs.items():
                work = (
                    (
                        lambda: self._tests(
                            self.current,
                            self._selection(contents),
                            self.timeout,
                            generation,
                        )
                    )
                    if key == "tests"
                    else (lambda: self._tsc(self.current, self.timeout, generation))
                )

                def target(run=run, work=work, part=key):
                    try:
                        result = work()
                    except Exception as exc:  # noqa: BLE001
                        result = {"error": str(exc)}
                    with self._lock:
                        if generation == self._generation and result is not None:
                            run["result"] = result
                            run["done"].set()
                            if isinstance(result, dict):
                                if state is not None and state in self._records:
                                    self._records[state]["results"][part] = result
                                if result.get("seconds") is not None and not result.get(
                                    "timed_out"
                                ):
                                    self._seconds[part] = result["seconds"]

                thread = threading.Thread(target=target, daemon=True)
                thread.start()
                threads.append(thread)
            for thread in threads:
                thread.join()

    def _selection(self, contents):
        """The whole suite when it is fast, else tests related to the change."""
        base = self._base["tests"]
        result = base["result"] if base["done"].is_set() else None
        if (
            _usable_tests(result)
            and not result.get("timed_out")
            and result["seconds"] <= self.SUITE_FAST
        ):
            return self.runner.suite
        names = [
            n
            for n in contents
            if PurePosixPath(n).suffix in JS_SUFFIXES and not is_scratch(n)
        ]
        return lambda report: self.runner.related(names, report)

    def close(self):
        with self._lock:
            self._closed = True
            self._generation += 1
            processes = list(self._processes | self._pristine_processes)
            self._runs = {}
        for process in processes:
            _kill(process)

    def cleanup(self):
        self.close()
        for path in (self.current, self.original, self._reports):
            if path is not None:
                shutil.rmtree(path, ignore_errors=True)

    # results

    def _baseline_tests(self, failing, end, deadline):
        """Pristine outcomes for the failing tests' files (None if unknown)."""
        base = self._base["tests"]
        if base["done"].wait(timeout=max(0.0, end - time.perf_counter())):
            result = base["result"]
            if _usable_tests(result) and not result.get("timed_out"):
                return result["tests"]
        if self.pristine is None or self.runner is None:
            return None
        budget = self.timeout
        if deadline is not None:
            budget = min(budget, deadline - time.perf_counter() - 1.0)
        if budget < 3.0:
            return None
        files = sorted(
            {file_of(t) for t in failing if (self.pristine / file_of(t)).is_file()}
        )
        if not files:
            return {}
        result = self._tests(
            self.pristine, lambda report: self.runner.files(files, report), budget
        )
        return result["tests"] if _usable_tests(result) else None

    def regressions(self, wait, deadline=None):
        """New test failures and type errors, or None (none, or unknown)."""
        runs = dict(self._runs)
        if not runs:
            return None
        started = time.perf_counter()
        if deadline is not None:
            wait = min(wait, max(0.0, deadline - started - 2.0))
        end = started + max(0.0, wait)

        def finished(run):
            if run is None:
                return None
            if run["done"].wait(timeout=max(0.0, end - time.perf_counter())):
                return run["result"]
            return None

        tests, types = finished(runs.get("tests")), finished(runs.get("tsc"))
        found = {"regressions": [], "added": [], "type_errors": [], "output": ""}
        shown = []
        if _usable_tests(tests) and not tests.get("timed_out"):
            failing = sorted(t for t, s in tests["tests"].items() if s == "failed")
            baseline = self._baseline_tests(failing, end, deadline) if failing else None
            if baseline is not None:
                new, added = self._compare(failing, baseline)
                if new or added:
                    new, added, messages = self._confirm(new, added, tests, deadline)
                    found["regressions"], found["added"] = new, added
                    for name in (new + added)[: self.MAX_SHOWN]:
                        shown.append(f"FAIL {name}\n{messages.get(name, '')}".rstrip())
        if _usable_tsc(types):
            base = self._base["tsc"]
            if base["done"].wait(timeout=max(0.0, end - time.perf_counter())):
                before = base["result"]
                if _usable_tsc(before):
                    fresh = excess_type_errors(
                        before["errors"], types["errors"], self.pristine, self.current
                    )
                    found["type_errors"] = [tsc_text(e) for e in fresh]
                    found["type_error_items"] = fresh
                    if fresh:
                        shown.append(
                            "tsc --noEmit:\n"
                            + "\n".join(tsc_text(e) for e in fresh[:15])
                        )
        if not (found["regressions"] or found["added"] or found["type_errors"]):
            return None
        found["output"] = "\n\n".join(shown)
        return found

    def peek(self, wait=0.0, parts=None):
        """What the current state's runs found so far (no confirming re-runs),
        waiting up to `wait` seconds for the runs named in `parts` ("tsc",
        "tests"; default all) and their baselines; None when nothing runs.

        {"generation", "pending": parts still running, "type_errors": new
        errors (None: unknown), "regressions"/"added": failing tests (None:
        unknown), "messages": {test: message}}.
        """
        with self._lock:
            runs = dict(self._runs)
            generation = self._generation
        if not runs:
            return None
        end = time.perf_counter() + max(0.0, wait)
        for key, run in runs.items():
            if parts is None or key in parts:
                run["done"].wait(timeout=max(0.0, end - time.perf_counter()))
                self._base[key]["done"].wait(
                    timeout=max(0.0, end - time.perf_counter())
                )
        found = {
            "generation": generation,
            "pending": [],
            "type_errors": None,
            "regressions": None,
            "added": None,
            "messages": {},
        }
        types = runs.get("tsc")
        if types is not None:
            base = self._base["tsc"]
            if not (types["done"].is_set() and base["done"].is_set()):
                found["pending"].append("tsc")
            elif _usable_tsc(types["result"]) and _usable_tsc(base["result"]):
                fresh = excess_type_errors(
                    base["result"]["errors"],
                    types["result"]["errors"],
                    self.pristine,
                    self.current,
                )
                found["type_errors"] = [tsc_text(e) for e in fresh]
                found["type_error_items"] = fresh
        tests = runs.get("tests")
        if tests is not None:
            base = self._base["tests"]
            if not (tests["done"].is_set() and base["done"].is_set()):
                found["pending"].append("tests")
            else:
                now, before = tests["result"], base["result"]
                if (
                    _usable_tests(now)
                    and not now.get("timed_out")
                    and _usable_tests(before)
                    and not before.get("timed_out")
                ):
                    failing = sorted(
                        t for t, s in now["tests"].items() if s == "failed"
                    )
                    new, added = self._compare(failing, before["tests"])
                    found["regressions"], found["added"] = new, added
                    found["messages"] = {
                        k: v
                        for k, v in (now.get("failures") or {}).items()
                        if k in new or k in added
                    }
        return found

    def verdict(self, key):
        """The recorded guard verdict of the state `key` (state_key), from
        runs that finished while it was current: {"complete": every run
        finished with a usable result and its baseline is known, "clean":
        complete and nothing new fails, "type_errors", "regressions",
        "added"}; None when no run was ever started for it."""
        with self._lock:
            record = self._records.get(key)
            if record is None:
                return None
            expected = set(record["expected"])
            results = dict(record["results"])
            files = set(record["files"])
        found = {
            "complete": True,
            "type_errors": [],
            "regressions": [],
            "added": [],
            "runs": sorted(expected),
        }
        if "tsc" in expected:
            now, base = results.get("tsc"), self._base["tsc"]
            before = base["result"] if base["done"].is_set() else None
            if _usable_tsc(now) and _usable_tsc(before):
                fresh = excess_type_errors(
                    before["errors"], now["errors"], self.pristine, self.current
                )
                found["type_errors"] = [tsc_text(e) for e in fresh]
            else:
                found["complete"] = False
        if "tests" in expected:
            now, base = results.get("tests"), self._base["tests"]
            before = base["result"] if base["done"].is_set() else None
            if (
                _usable_tests(now)
                and not now.get("timed_out")
                and _usable_tests(before)
                and not before.get("timed_out")
            ):
                failing = sorted(t for t, s in now["tests"].items() if s == "failed")
                found["regressions"], found["added"] = self._compare(
                    failing, before["tests"], files
                )
            else:
                found["complete"] = False
        found["clean"] = found["complete"] and not (
            found["type_errors"] or found["regressions"] or found["added"]
        )
        return found

    def _compare(self, failing, baseline, edited=None):
        def passed_before(test):
            if test.endswith(f" > {LOAD_FAILURE}"):
                prefix = file_of(test) + " > "
                return any(
                    s == "passed" and t.startswith(prefix) for t, s in baseline.items()
                )
            return baseline.get(test) == "passed"

        edited = set(self.workspace.changed if edited is None else edited)
        new = [t for t in failing if passed_before(t)]
        added = [
            t
            for t in failing
            if t not in baseline and not passed_before(t) and file_of(t) in edited
        ]
        return new, added

    def _confirm(self, new, added, tests, deadline):
        """Re-run the failing files on the current state; keep what still fails."""
        messages = dict(tests.get("failures") or {})
        budget = self.timeout
        if deadline is not None:
            budget = min(budget, deadline - time.perf_counter() - 1.0)
        files = sorted({file_of(t) for t in new + added})
        if budget < 3.0 or self.current is None:
            return new, added, messages
        with self._lock:
            generation = self._generation
        result = self._tests(
            self.current,
            lambda report: self.runner.files(files, report),
            budget,
            generation,
        )
        if not _usable_tests(result) or result.get("timed_out"):
            return new, added, messages
        still = {t for t, s in result["tests"].items() if s == "failed"}
        messages.update(result.get("failures") or {})
        return (
            [t for t in new if t in still],
            [t for t in added if t in still],
            messages,
        )

    def type_errors_beyond_original(self, errors, root, timeout):
        """Of `errors` (an agent's own tsc run in `root`), those the pristine
        sources do not have; None while the baseline is unknown."""
        base = self._base["tsc"]
        if not base["done"].wait(timeout=max(0.0, timeout)):
            return None
        before = base["result"]
        if not _usable_tsc(before):
            return None
        return excess_type_errors(before["errors"], errors, self.pristine, root)

    def known_failures(self, failing, timeout):
        """`failing` (test ids from an agent's own test run) when all of them
        fail on the pristine sources too; [] when any is new or unknown."""
        failing = sorted(set(failing))
        if not failing:
            return []
        base = self._base["tests"]
        if not base["done"].wait(timeout=max(0.0, timeout)):
            return []
        result = base["result"]
        if not _usable_tests(result):
            return []
        tests = result["tests"]
        return failing if all(tests.get(t) == "failed" for t in failing) else []


def pristine_copy(repo, directory, config, names):
    """A pristine snapshot of the sources (dependencies linked) for baselines."""
    from devstill.runtime import workspace as workspace_module

    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    workspace_module.snapshot(repo, directory, config, True)
    link_dependencies(repo, directory, names)
    return directory
