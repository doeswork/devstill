"""Workspace tools for the Quick agent loop: read, edit, write, python and bash.

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
from contextlib import contextmanager
from dataclasses import dataclass, field
from itertools import pairwise
from pathlib import Path

READ_DEFAULT_LINES = 400
READ_MAX_CHARS = 40_000
OUTPUT_MAX_CHARS = 6_000
# write may replace a whole existing file only when it is this small.
OVERWRITE_MAX_LINES = 40
BOM = b"\xef\xbb\xbf"

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


# Runs a check script. Top-level asserts (also inside top-level if/for/while/
# try blocks) are rewritten to record their outcome with operand values
# captured when the assert ran (pytest-style temporaries: nothing is evaluated
# twice, and names bound by `except ... as e` stay visible). A failing assert
# keeps going only when the next statement is another assert, so a run reports
# a whole block of failing assertions while guards still guard. Everything is
# recorded in argv[2] (JSON) so the loop can compare a failing check with the
# same check on the original code and spot a weakened assertion. The last
# stderr line names the first failing line.
PYTHON_RUNNER = r'''
import ast, inspect, json, os, re, sys, traceback, types

path = sys.argv[1]
record = sys.argv[2] if len(sys.argv) > 2 else None
source = open(path, encoding="utf-8").read()
lines = source.split("\n")
sys.argv = [path]
ERR = sys.stderr  # the check may redirect sys.stderr; runner output must not follow
PID = os.getpid()
SHOWN, KEPT = 8, 50
state = {
    "failures": [],
    "asserts": {},
    "first": None,
    "compare": None,
    "count": 0,
    "pending": False,  # an assert failed earlier in the current run of asserts
}


def say(text):
    try:
        ERR.write(text + "\n")
        ERR.flush()
    except Exception:
        pass


def snippet(line, end=None):
    text = " ".join(part.strip() for part in lines[line - 1 : (end or line)])
    return text if len(text) <= 200 else text[:199] + "..."


def show(value):
    try:
        return repr(value)[:300]
    except Exception as exc:
        return f"<repr failed: {type(exc).__name__}>"


def text_of(value):
    try:
        return str(value)[:300]
    except Exception:
        return show(value)


class Stop(AssertionError):
    """A failed assert that ends the script (the next statement is not an assert)."""


def ran(line):
    state["asserts"].setdefault(line, [0, 0])[0] += 1


def failed(line, end, kind, values, op, message, stop):
    state["asserts"].setdefault(line, [0, 0])[1] += 1
    state["count"] += 1
    shown = [[src, show(value)] for src, value in values]
    if state["first"] is None:
        state["first"] = line
    if kind == "compare" and state["compare"] is None:
        state["compare"] = {
            "left_src": shown[0][0],
            "right_src": shown[1][0],
            "op": op,
            "left": shown[0][1],
            "right": shown[1][1],
            "line": line,
        }
    if len(state["failures"]) < KEPT:
        entry = {"line": line, "kind": kind, "values": shown, "op": op}
        if message is not None:
            entry["message"] = show(message)
        state["failures"].append(entry)
    if state["count"] <= SHOWN:
        out = [f"AssertionError at check line {line}: {snippet(line, end)}"]
        if message is not None:
            out.append(f"  message: {text_of(message)}")
        if kind == "compare":
            out += [f"  left:  {shown[0][1]}", f"  right: {shown[1][1]}"]
        elif kind == "chain":
            out += [f"  {src} = {value}" for src, value in shown]
        else:
            out += [f"  {src} -> {value}" for src, value in shown]
        say("\n".join(out))
    if stop:
        state["pending"] = False
        raise Stop(f"check line {line}")
    state["pending"] = True


def end_run():
    """After the last assert of a run: stop if any assert in the run failed."""
    if state["pending"]:
        state["pending"] = False
        raise Stop("assert run")


def catches_assertions(handler):
    """True when an except clause would catch an AssertionError."""
    if handler.type is None:
        return True
    names = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
    return any(
        isinstance(n, (ast.Name, ast.Attribute))
        and (n.id if isinstance(n, ast.Name) else n.attr)
        in {"AssertionError", "Exception", "BaseException"}
        for n in names
    )


class Rewrite:
    """Rewrite top-level asserts so failures are recorded with their operands.

    A failing assert keeps the script going only when the next statement is
    another assert; otherwise it stops the script like a plain assert (a
    guard before a loop step or a cleanup still guards). Asserts inside
    functions, classes, `with` blocks and `try` blocks that catch
    AssertionError stay plain asserts.
    """

    count = 0

    def temp(self):
        Rewrite.count += 1
        return f"__dv_{Rewrite.count}"

    def block(self, statements):
        out = []
        for index, node in enumerate(statements):
            following = statements[index + 1] if index + 1 < len(statements) else None
            if isinstance(node, ast.Assert):
                out.extend(self.assertion(node, isinstance(following, ast.Assert)))
                continue
            if isinstance(node, (ast.If, ast.For, ast.While)):
                node.body = self.block(node.body)
                node.orelse = self.block(node.orelse)
            elif isinstance(node, ast.Try) or type(node).__name__ == "TryStar":
                if not any(catches_assertions(h) for h in node.handlers):
                    node.body = self.block(node.body)
                for handler in node.handlers:
                    handler.body = self.block(handler.body)
                node.orelse = self.block(node.orelse)
                node.finalbody = self.block(node.finalbody)
            elif type(node).__name__ == "Match":
                for case in node.cases:
                    case.body = self.block(case.body)
            out.append(node)
        return out

    def assertion(self, node, keep_going):
        line, end = node.lineno, node.end_lineno or node.lineno
        test = node.test
        load = lambda name: ast.Name(id=name, ctx=ast.Load())
        store = lambda name, value: ast.Assign(
            targets=[ast.Name(id=name, ctx=ast.Store())], value=value
        )
        call = lambda func, *args: ast.Expr(
            ast.Call(func=load(func), args=list(args), keywords=[])
        )
        values, ok = self.temp(), self.temp()
        temps = [values, ok]
        body = [store(values, ast.List(elts=[], ctx=ast.Load()))]
        append = lambda src, name: ast.Expr(
            ast.Call(
                func=ast.Attribute(value=load(values), attr="append", ctx=ast.Load()),
                args=[ast.Tuple(elts=[ast.Constant(src), load(name)], ctx=ast.Load())],
                keywords=[],
            )
        )
        op = None
        message = node.msg if node.msg is not None else ast.Constant(None)
        if isinstance(test, ast.Tuple) and test.elts:
            # `assert (x, "msg")` is always true: Python only warns about it.
            kind = "tuple"
            message = ast.Constant(
                "always true: the parentheses make the assertion a non-empty tuple"
            )
            body.append(store(ok, ast.Constant(False)))
        elif isinstance(test, ast.Compare):
            kind = "compare" if len(test.ops) == 1 else "chain"
            op = type(test.ops[0]).__name__ if kind == "compare" else None
            previous = self.temp()
            temps.append(previous)
            body += [store(previous, test.left), append(ast.unparse(test.left), previous)]
            target = body
            for index, (operator, operand) in enumerate(zip(test.ops, test.comparators)):
                value = self.temp()
                temps.append(value)
                target += [
                    store(value, operand),
                    append(ast.unparse(operand), value),
                    store(
                        ok,
                        ast.Compare(
                            left=load(previous), ops=[operator], comparators=[load(value)]
                        ),
                    ),
                ]
                if index < len(test.ops) - 1:
                    nested = []
                    target.append(ast.If(test=load(ok), body=nested, orelse=[]))
                    target = nested
                previous = value
        elif isinstance(test, ast.BoolOp):
            kind = "bool"
            target = body
            for index, operand in enumerate(test.values):
                target += [store(ok, operand), append(ast.unparse(operand), ok)]
                if index < len(test.values) - 1:
                    nested = []
                    guard = load(ok)
                    if isinstance(test.op, ast.Or):
                        guard = ast.UnaryOp(op=ast.Not(), operand=guard)
                    target.append(ast.If(test=guard, body=nested, orelse=[]))
                    target = nested
        else:
            kind = "value"
            body += [store(ok, test), append(ast.unparse(test), ok)]
        # Counted only once the operands evaluated: an assert whose operands
        # raised never ran.
        body.append(call("__dv_ran__", ast.Constant(line)))
        body.append(
            ast.If(
                test=ast.UnaryOp(op=ast.Not(), operand=load(ok)),
                body=[
                    call(
                        "__dv_failed__",
                        ast.Constant(line),
                        ast.Constant(end),
                        ast.Constant(kind),
                        load(values),
                        ast.Constant(op),
                        message,
                        ast.Constant(not keep_going),
                    )
                ],
                orelse=[],
            )
        )
        if not keep_going:
            # The run of consecutive asserts ends here: what follows may rely
            # on all of them, so any earlier failure in the run stops too.
            body.append(call("__dv_end_run__"))
        # Drop the operand temporaries so asserted objects can be collected.
        clear = ast.Assign(
            targets=[ast.Name(id=t, ctx=ast.Store()) for t in temps],
            value=ast.Constant(None),
        )
        wrapped = ast.Try(body=body, handlers=[], orelse=[], finalbody=[clear])
        for child in ast.walk(wrapped):
            if "lineno" in child._attributes and not hasattr(child, "lineno"):
                child.lineno, child.end_lineno = line, line
                child.col_offset, child.end_col_offset = 0, 0
        return [wrapped]


def frames(tb):
    found = []
    while tb is not None:
        found.append((tb.tb_frame.f_code.co_filename, tb.tb_lineno, tb.tb_frame))
        tb = tb.tb_next
    return found


def statement_at(line):
    best = None
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.stmt) and node.lineno <= line <= (node.end_lineno or node.lineno):
            if best is None or node.lineno >= best.lineno:
                best = node
    return best


def resolve(chain, scope):
    """The object a dotted call target names, without running property getters."""
    parts = chain.split(".")
    if parts[0] not in scope:
        return None
    value = scope[parts[0]]
    for part in parts[1:]:
        if not (inspect.ismodule(value) or inspect.isclass(value)):
            return None
        try:
            static = inspect.getattr_static(value, part)
        except AttributeError:
            return None
        if isinstance(static, property):
            return None
        value = getattr(value, part)
    return value


def dotted(node):
    names = []
    while isinstance(node, ast.Attribute):
        names.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        return ".".join([node.id, *reversed(names)])
    return None


def api_hints(line, frame):
    """Signatures of the library calls on the failing check line."""
    statement = statement_at(line)
    if statement is None:
        return []
    scope = dict(frame.f_globals)
    scope.update(frame.f_locals)
    hints = []
    for node in ast.walk(statement):
        if not isinstance(node, ast.Call):
            continue
        chain = dotted(node.func)
        if chain is None or any(chain == h[0] for h in hints):
            continue
        try:
            target = resolve(chain, scope)
            if target is None or not callable(target):
                continue
            owner = target.__init__ if inspect.isclass(target) else target
            if (inspect.getsourcefile(owner) or "") == path:
                continue
            signature = str(inspect.signature(target))
        except (TypeError, ValueError, OSError):
            continue
        text = f"[called on check line {line}: {chain}{signature}"
        text = (text if len(text) <= 240 else text[:236] + "...") + "]"
        parameters = [
            p for p in re.findall(r"\(([^,)=:*\s]+)", signature) if p not in ("self", "cls")
        ]
        if parameters:
            owners = inspect.getmro(target) if inspect.isclass(target) else (target,)
            for each in owners:
                found = re.search(
                    rf":param {re.escape(parameters[0])}:([^\n]*(?:\n[ \t]+[^:\s][^\n]*)*)",
                    inspect.getdoc(each) or "",
                )
                if found:
                    text += f"\n  {parameters[0]}: " + " ".join(found.group(1).split())[:200]
                    break
        hints.append((chain, text))
        if len(hints) == 2:
            break
    return [text for _, text in hints]


def stopped(exc):
    """Report the exception that ended the script; returns its record."""
    stack = frames(exc.__traceback__)
    try:
        traceback.print_exception(type(exc), exc, exc.__traceback__, file=ERR)
    except Exception:
        say(f"{type(exc).__name__}: {text_of(exc)}")
    own = [f for f in stack if f[0] == path]
    line = own[0][1] if own else None  # the top-level statement that raised
    inner = own[-1][1] if own else None  # the innermost check line
    in_check = bool(stack) and stack[-1][0] == path
    if isinstance(exc, AssertionError) and own:
        # A plain assert (inside a function, with or try block): show operands.
        frame = own[-1][2]
        try:
            scope = dict(frame.f_globals)
            scope.update(frame.f_locals)
            for node in ast.walk(ast.parse(source)):
                if isinstance(node, ast.Assert) and node.lineno <= inner <= (node.end_lineno or node.lineno):
                    if isinstance(node.test, ast.Compare):
                        for item in [node.test.left, *node.test.comparators]:
                            value = eval(compile(ast.Expression(item), path, "eval"), scope)
                            say(f"  {ast.unparse(item)} = {show(value)}")
                    break
        except Exception:
            pass
    elif in_check and line is not None:
        say(
            f"[raised by the check script itself (line {stack[-1][1]}), "
            "not inside the code under test]"
        )
    elif own:
        try:
            for hint in api_hints(line, own[0][2]):
                say(hint)
        except Exception:
            pass
    return {
        "line": line,
        "inner_line": inner,
        "type": type(exc).__name__,
        "message": text_of(exc),
        "in_check": in_check,
    }


def finish(stop):
    """Write the record and summary; returns the process exit status."""
    first = state["first"] if state["first"] is not None else (stop or {}).get("line")
    if record:
        try:
            with open(record, "w", encoding="utf-8") as out:
                json.dump(
                    {
                        "failed_assert": state["compare"],
                        "failures": state["failures"],
                        "asserts": {str(k): v for k, v in state["asserts"].items()},
                        "stop": stop,
                        "first_line": first,
                    },
                    out,
                )
        except Exception:
            pass
    if not state["count"] and stop is None:
        return 0
    hidden = state["count"] - SHOWN
    if hidden > 0:
        say(f"[{hidden} more failed assert(s) not shown]")
    parts = []
    if state["count"]:
        parts.append(f"{state['count']} failed assert(s)")
    if stop is not None:
        parts.append(
            f"stopped by {stop['type']}"
            + (f" at line {stop['line']}" if stop["line"] is not None else "")
        )
    where = f" at line {first}" if first is not None else ""
    say(f"[check failed{where}: " + "; ".join(parts) + "]")
    return 1


real_exit = os._exit


def guarded_exit(status=0):
    # os._exit skips everything after it: settle the record first.
    if os.getpid() == PID:
        failure = finish(None)
        real_exit(1 if failure else status)
    real_exit(status)


os._exit = guarded_exit
module = types.ModuleType("__main__")
module.__file__ = path
module.__dict__.update(
    {"__dv_ran__": ran, "__dv_failed__": failed, "__dv_end_run__": end_run}
)
sys.modules["__main__"] = module
stop = None
code = None
try:
    tree = ast.parse(source, path)
    try:
        tree.body = Rewrite().block(tree.body)
        code = compile(ast.fix_missing_locations(tree), path, "exec")
    except Exception:
        code = compile(source, path, "exec")  # fall back to plain asserts
except SyntaxError as exc:
    say("".join(traceback.format_exception_only(type(exc), exc)).rstrip())
    stop = {
        "line": exc.lineno,
        "inner_line": exc.lineno,
        "type": "SyntaxError",
        "message": text_of(exc.msg),
        "in_check": True,
    }
if code is not None:
    try:
        exec(code, module.__dict__)
    except Stop:
        pass  # a failed assert already recorded and reported
    except SystemExit as exc:
        stack = frames(exc.__traceback__)
        own = [f for f in stack if f[0] == path]
        last = max((n.lineno for n in ast.parse(source).body), default=0)
        if exc.code not in (0, None):
            stop = stopped(exc)
        elif stack and stack[-1][0] != path and own and own[0][1] < last:
            # The code under test exited (e.g. a CLI's main()) before the
            # rest of the check ran; exiting at the last statement is fine.
            stop = {
                "line": own[0][1] if own else None,
                "inner_line": own[-1][1] if own else None,
                "type": "SystemExit",
                "message": f"the code under test exited with status {exc.code!r}",
                "in_check": False,
            }
            say(f"[the code under test called exit ({exc.code!r}); the rest of the check never ran]")
    except BaseException as exc:
        stop = stopped(exc)
raise SystemExit(finish(stop))
'''


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


def _occurrences(content: str, old: str, limit: int = 50) -> list[int]:
    """Start offsets of old in content, overlapping matches included."""
    found = []
    position = content.find(old)
    while position != -1 and len(found) < limit:
        found.append(position)
        position = content.find(old, position + 1)
    return found


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
    old_lines = old.strip("\n").split("\n") or [old]
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
        found = _occurrences(content, old)
        if len(found) == 1:
            begin = found[0]
            spans.append((begin, begin + len(old), new, index))
            continue
        if len(found) > 1:
            lines = [str(_line_number(content, position)) for position in found[:6]]
            raise ToolError(
                f"{label} matches {len(found)} places in {name} (lines "
                f"{', '.join(lines)}). Include more surrounding lines so it is unique."
            )
        fuzzy = _fuzzy_spans(content, old)
        if len(fuzzy) == 1:
            begin, end = fuzzy[0]
            if old.endswith("\n") and content[end : end + 1] == "\n":
                # The whole lines matched: replace their final newline too, so
                # deleting lines behaves exactly like the exact-match path.
                end += 1
                replacement = new
            else:
                replacement = (
                    new[:-1] if old.endswith("\n") and new.endswith("\n") else new
                )
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
    """Show the edited region of the new file for confirmation."""
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


def _compiles(name: str, content: str) -> bool:
    if not name.endswith(".py"):
        return True
    try:
        compile(content, name, "exec")
    except (SyntaxError, ValueError):
        return False
    return True


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


def _tail(stream, limit: int) -> str:
    """Decode only the last `limit` characters' worth of a captured stream."""
    stream.seek(0, os.SEEK_END)
    size = stream.tell()
    stream.seek(max(0, size - limit * 4))
    text = stream.read().decode("utf-8", errors="replace")
    if size > limit * 4 or len(text) > limit:
        text = f"[output truncated; last {limit} chars]\n" + text[-limit:]
    return text


