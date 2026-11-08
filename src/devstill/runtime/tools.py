"""Workspace tools for the Quick agent loop: read, edit, write and bash.

Edit semantics follow the familiar exact-replacement contract (every oldText
must match one unique, non-overlapping region of the original file), with a
whitespace/Unicode-tolerant fallback and diagnostics that quote the closest
current text so a failed match can be repaired in one step.
"""

from __future__ import annotations

import difflib
import json
import os
import re
import signal
import subprocess
import tempfile
import threading
import time
import unicodedata
from dataclasses import dataclass, field
from itertools import pairwise
from pathlib import Path

READ_DEFAULT_LINES = 400
READ_MAX_CHARS = 40_000
OUTPUT_MAX_CHARS = 6_000
# write may replace a whole existing file only when it is this small.
OVERWRITE_MAX_LINES = 40

_TRANSLATE = str.maketrans(
    {
        "‘": "'",
        "’": "'",
        "‚": "'",
        "‛": "'",
        "“": '"',
        "”": '"',
        "„": '"',
        "‟": '"',
        "‐": "-",
        "‑": "-",
        "‒": "-",
        "–": "-",
        "—": "-",
        "−": "-",
        " ": " ",
        " ": " ",
        " ": " ",
        "　": " ",
    }
)


class ToolError(ValueError):
    """A tool failure reported back to the model; nothing was written."""


# Runs a check script; a failing `assert left <op> right` also prints both sides
# so a wrong expectation and a wrong implementation are easy to tell apart.
PYTHON_RUNNER = r"""
import ast, sys, traceback
path = sys.argv[1]
record = sys.argv[2] if len(sys.argv) > 2 else None
source = open(path, encoding="utf-8").read()
namespace = {"__name__": "__main__", "__file__": path}
sys.argv = [path]
try:
    exec(compile(source, path, "exec"), namespace)
except AssertionError as exc:
    tb, line, frame = exc.__traceback__, None, None
    while tb is not None:
        if tb.tb_frame.f_code.co_filename == path:
            line, frame = tb.tb_lineno, tb.tb_frame
        tb = tb.tb_next
    traceback.print_exc()
    if line is not None:
        try:
            for node in ast.walk(ast.parse(source)):
                if isinstance(node, ast.Assert) and node.lineno <= line <= (node.end_lineno or node.lineno):
                    test = node.test
                    if isinstance(test, ast.Compare) and len(test.ops) == 1:
                        scope = dict(frame.f_globals)
                        scope.update(frame.f_locals)
                        left = eval(compile(ast.Expression(test.left), path, "eval"), scope)
                        right = eval(compile(ast.Expression(test.comparators[0]), path, "eval"), scope)
                        print(f"  left:  {left!r}\n  right: {right!r}", file=sys.stderr)
                        if record:
                            import json
                            with open(record, "w", encoding="utf-8") as out:
                                json.dump({
                                    "left_src": ast.unparse(test.left),
                                    "right_src": ast.unparse(test.comparators[0]),
                                    "op": type(test.ops[0]).__name__,
                                    "left": repr(left)[:300],
                                    "right": repr(right)[:300],
                                }, out)
                    break
        except Exception:
            pass
    sys.exit(1)
"""


@dataclass
class ToolResult:
    text: str
    ok: bool = True
    changed: list[str] = field(default_factory=list)
    # Structured details for the loop (not shown to the model), e.g. the
    # comparison of a failing check assertion.
    meta: dict = field(default_factory=dict)


def _normal_line(line: str) -> str:
    return unicodedata.normalize("NFKC", line).translate(_TRANSLATE).rstrip()


def _line_starts(text: str) -> list[int]:
    starts = [0]
    for index, char in enumerate(text):
        if char == "\n":
            starts.append(index + 1)
    return starts


def _line_number(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _fuzzy_spans(content: str, old: str) -> list[tuple[int, int]]:
    """Whole-line matches ignoring trailing whitespace and Unicode punctuation.

    Leading indentation stays significant. Used only when no exact match exists.
    """
    old_lines = old.split("\n")
    if old_lines and old_lines[-1] == "":
        old_lines = old_lines[:-1]
    if not old_lines or not any(line.strip() for line in old_lines):
        return []
    lines = content.split("\n")
    target = [_normal_line(line) for line in old_lines]
    normal = [_normal_line(line) for line in lines]
    starts = _line_starts(content)
    spans = []
    width = len(target)
    for index in range(len(lines) - width + 1):
        if normal[index : index + width] == target:
            begin = starts[index]
            last = index + width - 1
            end = starts[last] + len(lines[last])
            spans.append((begin, end))
    if spans or width != 1:
        return spans
    # A single partial line: tolerate only punctuation/space substitutions,
    # which keep character offsets aligned with the original text.
    needle = old_lines[0].translate(_TRANSLATE).rstrip()
    if not needle.strip():
        return []
    haystack = content.translate(_TRANSLATE)
    position = haystack.find(needle)
    while position != -1:
        spans.append((position, position + len(needle)))
        position = haystack.find(needle, position + 1)
    return spans


def _closest(content: str, old: str, limit: int = 12) -> str:
    """Quote the most similar current region so the model can copy it exactly."""
    lines = content.split("\n")
    old_lines = [line for line in old.strip("\n").split("\n")] or [old]
    width = max(1, min(len(old_lines), 40))
    best, best_index = 0.0, None
    probe = "\n".join(old_lines)
    for index in range(max(1, len(lines) - width + 1)):
        window = "\n".join(lines[index : index + width])
        if abs(len(window) - len(probe)) > max(200, len(probe)):
            continue
        ratio = difflib.SequenceMatcher(
            None, window, probe, autojunk=False
        ).quick_ratio()
        if ratio <= best:
            continue
        ratio = difflib.SequenceMatcher(None, window, probe, autojunk=False).ratio()
        if ratio > best:
            best, best_index = ratio, index
    if best_index is None or best < 0.5:
        return ""
    end = min(len(lines), best_index + max(width, 1))
    shown = lines[best_index : min(end, best_index + limit)]
    return (
        f"Closest current text (lines {best_index + 1}-{best_index + len(shown)}):\n"
        + "\n".join(shown)
    )


def apply_text_edits(content: str, edits: list[dict], name: str) -> str:
    """Apply edits matched against the ORIGINAL content; raise ToolError on any problem."""
    if not edits:
        raise ToolError("edits must contain at least one {oldText, newText} item.")
    spans = []
    for index, edit in enumerate(edits):
        old, new = edit["oldText"], edit["newText"]
        label = f"edits[{index}]" if len(edits) > 1 else "oldText"
        if old == "":
            raise ToolError(
                f"{label} must not be empty in {name}. Use write to create a file."
            )
        if old == new:
            raise ToolError(
                f"{label}: newText is identical to oldText; nothing to change."
            )
        count = content.count(old)
        if count == 1:
            begin = content.index(old)
            spans.append((begin, begin + len(old), new, index))
            continue
        if count > 1:
            lines = []
            position = content.find(old)
            while position != -1 and len(lines) < 6:
                lines.append(str(_line_number(content, position)))
                position = content.find(old, position + 1)
            raise ToolError(
                f"{label} matches {count} places in {name} (lines {', '.join(lines)}). "
                "Include more surrounding lines so it is unique."
            )
        fuzzy = _fuzzy_spans(content, old)
        if len(fuzzy) == 1:
            begin, end = fuzzy[0]
            # Fuzzy spans exclude the final newline; keep the file's own.
            replacement = new[:-1] if old.endswith("\n") and new.endswith("\n") else new
            spans.append((begin, end, replacement, index))
            continue
        if len(fuzzy) > 1:
            raise ToolError(
                f"{label} matches {len(fuzzy)} places in {name} after ignoring "
                "trailing whitespace. Include more surrounding lines so it is unique."
            )
        hint = _closest(content, old)
        raise ToolError(
            f"{label} was not found in {name}. It must match the current file exactly, "
            "including indentation." + (f"\n{hint}" if hint else "")
        )
    spans.sort()
    for (_, end, _, first), (begin, _, _, second) in pairwise(spans):
        if begin < end:
            raise ToolError(
                f"edits[{first}] and edits[{second}] overlap in {name}. "
                "Merge them into one edit."
            )
    result = content
    for begin, end, new, _ in reversed(spans):
        result = result[:begin] + new + result[end:]
    return result


def _render_changed(before: str, after: str, limit: int = 24) -> str:
    """Show the edited region of the new file (with line numbers) for confirmation."""
    old_lines = before.split("\n")
    new_lines = after.split("\n")
    matcher = difflib.SequenceMatcher(None, old_lines, new_lines, autojunk=False)
    blocks = [op for op in matcher.get_opcodes() if op[0] != "equal"]
    if not blocks:
        return ""
    first = max(0, blocks[0][3] - 1)
    last = min(len(new_lines), blocks[-1][4] + 1)
    if last - first > limit:
        last = first + limit
    # No per-line numbers: models copy shown text into later oldText values.
    body = "\n".join(new_lines[first:last])
    return f"Lines {first + 1}-{last} now read:\n{body}"


def _check_python(name: str, content: str) -> None:
    if not name.endswith(".py"):
        return
    try:
        compile(content, name, "exec")
    except SyntaxError as exc:
        lines = content.split("\n")
        number = exc.lineno or 1
        first = max(0, number - 3)
        shown = "\n".join(lines[first : min(len(lines), number + 2)])
        raise ToolError(
            f"Rejected: {name} would not compile ({exc.msg}, line {number}). "
            f"No changes were written. Lines {first + 1}-{min(len(lines), number + 2)} "
            f"of the result would be:\n{shown}"
        ) from None


def _decode(raw: bytes, name: str) -> tuple[str, bool]:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise ToolError(f"{name} is not UTF-8 text.") from None
    text = text.removeprefix("﻿")
    crlf = "\r\n" in text and "\n" not in text.replace("\r\n", "")
    return (text.replace("\r\n", "\n") if crlf else text), crlf


class Workspace:
    """File and shell operations confined to one prepared workspace copy."""

    def __init__(
        self, root: Path, *, guard=lambda name: None, resolve=None, env=None, tracked=()
    ):
        self.root = root
        self.guard = guard
        self.resolve = resolve or (lambda name: root / name)
        self.env = env or dict(os.environ)
        self.changed: list[str] = []
        # Editable files whose modification by a command (e.g. sed -i) counts
        # as a proposed change, like an edit made with the edit tool.
        self.tracked = list(tracked)

    def _stat_tracked(self) -> dict[str, tuple[int, int] | None]:
        states = {}
        for name in self.tracked:
            try:
                info = (self.root / name).stat()
                states[name] = (info.st_mtime_ns, info.st_size)
            except OSError:
                states[name] = None
        return states

    def _note(self, name: str) -> None:
        if name not in self.changed:
            self.changed.append(name)

    def read(
        self, path: str, offset: int | None = None, limit: int | None = None
    ) -> ToolResult:
        target = self.resolve(path)
        if not target.is_file():
            raise ToolError(f"File not found: {path}")
        text, _ = _decode(target.read_bytes(), path)
        lines = text.split("\n")
        if lines and lines[-1] == "":
            lines = lines[:-1]
        total = len(lines)
        start = max(1, int(offset or 1))
        if start > max(total, 1):
            raise ToolError(
                f"Offset {start} is beyond end of file ({total} lines total)."
            )
        count = int(limit) if limit else READ_DEFAULT_LINES
        count = max(1, count)
        end = min(total, start + count - 1)
        shown = lines[start - 1 : end]
        body = "\n".join(shown)
        if len(body) > READ_MAX_CHARS:
            body = body[:READ_MAX_CHARS]
            end = start + body.count("\n")
        header = f"[{path} lines {start}-{end} of {total}]"
        footer = (
            f"\n[{total - end} more lines; use offset={end + 1} to continue]"
            if end < total
            else ""
        )
        return ToolResult(f"{header}\n{body}{footer}")

    def edit(self, path: str, edits: list[dict]) -> ToolResult:
        self.guard(path)
        target = self.resolve(path)
        if not target.is_file():
            raise ToolError(f"File not found: {path}. Use write to create a new file.")
        raw = target.read_bytes()
        bom = raw.startswith(b"\xef\xbb\xbf")
        text, crlf = _decode(raw, path)
        updated = apply_text_edits(text, edits, path)
        _check_python(path, updated)
        output = updated.replace("\n", "\r\n") if crlf else updated
        target.write_bytes((b"\xef\xbb\xbf" if bom else b"") + output.encode("utf-8"))
        self._note(path)
        shown = _render_changed(text, updated)
        noun = "block" if len(edits) == 1 else "blocks"
        return ToolResult(
            f"Edited {path}: replaced {len(edits)} {noun}."
            + (f"\n{shown}" if shown else ""),
            changed=[path],
        )

    def write(self, path: str, content: str, append: bool = False) -> ToolResult:
        self.guard(path)
        target = self.resolve(path)
        existed = target.exists()
        crlf = False
        original = ""
        if existed:
            original, crlf = _decode(target.read_bytes(), path)
        if append:
            if original and not original.endswith("\n"):
                original += "\n"
            content = original + content
        elif existed and original.count("\n") > OVERWRITE_MAX_LINES:
            raise ToolError(
                f"{path} already exists ({original.count(chr(10))} lines); write would "
                "replace all of it. Use edit to change part of it, or write with "
                "append=true to add to its end."
            )
        _check_python(path, content)
        target.parent.mkdir(parents=True, exist_ok=True)
        output = content.replace("\r\n", "\n")
        if crlf:
            output = output.replace("\n", "\r\n")
        target.write_bytes(output.encode("utf-8"))
        self._note(path)
        lines = content.count("\n") + (
            0 if content.endswith("\n") or not content else 1
        )
        if append and existed:
            verb = "Appended to"
        else:
            verb = "Overwrote" if existed else "Created"
        return ToolResult(f"{verb} {path} (now {lines} lines).", changed=[path])

    def python(self, code: str, timeout: float, checkpoint=lambda: None) -> ToolResult:
        """Run a Python script in the repository root with the project interpreter."""
        if not code.strip():
            raise ToolError("code must not be empty")
        with tempfile.TemporaryDirectory(prefix="devstill-check-") as folder:
            runner = Path(folder) / "runner.py"
            script = Path(folder) / "check.py"
            record = Path(folder) / "failed-assert.json"
            runner.write_text(PYTHON_RUNNER)
            script.write_text(code)
            result = self._run(
                ["python", str(runner), str(script), str(record)], timeout, checkpoint
            )
            if record.is_file():
                try:
                    result.meta["failed_assert"] = json.loads(record.read_text())
                except ValueError:
                    pass
            return result

    def bash(self, command: str, timeout: float, checkpoint=lambda: None) -> ToolResult:
        if not command.strip():
            raise ToolError("command must not be empty")
        return self._run(["bash", "-c", command], timeout, checkpoint)

    def _run(self, argv, timeout: float, checkpoint=lambda: None) -> ToolResult:
        before = self._stat_tracked()
        started = time.perf_counter()
        with tempfile.TemporaryFile() as output:
            process = subprocess.Popen(
                argv,
                cwd=self.root,
                env=self.env,
                stdin=subprocess.DEVNULL,
                stdout=output,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            timed_out = False
            try:
                while True:
                    try:
                        process.wait(timeout=0.05)
                        break
                    except subprocess.TimeoutExpired:
                        checkpoint()
                        if time.perf_counter() - started >= timeout:
                            timed_out = True
                            break
            finally:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass
                process.wait()
            output.seek(0)
            raw = output.read()
        text = raw.decode("utf-8", errors="replace")
        if len(text) > OUTPUT_MAX_CHARS:
            text = (
                f"[output truncated; last {OUTPUT_MAX_CHARS} chars]\n"
                + text[-OUTPUT_MAX_CHARS:]
            )
        after = self._stat_tracked()
        modified = []
        for name, state in after.items():
            if state is None or state == before.get(name):
                continue
            try:
                self.guard(name)
            except ValueError:
                continue
            modified.append(name)
            self._note(name)
        note = (
            f"\n[files changed by this command: {', '.join(modified)}]"
            if modified
            else ""
        )
        if timed_out:
            return ToolResult(
                (text + "\n" if text else "")
                + f"[command timed out after {timeout:.0f}s]"
                + note,
                ok=False,
                changed=modified,
            )
        code = process.returncode
        status = f"[exit code {code}]"
        return ToolResult(
            (text.rstrip("\n") + "\n" if text.strip() else "") + status + note,
            ok=code == 0,
            changed=modified,
        )


_FAILURE = re.compile(r"^(FAILED|ERROR) (\S+)", re.MULTILINE)


class TestGuard:
    """Run related existing tests in the background after each change.

    The latest run is awaited when the agent finishes. Tests that fail after
    the change are re-run against the original sources (restored briefly);
    only tests that passed before count as regressions.
    """

    def __init__(self, workspace: Workspace, files, originals, timeout=30.0):
        """files: test paths, or a callable mapping changed paths to test paths."""
        self.workspace = workspace
        self.select = files if callable(files) else (lambda changed: list(files))
        self.files = []
        self.originals = originals  # name -> original bytes, or None if new
        self.timeout = timeout
        self._lock = threading.Lock()
        self._generation = 0
        self._result = None
        self._done = threading.Event()
        self._process = None
        self._thread = None

    def _argv(self, targets):
        return ["python", "-m", "pytest", "-q", "-p", "no:cacheprovider", *targets]

    def _execute(self, targets, timeout, generation=None):
        started = time.perf_counter()
        with tempfile.TemporaryFile() as output:
            process = subprocess.Popen(
                self._argv(targets),
                cwd=self.workspace.root,
                env=self.workspace.env,
                stdin=subprocess.DEVNULL,
                stdout=output,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            if generation is not None:
                with self._lock:
                    if generation != self._generation:
                        _kill(process)
                        return None
                    self._process = process
            timed_out = False
            try:
                process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
            finally:
                _kill(process)
            output.seek(0)
            text = output.read().decode("utf-8", errors="replace")
        return {
            "code": process.returncode,
            "failed": sorted({m.group(2) for m in _FAILURE.finditer(text)}),
            "output": text[-4000:],
            "timed_out": timed_out,
            "seconds": time.perf_counter() - started,
        }

    def changed(self):
        """Start (or restart) a background run for the current workspace state."""
        self.files = list(self.select(list(self.workspace.changed)))
        with self._lock:
            self._generation += 1
            generation = self._generation
            if self._process is not None:
                _kill(self._process)
                self._process = None
            self._done.clear()
            self._result = None
            if not self.files:
                self._thread = None
                return

        def work():
            result = self._execute(self.files, self.timeout, generation)
            with self._lock:
                if generation == self._generation and result is not None:
                    self._result = result
                    self._done.set()

        self._thread = threading.Thread(target=work, daemon=True)
        self._thread.start()

    def close(self):
        with self._lock:
            self._generation += 1
            if self._process is not None:
                _kill(self._process)

    def diff(self, limit=1500):
        """Unified diff of the agent's changes so far, for regression feedback."""
        chunks = []
        for name in self.workspace.changed:
            path = self.workspace.root / name
            before = self.originals(name) or b""
            after = path.read_bytes() if path.exists() else b""
            chunks.extend(
                difflib.unified_diff(
                    before.decode("utf-8", errors="replace").splitlines(keepends=True),
                    after.decode("utf-8", errors="replace").splitlines(keepends=True),
                    fromfile="a/" + name,
                    tofile="b/" + name,
                    n=1,
                )
            )
        text = "".join(chunks)
        return text if len(text) <= limit else text[:limit] + "\n[diff truncated]"

    def regressions(self, wait):
        """Return (new_failures, output) for the latest change, or None if unknown."""
        if not self.files or self._thread is None:
            return None
        if not self._done.wait(timeout=max(0.0, wait)):
            return None
        result = self._result
        if result is None or result["timed_out"] or not result["failed"]:
            return None
        if result["code"] not in (0, 1):
            # Collection or usage errors say nothing about this change.
            return None
        failing = result["failed"]
        baseline = self._baseline(failing, wait)
        if baseline is None:
            return None
        new = [name for name in failing if name not in baseline]
        if not new:
            return None
        # Show only the new failures (the full run also lists old ones).
        focused = self._execute(new, max(5.0, min(self.timeout, wait)))
        output = focused["output"] if focused else result["output"]
        return new, output

    def _baseline(self, failing, wait):
        """Failures among `failing` with the original sources restored."""
        current = {}
        root = self.workspace.root
        for name in self.workspace.changed:
            path = root / name
            current[name] = path.read_bytes() if path.exists() else None
        try:
            for name in current:
                original = self.originals(name)
                path = root / name
                if original is None:
                    path.unlink(missing_ok=True)
                else:
                    path.write_bytes(original)
            result = self._execute(failing, max(5.0, min(self.timeout, wait)))
        finally:
            for name, data in current.items():
                path = root / name
                if data is None:
                    path.unlink(missing_ok=True)
                else:
                    path.write_bytes(data)
        if result is None or result["timed_out"] or result["code"] not in (0, 1):
            return None
        return set(result["failed"])


def _kill(process):
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass
