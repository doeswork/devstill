"""Syntax gate for JS/TS edits in JS mode.

One persistent node helper per run parses files with the project's own
TypeScript (createSourceFile parse diagnostics: syntax only, no types), or
with esbuild when TypeScript is not installed. An edit or write that would
leave a file that no longer parses is refused before anything is written,
like the compile gate for Python files; a file that already failed to parse
is never gated. Anything slow or unavailable means no gate, never a refusal.
"""

from __future__ import annotations

import hashlib
import json
import queue
import re
import shutil
import subprocess
import threading
from pathlib import PurePosixPath

from devstill.runtime.tools import ToolError, _kill

SUFFIXES = {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".mts", ".cts"}

HELPER = r"""
const path = require('path');
const readline = require('readline');
const root = process.argv[1];
let ts = null, esbuild = null;
try { ts = require(require.resolve('typescript', { paths: [root] })); } catch (e) {}
if (!ts) {
  try { esbuild = require(require.resolve('esbuild', { paths: [root] })); } catch (e) {}
}
const say = (value) => process.stdout.write(JSON.stringify(value) + '\n');
say({ ready: ts ? 'typescript ' + ts.version : esbuild ? 'esbuild' : null });
function check(name, text) {
  const ext = path.extname(name).toLowerCase();
  if (ts) {
    const K = ts.ScriptKind;
    const kind = { '.ts': K.TS, '.mts': K.TS, '.cts': K.TS, '.tsx': K.TSX,
      '.jsx': K.JSX, '.js': K.JS, '.mjs': K.JS, '.cjs': K.JS }[ext] || K.TS;
    const file = ts.createSourceFile(name, text, ts.ScriptTarget.Latest, false, kind);
    return (file.parseDiagnostics || []).slice(0, 5).map((d) => {
      const at = file.getLineAndCharacterOfPosition(d.start || 0);
      return { line: at.line + 1, col: at.character + 1, code: 'TS' + d.code,
        message: ts.flattenDiagnosticMessageText(d.messageText, ' ') };
    });
  }
  if (!esbuild) return [];
  const loader = { '.ts': 'ts', '.mts': 'ts', '.cts': 'ts', '.tsx': 'tsx',
    '.jsx': 'jsx', '.js': 'js', '.mjs': 'js', '.cjs': 'js' }[ext] || 'ts';
  try { esbuild.transformSync(text, { loader, sourcefile: name }); return []; }
  catch (e) {
    return (e.errors || []).slice(0, 5).map((x) => ({
      line: x.location ? x.location.line : 0,
      col: x.location ? x.location.column + 1 : 0, code: '', message: x.text }));
  }
}
readline.createInterface({ input: process.stdin }).on('line', (line) => {
  let request = {};
  try {
    request = JSON.parse(line);
    say({ id: request.id, errors: check(request.name, request.text) });
  } catch (e) { say({ id: request.id, failed: String(e) }); }
});
"""


def code_frame(lines, line, col=None, before=5, after=1):
    """Lines around `line` (1-based) with a gutter and a caret under `col`."""
    if not lines or line < 1 or line > len(lines) + 1:
        return ""
    first = max(1, line - before)
    last = min(len(lines), line + after)
    width = len(str(last))
    out = []
    for number in range(first, last + 1):
        mark = ">" if number == line else " "
        out.append(f"{mark} {number:>{width}} | {lines[number - 1]}")
        if number == line and col:
            out.append(f"  {' ' * width} | {' ' * max(0, col - 1)}^")
    return "\n".join(out)


class SyntaxGate:
    """content_gate for Workspace in JS mode (see module docstring)."""

    START_SECONDS = 5.0
    CHECK_SECONDS = 3.0

    def __init__(self, root, env):
        self.root = root
        self.env = dict(env)
        self.process = None
        self.engine = None
        self._lines = queue.Queue()
        self._lock = threading.Lock()
        self._started = threading.Event()
        self._closed = False
        self._count = 0
        self._parsed = {}  # sha256 of text -> errors (cached)
        threading.Thread(target=self._start, daemon=True).start()

    def _start(self):
        try:
            node = shutil.which("node", path=self.env.get("PATH"))
            if node is None:
                return
            process = subprocess.Popen(
                [node, "-e", HELPER, str(self.root)],
                cwd=self.root,
                env=self.env,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                bufsize=1,
                start_new_session=True,
            )
            with self._lock:
                if self._closed:
                    _kill(process)
                    return
                self.process = process
            threading.Thread(target=self._read, args=(process,), daemon=True).start()
            try:
                first = self._lines.get(timeout=self.START_SECONDS)
            except queue.Empty:
                return
            self.engine = (first or {}).get("ready")
        finally:
            self._started.set()

    def _read(self, process):
        for line in process.stdout:
            try:
                self._lines.put(json.loads(line))
            except ValueError:
                continue

    def errors(self, name, text):
        """Parse errors of `text` as file `name`; None when unknown."""
        key = hashlib.sha256(f"{PurePosixPath(name).suffix}\0{text}".encode()).digest()
        if key in self._parsed:
            return self._parsed[key]
        if not self._started.wait(timeout=self.START_SECONDS):
            return None
        with self._lock:
            process = self.process
            if process is None or self.engine is None or process.poll() is not None:
                return None
            self._count += 1
            request = self._count
            try:
                process.stdin.write(
                    json.dumps({"id": request, "name": name, "text": text}) + "\n"
                )
                process.stdin.flush()
            except OSError:
                return None
            while True:
                try:
                    answer = self._lines.get(timeout=self.CHECK_SECONDS)
                except queue.Empty:
                    return None
                if answer.get("id") == request:
                    break
        found = answer.get("errors")
        if not isinstance(found, list):
            return None
        self._parsed[key] = found
        return found

    def __call__(self, name, before, after):
        """Refuse `after` when it no longer parses (ToolError); `before` is
        the file's current text (None for a new file)."""
        if PurePosixPath(name).suffix.lower() not in SUFFIXES:
            return
        found = self.errors(name, after)
        if not found:
            return
        if before is not None and self.errors(name, before) != []:
            return  # it did not parse before either (or unknown): not gated
        first = found[0]
        lines = after.split("\n")
        where = f"line {first['line']}" + (
            f", column {first['col']}" if first.get("col") else ""
        )
        code = f"{first['code']}: " if first.get("code") else ""
        more = (
            f" ({len(found) - 1} more syntax error(s) after it)"
            if len(found) > 1
            else ""
        )
        message = str(first["message"]).rstrip(".")
        raise ToolError(
            f"Rejected: {name} would not parse ({code}{message}; {where})"
            f"{more}. No changes were written. The result would read:\n"
            + code_frame(lines, first["line"], first.get("col"), before=4, after=2)
        )

    def close(self):
        with self._lock:
            self._closed = True
            process, self.process = self.process, None
        if process is not None:
            try:
                process.stdin.close()
            except OSError:
                pass
            _kill(process)


# --- edit boundary repairs (JS mode) -------------------------------------------

_OPEN, _CLOSE = "([{", ")]}"


def bracket_balance(text):
    """Opening minus closing brackets outside string literals and comments
    (a rough count: template literals and regexes are not parsed)."""
    balance, quote, index = 0, None, 0
    while index < len(text):
        char = text[index]
        if quote:
            if char == "\\":
                index += 2
                continue
            if char == quote or (char == "\n" and quote != "`"):
                quote = None
        elif text.startswith("//", index):
            end = text.find("\n", index)
            index = len(text) if end < 0 else end
            continue
        elif text.startswith("/*", index):
            end = text.find("*/", index + 2)
            index = len(text) if end < 0 else end + 2
            continue
        elif char in "'\"`":
            quote = char
        elif char in _OPEN:
            balance += 1
        elif char in _CLOSE:
            balance -= 1
        index += 1
    return balance


def _line_bounds(text, start, end):
    """Start of the line holding `start` and end (after the newline) of the
    line holding `end - 1`."""
    first = text.rfind("\n", 0, start) + 1
    last = text.find("\n", max(start, end - 1))
    return first, len(text) if last < 0 else last + 1


def boundary_candidates(text, old, new, most=6):
    """Repairs of one edit block whose boundary is likely off, as (oldText,
    reason) pairs; `old` must occur exactly once in `text`.

    * newText closes more brackets than oldText: oldText is extended forward
      line by line until both balance the same (the closing lines newText
      already contains).
    * newText ends (starts) with lines that already follow (precede)
      oldText: oldText is extended over them.

    An extended oldText keeps whether oldText ended with a newline.
    """
    if not old or text.count(old) != 1:
        return []
    start = text.index(old)
    end = start + len(old)
    newline = old.endswith("\n")
    found = []

    def add(extended, reason):
        if (
            extended != old
            and text.count(extended) == 1
            and all(extended != item for item, _ in found)
        ):
            found.append((extended, reason))

    rest = text[end:]
    lead = 1 if not newline and rest.startswith("\n") else 0
    following = rest[lead:].split("\n")
    if following and following[-1] == "" and rest.endswith("\n"):
        following.pop()

    def forward(count):
        stop = end + lead + len("\n".join(following[:count]))
        if newline:
            stop += 1
        return text[start : min(stop, len(text))]

    if bracket_balance(new) < bracket_balance(old):
        for count in range(1, min(most, len(following)) + 1):
            extended = forward(count)
            if bracket_balance(extended) == bracket_balance(new):
                shown = " ".join(line.strip() for line in following[:count])[:60]
                add(
                    extended,
                    f"its oldText was extended over the next {count} line(s) "
                    f"({shown!r}), which newText already closes",
                )
                break
    new_lines = new.rstrip("\n").split("\n")
    at_line_start = start == 0 or text[start - 1] == "\n"
    # The non-blank lines before oldText, newest last, with their offsets
    # (blank lines between them and oldText are covered by the extension).
    before, offset = [], start
    while at_line_start and offset > 0 and len(before) < 4:
        line_start = text.rfind("\n", 0, offset - 1) + 1
        line = text[line_start : offset - 1]
        if line.strip():
            before.insert(0, (line, line_start))
        offset = line_start
    for count in range(1, min(4, len(new_lines)) + 1):
        tail = [line.strip() for line in new_lines[-count:]]
        if any(tail) and [line.strip() for line in following[:count]] == tail:
            add(
                forward(count),
                f"its oldText was extended over the next {count} line(s), which "
                "newText repeats",
            )
        head = [line.strip() for line in new_lines[:count]]
        if (
            at_line_start
            and any(head)
            and len(before) >= count
            and [line.strip() for line, _ in before[-count:]] == head
        ):
            first = before[-count][1]
            if first >= 0 and text[first:start].endswith("\n"):
                add(
                    text[first:end],
                    f"its oldText was extended over the {count} line(s) before it, "
                    "which newText repeats",
                )
    return found


_EMPTY_BODY = re.compile(
    r"^\s*(?:\}\s*else\s+)?(?:if|for|while)\s*\(.*\)\s*(?:;|\{\s*\})\s*(?://.*)?$"
)


def empty_bodies(new):
    """Lines of newText that are an if/for/while with an empty body."""
    return [line.strip() for line in new.split("\n") if _EMPTY_BODY.match(line)]


def close_brackets(text):
    """`text` plus the closing brackets it lacks at its end (strings and
    escapes respected); None when it ends inside a string or closes a
    bracket it never opened."""
    stack, in_string, escaped = [], False, False
    pairs = {"{": "}", "[": "]"}
    for char in text:
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
        elif char == '"':
            in_string = True
        elif char in pairs:
            stack.append(pairs[char])
        elif char in "}]" and (not stack or stack.pop() != char):
            return None
    if in_string or not stack:
        return None
    return text.rstrip() + "".join(reversed(stack))
