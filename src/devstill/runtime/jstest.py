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

from devstill.runtime.toolchain import JS_SUFFIXES, is_scratch, link_dependencies
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


def run_test_module(workspace, toolchain, code, timeout, checkpoint=lambda: None):
    """Write `code` to a scratch test file, run it alone, always delete it.

    ok only if at least one test passed and nothing failed (a module that
    fails to load is a failure); a test that runs no expect fails. Coverage
    is off. meta["record"] holds JSON-native per-test results: {"tests":
    {name: status}, "failures": [{"name","message","line"}], "passed": n,
    "failed": n, "load_error": message or None, "assertions_required": True}.
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
    number = getattr(workspace, "_scratch_count", 0)
    while True:
        number += 1
        name = toolchain.scratch_name(number)
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
    with tempfile.TemporaryDirectory(prefix="devstill-test-") as folder:
        report = Path(folder) / "report.json"
        try:
            directory.mkdir(parents=True, exist_ok=True)
            path.write_text(source + (footer or ""))
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
        finally:
            path.unlink(missing_ok=True)
            for leftover in directory.glob(f"__snapshots__/{path.name}*"):
                leftover.unlink(missing_ok=True)
            snapshots = directory / "__snapshots__"
            if snapshots.is_dir() and not any(snapshots.iterdir()):
                snapshots.rmdir()
            if created_dir is not None:
                shutil.rmtree(created_dir, ignore_errors=True)
        data = _read_report(report)
    return describe_test_run(result, data, root, name, code, assertions_required=True)


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
    for test_id, status in parsed["tests"].items():
        short = test_id.removeprefix(prefix)
        if short == LOAD_FAILURE:
            info = parsed["failures"].get(test_id) or {}
            message, location = details.get(name, ("", None))
            load_error = clean_message(
                (info.get("message") or message).replace(str(root) + "/", "")
            )
            line = location[1] if location and location[0].endswith(name) else None
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
            if NO_ASSERTION in text:
                text = (
                    "No expect ran in this test (an expect inside a callback that "
                    "never runs checks nothing): make it assert the behavior directly."
                )
            failures.append({"name": short, "message": text, "line": line})
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
    for failure in failures[:8]:
        if failure["name"] == LOAD_FAILURE:
            folder = str(PurePosixPath(name).parent)
            block = (
                "The test module failed to load:\n"
                + failure["message"]
                + f"\n[the module runs as {name}: relative imports start from "
                + ("the repository root" if folder == "." else folder + "/")
                + "]"
            )
        else:
            block = f"FAIL {failure['name']}\n{failure['message']}"
        line = failure.get("line")
        if line and 0 < line <= len(lines):
            block += f"\n  at check line {line}: {lines[line - 1].strip()[:160]}"
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

    diff = TestGuard.diff

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

    def changed(self):
        """(Re)start current-state runs for the workspace as it is now."""
        names = list(self.workspace.changed)
        contents = {}
        for name in names:
            path = self.workspace.root / name
            try:
                contents[name] = path.read_bytes() if path.is_file() else None
            except OSError:
                contents[name] = None
        code = [
            n
            for n in names
            if PurePosixPath(n).suffix in JS_SUFFIXES or n.endswith(".json")
        ]
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
        for process in stale:
            _kill(process)
        if runs:
            threading.Thread(
                target=self._work, args=(generation, contents, runs), daemon=True
            ).start()

    def _stale(self, generation):
        with self._lock:
            return self._closed or generation != self._generation

    def _work(self, generation, contents, runs):
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

                def target(run=run, work=work):
                    try:
                        result = work()
                    except Exception as exc:  # noqa: BLE001
                        result = {"error": str(exc)}
                    with self._lock:
                        if generation == self._generation and result is not None:
                            run["result"] = result
                            run["done"].set()

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
        for path in (self.current, self._reports):
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
                    if fresh:
                        shown.append(
                            "tsc --noEmit:\n"
                            + "\n".join(tsc_text(e) for e in fresh[:15])
                        )
        if not (found["regressions"] or found["added"] or found["type_errors"]):
            return None
        found["output"] = "\n\n".join(shown)
        return found

    def _compare(self, failing, baseline):
        def passed_before(test):
            if test.endswith(f" > {LOAD_FAILURE}"):
                prefix = file_of(test) + " > "
                return any(
                    s == "passed" and t.startswith(prefix) for t, s in baseline.items()
                )
            return baseline.get(test) == "passed"

        edited = set(self.workspace.changed)
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