# The runner's own summary line; tracebacks are a fallback for older output.
_CHECK_SUMMARY = re.compile(r"\[check failed at line (\d+)")
_CHECK_FRAME = re.compile(r'File "[^"]*devstill-check-[^/"]*/check\.py", line (\d+)')


def check_failure_line(output: str):
    """Line of the check script where it first failed (None if unknown)."""
    match = _CHECK_SUMMARY.search(output or "") or _CHECK_FRAME.search(output or "")
    return int(match.group(1)) if match else None


class Workspace:
    """File and shell operations confined to one prepared workspace copy."""

    def __init__(
        self,
        root: Path,
        *,
        guard=lambda name: None,
        resolve=None,
        env=None,
        tracked=(),
        python="python",
    ):
        self.root = root
        self.guard = guard
        self.resolve = resolve or (lambda name: root / name)
        self.env = env or dict(os.environ)
        self.python_executable = python
        self.changed: list[str] = []
        # Editable files whose modification by a command (e.g. sed -i) counts
        # as a proposed change, like an edit made with the edit tool.
        self.tracked = list(tracked)

    def name(self, path: str) -> str:
        """Normalized repository-relative name ('./a//b.py' -> 'a/b.py')."""
        target = self.resolve(str(path).replace("\\", "/"))
        try:
            return target.relative_to(self.root).as_posix()
        except ValueError:
            return Path(os.path.relpath(target, self.root)).as_posix()

    def _stat_tracked(self) -> dict[str, tuple[int, int] | None]:
        states = {}
        for name in self.tracked:
            try:
                info = (self.root / name).stat()
                states[name] = (info.st_mtime_ns, info.st_size)
            except OSError:
                states[name] = None
        return states

    def _files(self) -> set[str]:
        """Relative paths of regular files (caches and VCS data excluded)."""
        found = set()
        for current, directories, names in os.walk(self.root):
            directories[:] = [
                d
                for d in directories
                if d not in {"__pycache__", ".pytest_cache", ".git", ".devstill"}
            ]
            base = Path(current).relative_to(self.root)
            for name in names:
                found.add((base / name).as_posix())
        return found

    @contextmanager
    def original_state(self, originals):
        """Temporarily restore the original bytes of every changed file."""
        current = {}
        for name in self.changed:
            path = self.root / name
            current[name] = path.read_bytes() if path.exists() else None
        try:
            for name in current:
                original = originals(name)
                path = self.root / name
                if original is None:
                    path.unlink(missing_ok=True)
                else:
                    path.write_bytes(original)
            yield
        finally:
            for name, data in current.items():
                path = self.root / name
                if data is None:
                    path.unlink(missing_ok=True)
                else:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(data)

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
        shown, size = [], 0
        for number in range(start, end + 1):
            line = lines[number - 1]
            if shown and size + len(line) + 1 > READ_MAX_CHARS:
                end = number - 1  # stop at a whole line
                break
            shown.append(line)
            size += len(line) + 1
        body = "\n".join(shown)
        header = f"[{path} lines {start}-{end} of {total}]"
        footer = (
            f"\n[{total - end} more lines; use offset={end + 1} to continue]"
            if end < total
            else ""
        )
        return ToolResult(f"{header}\n{body}{footer}")

    def edit(self, path: str, edits: list[dict]) -> ToolResult:
        name = self.name(path)
        self.guard(name)
        target = self.resolve(name)
        if not target.is_file():
            raise ToolError(f"File not found: {name}. Use write to create a new file.")
        raw = target.read_bytes()
        bom = raw.startswith(BOM)
        text, crlf = _decode(raw, name)
        updated = apply_text_edits(text, edits, name)
        if _compiles(name, text):
            # Files that already fail to compile (new syntax, fixtures) are not gated.
            _check_python(name, updated)
        output = updated.replace("\n", "\r\n") if crlf else updated
        target.write_bytes((BOM if bom else b"") + output.encode("utf-8"))
        self._note(name)
        shown = _render_changed(text, updated)
        noun = "block" if len(edits) == 1 else "blocks"
        return ToolResult(
            f"Edited {name}: replaced {len(edits)} {noun}."
            + (f"\n{shown}" if shown else ""),
            changed=[name],
        )

    def write(self, path: str, content: str, append: bool = False) -> ToolResult:
        name = self.name(path)
        self.guard(name)
        target = self.resolve(name)
        existed = target.exists()
        raw = target.read_bytes() if existed else b""
        original, crlf = _decode(raw, name) if existed else ("", False)
        content = content.replace("\r\n", "\n")
        if append and existed:
            combined = original
            if combined and not combined.endswith("\n"):
                combined += "\n"
            combined += content
            if _compiles(name, original):
                _check_python(name, combined)
            # Keep the existing bytes (BOM, line endings) untouched; only add.
            separator = b"" if not raw or raw.endswith(b"\n") else b"\n"
            addition = content.replace("\n", "\r\n") if crlf else content
            if separator and crlf:
                separator = b"\r\n"
            target.write_bytes(raw + separator + addition.encode("utf-8"))
            self._note(name)
            lines = combined.count("\n") + (0 if combined.endswith("\n") else 1)
            return ToolResult(
                f"Appended to {name} (now {lines} lines).", changed=[name]
            )
        if existed and original.count("\n") > OVERWRITE_MAX_LINES:
            raise ToolError(
                f"{name} already exists ({original.count(chr(10))} lines); write would "
                "replace all of it. Use edit to change part of it, or write with "
                "append=true to add to its end."
            )
        if not existed or _compiles(name, original):
            _check_python(name, content)
        target.parent.mkdir(parents=True, exist_ok=True)
        output = content.replace("\n", "\r\n") if crlf else content
        bom = BOM if raw.startswith(BOM) else b""
        target.write_bytes(bom + output.encode("utf-8"))
        self._note(name)
        lines = content.count("\n") + (
            0 if content.endswith("\n") or not content else 1
        )
        verb = "Overwrote" if existed else "Created"
        return ToolResult(f"{verb} {name} (now {lines} lines).", changed=[name])

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
                [self.python_executable, str(runner), str(script), str(record)],
                timeout,
                checkpoint,
            )
            if record.is_file():
                try:
                    data = json.loads(record.read_text())
                except ValueError:
                    data = {}
                if data.get("failed_assert"):
                    result.meta["failed_assert"] = data["failed_assert"]
                # JSON-native (string line keys): commands end up in saved
                # proposals, whose digests must survive a JSON round trip.
                result.meta["record"] = {
                    "failures": data.get("failures") or [],
                    "asserts": data.get("asserts") or {},
                    "stop": data.get("stop"),
                    "first_line": data.get("first_line"),
                }
                failing = any(
                    bad for _, bad in result.meta["record"]["asserts"].values()
                )
                if result.ok and (failing or data.get("stop")):
                    # The script hid its failure (e.g. a patched sys.exit).
                    result.ok = False
                    result.text += (
                        "\n[the check recorded failures but exited with status 0]"
                    )
            return result

    def bash(self, command: str, timeout: float, checkpoint=lambda: None) -> ToolResult:
        if not command.strip():
            raise ToolError("command must not be empty")
        # pipefail: `pytest ... | tail` must not report the pipe's last status.
        return self._run(["bash", "-o", "pipefail", "-c", command], timeout, checkpoint)

    def _run(self, argv, timeout: float, checkpoint=lambda: None) -> ToolResult:
        before = self._stat_tracked()
        existing = self._files()
        started = time.perf_counter()
        with tempfile.TemporaryFile() as output:
            try:
                process = subprocess.Popen(
                    argv,
                    cwd=self.root,
                    env=self.env,
                    stdin=subprocess.DEVNULL,
                    stdout=output,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
            except OSError as exc:
                raise ToolError(f"Could not start {argv[0]}: {exc}") from None
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
                _kill(process)
            text = _tail(output, OUTPUT_MAX_CHARS)
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
        created = []
        for name in sorted(self._files() - existing):
            try:
                self.guard(name)
            except ValueError:
                continue  # scratch output the agent could not propose anyway
            created.append(name)
        if created:
            note += (
                "\n[new files created by this command are NOT part of the change: "
                + ", ".join(created[:8])
                + "; create them with write if they are needed]"
            )
        if timed_out:
            return ToolResult(
                (text + "\n" if text else "")
                + f"[command timed out after {timeout:.0f}s]"
                + note,
                ok=False,
                changed=modified,
                meta={"timed_out": True},
            )
        code = process.returncode
        status = f"[exit code {code}]"
        return ToolResult(
            (text.rstrip("\n") + "\n" if text.strip() else "") + status + note,
            ok=code == 0,
            changed=modified,
        )


# Short test summary lines: "FAILED tests/x.py::t[a b] - msg", "ERROR tests/x.py".
_FAILURE = re.compile(r"^(FAILED|ERROR) (.+?)(?: - .*)?$", re.MULTILINE)
_PASSED = re.compile(r"^PASSED (.+?)$", re.MULTILINE)


class TestGuard:
    """Run existing tests in the background after each change.

    Two runs start after every edit: the test files related to the changed
    functions (awaited briefly when the agent finishes) and, when Python code
    changed, the project's whole test directory (used only if it finishes in
    time; a suite that times out or fails to run is not started again).
    A failing test counts as a regression only if it passed with the original
    sources (whole test files re-run with them restored) and fails again in a
    focused re-run of the current state.
    """

    SUITE_EXTRA_WAIT = 3.0
    MAX_SUITE_FAILURES = 20

    def __init__(self, workspace: Workspace, files, originals, timeout=30.0, suite=()):
        """files: test paths, or a callable mapping changed paths to test paths."""
        self.workspace = workspace
        self.select = files if callable(files) else (lambda changed: list(files))
        self.suite = list(suite)
        self.originals = originals
        self.timeout = timeout
        self.files = []
        self._suite_ok = True
        self._lock = threading.Lock()
        self._generation = 0
        self._runs = {}

    def _argv(self, targets, extra=()):
        return [
            self.workspace.python_executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            *extra,
            *targets,
        ]

    def _execute(self, targets, timeout, generation=None, run=None, extra=()):
        started = time.perf_counter()
        with tempfile.TemporaryFile() as output:
            process = subprocess.Popen(
                self._argv(targets, extra),
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
                    run["process"] = process
            timed_out = False
            try:
                process.wait(timeout=max(0.1, timeout))
            except subprocess.TimeoutExpired:
                timed_out = True
            finally:
                _kill(process)
            output.seek(0)
            # Test ids are parsed from the full summary; pytest output for the
            # selected files is bounded, unlike arbitrary agent commands.
            text = output.read(4_000_000).decode("utf-8", errors="replace")
        return {
            "code": process.returncode,
            "failed": sorted({m.group(2).strip() for m in _FAILURE.finditer(text)}),
            "passed": sorted({m.group(1).strip() for m in _PASSED.finditer(text)}),
            "output": text[-4000:],
            "timed_out": timed_out,
            "seconds": time.perf_counter() - started,
        }

    def _start(self, name, targets, generation):
        run = {"done": threading.Event(), "result": None, "process": None}
        self._runs[name] = run

        def work():
            try:
                result = self._execute(targets, self.timeout, generation, run)
            except Exception as exc:  # noqa: BLE001 - report "unknown", never hang
                result = {
                    "code": None,
                    "failed": [],
                    "passed": [],
                    "output": f"could not run tests: {exc}",
                    "timed_out": False,
                    "seconds": 0.0,
                }
            with self._lock:
                if generation == self._generation and result is not None:
                    run["result"] = result
                    run["done"].set()

        threading.Thread(target=work, daemon=True).start()

    def changed(self):
        """Start (or restart) background runs for the current workspace state."""
        changed = list(self.workspace.changed)
        self.files = list(self.select(changed))
        python = any(name.endswith(".py") for name in changed)
        with self._lock:
            self._generation += 1
            generation = self._generation
            for run in self._runs.values():
                if run["process"] is not None:
                    _kill(run["process"])
            self._runs = {}
            if self.files:
                self._start("related", self.files, generation)
            if self.suite and self._suite_ok and python:
                self._start("suite", self.suite, generation)

    def close(self):
        with self._lock:
            self._generation += 1
            for run in self._runs.values():
                if run["process"] is not None:
                    _kill(run["process"])
            self._runs = {}

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

    @staticmethod
    def _usable(result):
        # 0 passed, 1 failures, 2 interrupted by collection errors (still a result).
        return (
            result is not None
            and not result["timed_out"]
            and result["code"] in (0, 1, 2)
        )

    def regressions(self, wait, deadline=None):
        """Failures the change introduced, or None when there are none (or unknown).

        Returns {"regressions": tests that passed with the original sources and
        fail now, "added": failing tests the agent added in files it edited,
        "output": focused pytest output}. Never runs past `deadline`
        (a time.perf_counter() value).
        """
        runs = dict(self._runs)
        if not runs:
            return None
        started = time.perf_counter()
        if deadline is not None:
            wait = min(wait, max(0.0, deadline - started - 2.0))
        failing = set()
        related = runs.get("related")
        if (
            related is not None
            and related["done"].wait(timeout=max(0.0, wait))
            and self._usable(related["result"])
        ):
            failing.update(related["result"]["failed"])
        suite = runs.get("suite")
        if suite is not None:
            remaining = max(0.0, wait - (time.perf_counter() - started))
            suite["done"].wait(timeout=min(self.SUITE_EXTRA_WAIT, remaining))
            result = suite["result"] if suite["done"].is_set() else None
            if result is not None and not self._usable(result):
                self._suite_ok = False  # slow or broken suite: stop starting it
            elif (
                self._usable(result)
                and len(result["failed"]) <= self.MAX_SUITE_FAILURES
            ):
                failing.update(result["failed"])
        if not failing:
            return None

        def budget():
            limit = self.timeout
            if deadline is not None:
                limit = min(limit, deadline - time.perf_counter() - 1.0)
            return limit

        if budget() < 3.0:
            return None
        baseline = self._baseline(sorted(failing), budget())
        if baseline is None:
            return None
        passed, failed_before = baseline
        new = sorted(
            name
            for name in failing
            if name in passed
            or ("::" not in name and any(p.startswith(name + "::") for p in passed))
        )
        edited = set(self.workspace.changed)
        added = sorted(
            name
            for name in failing
            if name not in passed
            and name not in failed_before
            and "::" in name
            and name.split("::", 1)[0] in edited
        )
        if not (new or added) or budget() < 3.0:
            return None
        # Confirm on the current state (filters flakes from concurrent runs)
        # and show only these failures.
        focused = self._execute(new + added, budget())
        if not self._usable(focused):
            return None
        still = set(focused["failed"])
        result = {
            "regressions": [name for name in new if name in still],
            "added": [name for name in added if name in still],
            "output": focused["output"],
        }
        return result if result["regressions"] or result["added"] else None

    def _baseline(self, failing, timeout):
        """(passed, failed) test ids with the original sources restored.

        Whole test files are re-run (not node ids), so tests the agent added
        and ids pytest cannot re-select do not hide real regressions.
        """
        files = sorted({name.split("::", 1)[0] for name in failing})
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
            present = [name for name in files if (root / name).exists()]
            if not present:
                return set(), set()
            result = self._execute(present, max(1.0, timeout), extra=("-rA",))
        finally:
            for name, data in current.items():
                path = root / name
                if data is None:
                    path.unlink(missing_ok=True)
                else:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(data)
        if not self._usable(result):
            return None
        return set(result["passed"]), set(result["failed"])


def _kill(process):
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass
