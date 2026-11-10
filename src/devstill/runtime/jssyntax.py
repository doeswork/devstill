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
