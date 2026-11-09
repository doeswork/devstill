"""Retrieval-primed tool loop for short coding tasks.

The first request already carries the retrieved source, so most tasks finish in
one or two model calls: edit, verify and call ``done`` in a single response.
The model can still read, search and run commands when the excerpts are not
enough. Generated verification commands inform the model; they are not a
separate gate, and their results are recorded for review.
"""

from __future__ import annotations

import ast
import json
import re
import time

from devstill.runtime.artifacts import write
from devstill.runtime.loop_context import is_test_path
from devstill.runtime.tools import (
    _FAILURE,
    ToolError,
    ToolResult,
    Workspace,
    check_failure_line,
)

SYSTEM = """You are Devstill Quick, a fast, careful coding agent working in a copy of the user's repository. Make the smallest correct change that fully satisfies the request.

How to work:
- The request comes with source excerpts retrieved from the repository. They are the current file contents: edit from them directly. Read or search only for something you need that is not shown.
- Change files with edit (or write for a new file): give the path and edits, a list of {oldText, newText} blocks, and put all changes to one file in a single edit call. Each oldText must match the current file exactly and uniquely: copy it from the shown source, keep it short (the lines you change plus enough context to be unique) and never retype unchanged code.
- Check your change with the python tool: a short script (2-6 asserts) that calls the changed function or method directly with minimal setup and asserts the requested behavior plus each behavior the request explicitly says to keep. It runs in the repository root with the project interpreter (src/ is importable); a failing assert prints both sides. Only test what the request mentions.
- Send several tool calls in one response whenever you can; they run in order. The fastest finish is a single response with three calls: edit(...), python(code with asserts), done(summary). If the check fails, done is ignored and you see the failure. Make every change the request needs before that check: once a check passes after your last edit and would have failed before your edits, the task finishes (related tests run first).
- A failing assert about requested or preserved behavior means the code is wrong: fix the code. Never delete or weaken an assertion to make it pass; change one only if it misstates the request.
- Keep behavior the request does not mention unchanged. Do not edit tests or docs unless asked, and do not add changelog or version notes unless asked.
- Finish by calling done with a one-line summary."""

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "read",
            "description": "Read a text file. Returns raw lines (no line numbers); use offset/limit for large files.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Repository-relative path",
                    },
                    "offset": {"type": "integer", "description": "1-based first line"},
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of lines",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "python",
            "description": "Run a Python script in the repository root with the project interpreter (src/ importable). Use for quick behavior checks with asserts; a failing assert shows both sides.",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "Python source to run"},
                    "timeout": {
                        "type": "number",
                        "description": "Seconds (default 30)",
                    },
                },
                "required": ["code"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "bash",
            "description": "Run a shell command in the repository root (e.g. grep -rn, python -m pytest -q path). Returns output and exit code. The workspace is a copy without git history, so git commands do not work. Files created by commands are not part of the change; create files with write.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string"},
                    "timeout": {
                        "type": "number",
                        "description": "Seconds (default 30)",
                    },
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit",
            "description": "Replace exact, unique text in one file. Put all changes to the file in one call: each edits item is {oldText, newText}, matched against the current file; all apply or none.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "edits": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "oldText": {
                                    "type": "string",
                                    "description": "Exact current text to replace: short, unique, copied from the file",
                                },
                                "newText": {
                                    "type": "string",
                                    "description": "Replacement text",
                                },
                            },
                            "required": ["oldText", "newText"],
                        },
                    },
                },
                "required": ["path", "edits"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write",
            "description": "Create a new file, or with append=true add content to the end of an existing file (e.g. a new test). Existing files longer than 40 lines cannot be replaced; use edit.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                    "append": {
                        "type": "boolean",
                        "description": "Add content to the end of the existing file",
                    },
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "done",
            "description": "Finish the task. Call after the change is complete and verified.",
            "parameters": {
                "type": "object",
                "properties": {"summary": {"type": "string"}},
                "required": ["summary"],
            },
        },
    },
]

MUTATING = {"edit", "write"}

# One finished example exchange: shows the single-response finish (edit, check,
# done) in real tool-call form. It is a constant prefix, so servers with prefix
# caching only process it once.
EXAMPLE = [
    {
        "role": "user",
        "content": "Example task (already finished):\n\nRequest:\nMake parse_ratio "
        "return None for a zero denominator instead of raising. Keep normal ratios."
        "\n\nRepository context (current file contents):\n\n### src/calc/ratio.py "
        "(import as calc.ratio) — 3 lines\nwhole file:\n```python\ndef "
        'parse_ratio(text):\n    num, den = text.split("/")\n    return int(num) / '
        "int(den)\n```",
    },
    {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "id": "example_1",
                "type": "function",
                "function": {
                    "name": "edit",
                    "arguments": json.dumps(
                        {
                            "path": "src/calc/ratio.py",
                            "edits": [
                                {
                                    "oldText": "    return int(num) / int(den)",
                                    "newText": "    if int(den) == 0:\n        return None\n"
                                    "    return int(num) / int(den)",
                                }
                            ],
                        }
                    ),
                },
            },
            {
                "id": "example_2",
                "type": "function",
                "function": {
                    "name": "python",
                    "arguments": json.dumps(
                        {
                            "code": "from calc.ratio import parse_ratio\n"
                            "assert parse_ratio('1/0') is None\n"
                            "assert parse_ratio('3/4') == 0.75\n"
                        }
                    ),
                },
            },
            {
                "id": "example_3",
                "type": "function",
                "function": {
                    "name": "done",
                    "arguments": json.dumps(
                        {"summary": "parse_ratio returns None for a zero denominator"}
                    ),
                },
            },
        ],
    },
    {
        "role": "tool",
        "tool_call_id": "example_1",
        "content": "Edited src/calc/ratio.py: replaced 1 block.",
    },
    {"role": "tool", "tool_call_id": "example_2", "content": "[exit code 0]"},
    {"role": "tool", "tool_call_id": "example_3", "content": "ok"},
]


def repair_json(text):
    """Parse tool arguments, tolerating raw control characters inside strings."""
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        pass
    out, in_string, escaped = [], False, False
    for char in text or "":
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            elif char == "\n":
                out.append("\\n")
                continue
            elif char == "\r":
                out.append("\\r")
                continue
            elif char == "\t":
                out.append("\\t")
                continue
        elif char == '"':
            in_string = True
        out.append(char)
    return json.loads("".join(out))


def normalize_edits(arguments):
    """Accept the common shapes models emit for edit arguments."""
    edits = arguments.get("edits")
    if isinstance(edits, str):
        edits = repair_json(edits)
    if isinstance(edits, dict):
        edits = [edits]
    if edits is None and ("oldText" in arguments or "old_text" in arguments):
        edits = [arguments]
    if not isinstance(edits, list) or not edits:
        raise ToolError(
            "edit needs path, oldText and newText (got: "
            + (", ".join(sorted(arguments)) or "nothing")
            + "). Re-issue the call with all three."
        )
    result = []
    for item in edits:
        if not isinstance(item, dict):
            raise ToolError("Each edits[] item must be an object {oldText, newText}")
        old = item.get("oldText", item.get("old_text", item.get("old")))
        new = item.get("newText", item.get("new_text", item.get("new")))
        if not isinstance(old, str) or not isinstance(new, str):
            raise ToolError("Each edits[] item needs string oldText and newText")
        result.append({"oldText": old, "newText": new})
    return result


def render_context(query, sections, requirements=None):
    """User message: the request followed by compact, faithful repository context."""
    parts = [f"New task:\n\nRequest:\n{query.strip()}"]
    if requirements and any(requirements.values()):
        parts.append(
            "Required deliveries (checked by the runtime): " + json.dumps(requirements)
        )
    if sections:
        parts.append("Repository context (current file contents):")
        parts.extend(sections)
    else:
        parts.append(
            "Repository context: none was found automatically. Locate the relevant "
            "code first (e.g. bash: grep -rn 'keyword' src/) and read it before editing."
        )
    return "\n\n".join(parts)


_OPERATORS = {
    "Eq": "==",
    "NotEq": "!=",
    "Is": "is",
    "IsNot": "is not",
    "In": "in",
    "NotIn": "not in",
    "Lt": "<",
    "LtE": "<=",
    "Gt": ">",
    "GtE": ">=",
}


def _comparisons(code):
    """Map each asserted left-hand expression to its right-hand sources."""
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return None
    found = {}
    for node in ast.walk(tree):
        test = getattr(node, "test", None)
        if (
            isinstance(node, ast.Assert)
            and isinstance(test, ast.Compare)
            and len(test.ops) == 1
        ):
            found.setdefault(ast.unparse(test.left), []).append(
                ast.unparse(test.comparators[0])
            )
    return found


def _literal_repr(source):
    try:
        return repr(ast.literal_eval(source))
    except (ValueError, SyntaxError, TypeError, MemoryError, RecursionError):
        return None


def weakening_note(previous, code):
    """Warn when a new check drops or flips an assertion that just failed."""
    if not previous:
        return ""
    found = _comparisons(code)
    if found is None:
        return ""
    left, right = previous["left_src"], previous["right_src"]
    shown = f"`assert {left} {_OPERATORS.get(previous.get('op'), '==')} {right}`"
    # For `x in y` the informative value is the container, not the needle.
    membership = previous.get("op") in {"In", "NotIn"}
    actual = (
        f"{right} was {previous.get('right')}"
        if membership
        else f"actual value {previous['left']}"
    )
    advice = (
        " If that assertion states what the request requires or keeps, the code is "
        "wrong: restore the assertion and fix the code."
    )
    rights = found.get(left)
    if rights is None:
        return (
            f"\nWarning: your previous check asserted {shown}, which failed ({actual}); "
            "this check no longer asserts it." + advice
        )
    if previous.get("op") in {"Eq", "Is"} and any(
        source != right and _literal_repr(source) == previous["left"]
        for source in rights
    ):
        return (
            f"\nWarning: your previous check asserted {shown}, which failed ({actual}); "
            "this check now expects that actual value." + advice
        )
    return ""


def _call_name(call):
    return (call.get("function") or {}).get("name", "")


# A python script verifies only if it can fail on a wrong result.
ASSERTING = re.compile(r"\bassert\b|AssertionError|\braises\(")
# A failing command that runs Python or tests is evidence against the change.
CHECK_COMMAND = re.compile(
    r"\b(pytest|python3?|unittest|tox|nox|make\s+test|npm\s+test)\b"
)
# A passing command verifies only if it runs a test runner (at command
# position) or an inline Python script (-c or a heredoc) that asserts.
# Print-only probes explore.
_COMMAND_START = (
    r"(?:^|[;&|(\n]|\bthen\b|\bdo\b)\s*"
    r"(?:(?:time|nice|env|command|exec|stdbuf|xvfb-run)\s+(?:-\S+\s+(?:\d+\s+)?)*"
    r"|\w+=\S*\s+|timeout\s+(?:-\S+\s+)*\S+\s+"
    r"|(?:uv|poetry|pdm|hatch|pipenv|rye|conda)\s+run\s+(?:-\S+\s+(?:\S+\s+)?)*)*"
)
_PYTHON = r"(?:\S*/)?python(?:3(?:\.\d+)?)?(?:\s+-(?:[XW]\s*\S+|[A-Za-z]+))*"
TEST_RUNNER = re.compile(
    _COMMAND_START
    + r"(?:"
    + _PYTHON
    + r"\s+-m\s+(?:pytest|unittest|doctest|tox|nox|coverage\s+run\s+(?:-\S+\s+)*-m\s+(?:pytest|unittest))\b"
    r"|(?:\S*/)?(?:pytest|py\.test|tox|nox)\b"
    r"|coverage\s+run\s+(?:-\S+\s+)*-m\s+(?:pytest|unittest)\b"
    r"|make\s+(?:test|check)\b|npm\s+(?:run\s+)?test\b"
    r"|(?:" + _PYTHON + r"\s+)?\S*(?:runtests\.py|manage\.py\s+test)\b)"
)
PY_SCRIPT = re.compile(_COMMAND_START + _PYTHON + r"\s+(?:-c\b|-\s*<<|<<)")


def bash_check_kind(command):
    """'tests' for a test run, 'script' for an asserting inline script, else None."""
    if TEST_RUNNER.search(command):
        return "tests"
    if PY_SCRIPT.search(command) and ASSERTING.search(command):
        return "script"
    return None


MALFORMED_CALL = re.compile(r"<tool_call>|<function=|\{\s*\"name\"\s*:")


def agent_loop(
    workspace: Workspace,
    query,
    context_sections,
    chat,
    output,
    deadline,
    *,
    checkpoint=lambda: None,
    requirements=None,
    required_checks=(),
    check_runner=None,
    max_turns=20,
    bash_timeout=30.0,
    report=lambda message: None,
    before_tools=lambda: None,
    guard=None,
    originals=None,
):
    """Run the tool loop; return an outcome compatible with the structured editor.

    Cancellation (InterruptedError) propagates; the task deadline (TimeoutError)
    ends the loop as a failed outcome; the background test guard is always shut
    down, whichever way the loop exits.
    """
    state = {
        "messages": [
            {"role": "system", "content": SYSTEM},
            *json.loads(json.dumps(EXAMPLE)),
            {
                "role": "user",
                "content": render_context(query, context_sections, requirements),
            },
        ],
        "commands": [],
        "calls": 0,
        "usage": {"prompt_tokens": 0, "completion_tokens": 0},
        "last_edit_step": -1,
        "regressions": [],
    }

    def outcome(status, **extra):
        commands = state["commands"]
        after = [c for c in commands if c["step"] > state["last_edit_step"]]
        checks = [c for c in after if c.get("check", True)]
        # Failures let through (a check judged wrong, a test run that never
        # finished) never count as verification.
        excused = any(
            not c["ok"] and (c.get("check_problem") or c.get("timed_out"))
            for c in after
        )
        write(
            output / "agent-trace.json",
            {"messages": state["messages"], "commands": commands},
        )
        return {
            "status": status,
            "engine": "loop",
            "model_calls": state["calls"],
            "changed_files": sorted(set(workspace.changed)),
            "commands": [
                {k: v for k, v in c.items() if k not in ("record", "message")}
                for c in commands
            ],
            # The agent's latest check after its last edit passed (and was not
            # refused as passing on the original code too), nothing failing was
            # excused, and the regression guard reported nothing outstanding.
            "verified": bool(checks)
            and checks[-1]["ok"]
            and not checks[-1].get("rejected")
            and not excused
            and not state["regressions"],
            "regressions": state["regressions"],
            "usage": state["usage"],
            **extra,
        }

    try:
        return _loop(
            state,
            outcome,
            workspace,
            chat,
            output,
            deadline,
            checkpoint=checkpoint,
            required_checks=required_checks,
            check_runner=check_runner,
            max_turns=max_turns,
            bash_timeout=bash_timeout,
            report=report,
            before_tools=before_tools,
            guard=guard,
            originals=originals,
        )
    except InterruptedError:
        raise
    except TimeoutError as exc:
        return outcome(
            "failed",
            failure_stage="deadline",
            error=str(exc) or "Editor deadline exhausted",
        )
    finally:
        if guard is not None:
            guard.close()


def _loop(
    state,
    outcome,
    workspace,
    chat,
    output,
    deadline,
    *,
    checkpoint,
    required_checks,
    check_runner,
    max_turns,
    bash_timeout,
    report,
    before_tools,
    guard,
    originals,
):
    messages = state["messages"]
    commands = state["commands"]
    nudged = False
    step = 0
    failed_assert = None  # the latest failing check comparison, if any
    failed_code = None  # the check that produced it
    guard_reports = 0
    failed_check_warned = None  # last_edit_step when we last refused a finish
    discrimination_asked = None  # last_edit_step when a non-testing check was refused

    def trace():
        write(output / "agent-trace.json", {"messages": messages, "commands": commands})

    def latest_check():
        checks = [
            c
            for c in commands
            if c.get("check", True) and c["step"] > state["last_edit_step"]
        ]
        return checks[-1] if checks else None

    def check_failed():
        check = latest_check()
        return check is not None and not check["ok"]

    original_runs = {}  # check source -> its result on the original sources

    def on_original(code, timeout=None):
        """Run a check script against the original sources (no model call).

        The original state never changes, so each script runs there once
        (a run that timed out is not kept: it says nothing).
        """
        if originals is None or not workspace.changed:
            return None
        if code in original_runs:
            return original_runs[code]
        if guard is not None:
            guard.close()  # background test runs must not see swapped files
        limit = min(bash_timeout, max(1.0, deadline - time.perf_counter() - 1))
        if timeout is not None:
            limit = min(limit, timeout)
        try:
            with workspace.original_state(originals):
                result = workspace.python(code, limit, checkpoint)
        finally:
            if guard is not None:
                guard.changed()  # restart runs for the restored current state
        if not result.meta.get("timed_out"):
            original_runs[code] = result
        return result

    def known(result):
        """The run finished (not timed out) and left a record."""
        return (
            result is not None
            and not result.meta.get("timed_out")
            and result.meta.get("record") is not None
        )

    def assert_counts(record):
        return {
            int(line): (ran, bad)
            for line, (ran, bad) in ((record or {}).get("asserts") or {}).items()
        }

    def first_values(record):
        """Line -> the operand values of its first recorded failure."""
        found = {}
        for failure in (record or {}).get("failures") or []:
            found.setdefault(failure["line"], failure.get("values"))
        return found

    def compare_runs(now, before):
        """Per-assert verdict of a failing check against the original code."""
        if not now or not before:
            return None
        counts_now, counts_before = assert_counts(now), assert_counts(before)
        values_now, values_before = first_values(now), first_values(before)
        failing = sorted(line for line, (_, bad) in counts_now.items() if bad)

        def held(line):  # ran on the original and never failed there
            ran, bad = counts_before.get(line, (0, 0))
            return ran > 0 and bad == 0

        also = [line for line in failing if counts_before.get(line, (0, 0))[1]]
        verdict = {
            "broke": [line for line in failing if held(line)],
            "same": [
                line
                for line in also
                if values_now.get(line) is not None
                and values_now.get(line) == values_before.get(line)
            ],
            "changed": [
                line
                for line in also
                if values_now.get(line) is None
                or values_now.get(line) != values_before.get(line)
            ],
            "fixed": sorted(
                line
                for line, (ran, bad) in counts_now.items()
                if ran and not bad and counts_before.get(line, (0, 0))[1]
            ),
            "raised": None,  # the check now stops at a statement the original ran
            "stop_same": False,
        }
        stop, stop_before = now.get("stop"), before.get("stop")
        if stop and stop.get("line") is not None:
            if stop_before is None or (
                stop_before.get("line") is not None
                and stop_before["line"] > stop["line"]
            ):
                verdict["raised"] = stop
            elif (
                stop_before.get("line"),
                stop_before.get("inner_line"),
                stop_before.get("type"),
            ) == (stop["line"], stop.get("inner_line"), stop.get("type")):
                verdict["stop_same"] = True
        return verdict

    def lines_text(lines):
        shown = ", ".join(str(line) for line in lines[:6])
        return ("line " if len(lines) == 1 else "lines ") + shown

    def verdict_text(verdict):
        if not verdict:
            return ""
        notes = []
        if verdict["broke"]:
            notes.append(
                f"- {lines_text(verdict['broke'])} held on the original code and "
                "fail now: if an assertion checks behavior the request keeps, your "
                "change broke it (fix the code); if it checks old behavior the "
                "request changes, fix the assertion."
            )
        if verdict["raised"]:
            stop = verdict["raised"]
            notes.append(
                f"- line {stop['line']} raised {stop['type']} only after your change; "
                "the original code ran it without that error."
            )
        if verdict["changed"]:
            notes.append(
                f"- {lines_text(verdict['changed'])} also failed on the original code, "
                "with other values: your change reached it, but the result is still "
                "not what the assertion expects."
            )
        if verdict["same"]:
            notes.append(
                f"- {lines_text(verdict['same'])} fail the same way on the original "
                "code (same values): either your change does not reach this scenario "
                "yet, or the check does not set it up the way the program really runs."
            )
        if verdict["stop_same"]:
            notes.append(
                "- the check stops with the same error at the same line on the "
                "original code, so that part never reaches your change."
            )
        if verdict["fixed"]:
            notes.append(
                f"- {lines_text(verdict['fixed'])} failed on the original code and "
                "pass now."
            )
        if not notes:
            return ""
        return "\nCompared with the original code (your edits undone):\n" + "\n".join(
            notes
        )

    def vacuous(before):
        """The original failed only because the check calls API the change adds."""
        stop = before.get("stop")
        if not stop or not stop.get("in_check") or before.get("failures"):
            return False
        kind, message = stop.get("type"), stop.get("message") or ""
        return kind in {
            "AttributeError",
            "ImportError",
            "ModuleNotFoundError",
            "NameError",
        } or (
            kind == "TypeError"
            and re.search(r"unexpected keyword|positional argument", message)
            is not None
        )

    def progressed(code, record, output):
        """A failing check gets further than on the original, and breaks nothing.

        Then the remaining failure is most likely in the check itself (the
        requested behavior it asserts first now holds), not in the change.
        Progress does not count when the original only failed on API the change
        adds, or when anything that held on the original fails now.
        """
        base = on_original(code)
        if base is None or base.ok or base.meta.get("timed_out"):
            return False
        before = base.meta.get("record") or {}
        now_line = (record or {}).get("first_line") or check_failure_line(output)
        before_line = before.get("first_line") or check_failure_line(base.text)
        if now_line is None or before_line is None or now_line <= before_line:
            return False
        if vacuous(before):
            return False
        verdict = compare_runs(record, before) if record and before else None
        return not (verdict and (verdict["broke"] or verdict["raised"]))

    def preexisting(check):
        """Failing test ids of a test command that all fail on the original too."""
        if guard is None or originals is None or not workspace.changed:
            return []
        failing = sorted(
            {m.group(2).strip() for m in _FAILURE.finditer(check.get("output") or "")}
        )
        if not failing:
            return []
        guard.close()
        try:
            baseline = guard._baseline(
                failing,
                max(1.0, min(guard.timeout, deadline - time.perf_counter() - 2)),
            )
        finally:
            guard.changed()
        if baseline is None or not all(name in baseline[1] for name in failing):
            return []
        return failing

    def bent(previous, code):
        """Keep a weakening warning unless the change never touched that assertion.

        It is dropped only when the assertion failed on the original code with
        exactly the values it fails with now (a wrong expectation the change
        did not reach).
        """
        if previous is None or code is None or not workspace.changed:
            return True  # nothing edited yet: the current code is the original
        base = on_original(code)
        if not known(base):
            return True
        line = previous.get("line")
        before = base.meta["record"]
        if line is None or not assert_counts(before).get(line, (0, 0))[1]:
            return True  # held on the original (or never ran there): warn
        now = (
            [previous.get("left_src"), previous.get("left")],
            [
                previous.get("right_src"),
                previous.get("right"),
            ],
        )
        return first_values(before).get(line) != list(map(list, now))

    def recheck():
        """Re-run the agent's latest python check if it edited afterwards.

        Costs no model call: a final edit that breaks what the agent already
        checked (e.g. deleting a helper it still uses) is caught here.
        """
        nonlocal step
        python_checks = [c for c in commands if c["command"].startswith("python:\n")]
        if not python_checks or python_checks[-1]["step"] > state["last_edit_step"]:
            return None
        code = python_checks[-1]["command"][len("python:\n") :]
        timeout = min(bash_timeout, max(1.0, deadline - time.perf_counter() - 1))
        report("Re-running the latest check")
        result = workspace.python(code, timeout, checkpoint)
        record = result.meta.get("record")
        step += 1
        commands.append(
            {
                "command": "python:\n" + code,
                "ok": result.ok,
                "check": not result.changed,
                "turn": state["calls"],
                "step": step,
                "output": result.text[-2000:],
                "record": record,
                "automatic": True,
            }
        )
        if result.ok:
            return None
        if progressed(code, record, result.text):
            commands[-1]["check"] = False  # the check, not the change, is wrong
            commands[-1]["check_problem"] = True
            return None
        base = on_original(code)
        compared = (
            verdict_text(compare_runs(record, base.meta.get("record")))
            if known(base)
            else ""
        )
        return (
            "Your latest check fails after your last edit (re-run automatically):\n"
            + result.text[-2000:]
            + compared
        )

    def finish(final=False):
        """None when the change may be accepted; otherwise feedback for the model."""
        nonlocal guard_reports
        before_tools()
        problem = recheck()
        if problem:
            return problem
        failures = []
        for command in required_checks:
            results = (check_runner or (lambda *a: []))(
                workspace.root, [command], deadline
            )
            failures.extend(r for r in results if r.get("returncode") != 0)
        if failures:
            return "Runtime delivery checks failed:\n" + "\n".join(
                f["output"][-1500:] for f in failures
            )
        if guard is not None:
            report("Running related tests")
            # Slow suites must not dominate a quick task: wait briefly at most.
            wait = min(15.0, max(0.0, deadline - time.perf_counter() - 5))
            found = guard.regressions(wait, deadline)
            failing = (found["regressions"] + found["added"]) if found else []
            state["regressions"] = failing
            if found and (guard_reports < 2 and not final):
                guard_reports += 1
                parts = []
                if found["regressions"]:
                    parts.append(
                        "Existing tests that passed before your change now fail:\n"
                        + "\n".join(found["regressions"][:10])
                    )
                if found["added"]:
                    parts.append(
                        "Tests you added fail:\n" + "\n".join(found["added"][:10])
                    )
                diff = guard.diff()
                return (
                    "\n\n".join(parts)
                    + "\n\nOutput (tail):\n"
                    + found["output"][-2500:]
                    + (
                        f"\n\nYour changes so far:\n```diff\n{diff}\n```"
                        if diff
                        else ""
                    )
                    + "\nFix the code (or a test you added, if it is wrong) so they pass, "
                    "unless the request explicitly asks to change that behavior."
                )
            if found:
                return "Tests still fail: " + ", ".join(failing[:10])
        return discriminate()

    def after_last_edit():
        return [c for c in commands if c["step"] > state["last_edit_step"]]

    def latest_passing_python():
        """The newest passing python check after the last edit, if any."""
        for check in reversed(after_last_edit()):
            if (
                check["command"].startswith("python:\n")
                and check.get("check", True)
                and check["ok"]
            ):
                return check
        return None

    def proven_by_added_tests(check):
        """A passing test run after `check` ran test files the agent changed."""
        added = [name for name in workspace.changed if is_test_path(name)]
        return any(
            c["step"] > check["step"]
            and c["ok"]
            and bash_check_kind(c["command"]) == "tests"
            and any(name in c["command"] for name in added)
            for c in after_last_edit()
        )

    def discriminate():
        """Refuse once when the latest passing check also passes without the change.

        Judged on the agent's own python check even when a test run followed it:
        existing tests passing says nothing about the requested change.
        """
        nonlocal discrimination_asked
        check = latest_passing_python()
        if (
            check is None
            or discrimination_asked == state["last_edit_step"]
            or all(is_test_path(name) for name in workspace.changed)
            or proven_by_added_tests(check)
        ):
            return None
        base = on_original(check["command"][len("python:\n") :])
        if base is None or not base.ok or base.meta.get("timed_out"):
            return None
        discrimination_asked = state["last_edit_step"]
        check["rejected"] = True
        return (
            "Not finished: your latest check also passes on the original code, so it "
            "does not test the requested change. Add assertions that fail without your "
            "change and pass with it, run them, then call done."
        )

    def changed():
        if guard is not None:
            guard.changed()

    def excused(check):
        """At the turn limit: a failing check that says nothing against the change.

        A python check that now gets further than on the original code with
        nothing that held there broken, or a test run that never finished.
        """
        if check.get("timed_out"):
            return True
        if check["command"].startswith("python:\n") and progressed(
            check["command"][len("python:\n") :], check.get("record"), check["output"]
        ):
            check["check"] = False  # the check, not the change, is wrong
            check["check_problem"] = True
            return True
        return False

    def refuse_finish():
        """Refuse one finish while the latest check after the last edit failed."""
        nonlocal failed_check_warned
        check = latest_check()
        if (
            check is None
            or check["ok"]
            or failed_check_warned == state["last_edit_step"]
        ):
            return None
        failed_check_warned = state["last_edit_step"]
        if check.get("timed_out"):
            return (
                "Not finished: your latest test command did not finish. Run a "
                "narrower command (one test file, -x or -k), then call done."
            )
        if not check["command"].startswith("python:\n"):
            old = preexisting(check)
            if old:
                return (
                    "Not finished: your latest test run failed, but these tests also "
                    "fail on the original code: " + ", ".join(old[:8]) + ". If they "
                    "are not what the request is about, call done again; otherwise fix "
                    "the code."
                )
        return (
            "Not finished: your latest check failed. Fix the code (or the check, "
            "only if it misstates the request) and run it again, then call done."
        )

    def auto_finish(check):
        """None when `check` proves the change and finish() accepts it.

        `check` is the last call of the current response: an assert-bearing
        python check that passed after the last edit (with every check since
        that edit passing), was not bent from an earlier failing assertion,
        ran at least one assert, and fails on the original code. Returns False
        when it does not apply, or finish()'s feedback.
        """
        if (
            check is None
            or not check["ok"]
            or not check.get("check", True)
            or not check["command"].startswith("python:\n")
            or check.get("weakened")
            or any(not c["ok"] for c in after_last_edit() if c.get("check", True))
        ):
            return False
        record = check.get("record") or {}
        code = check["command"][len("python:\n") :]
        if (
            not ASSERTING.search(code)
            or record.get("stop")
            or not any(ran for ran, _ in assert_counts(record).values())
        ):
            return False
        base = on_original(code)
        if base is None or base.ok or base.meta.get("timed_out"):
            return False
        return finish()

    last_batch_failed_edit = False
    while True:
        checkpoint()
        if state["calls"] >= max_turns:
            failed = check_failed()
            if failed:
                failing = [
                    c for c in after_last_edit() if c.get("check", True) and not c["ok"]
                ]
                failed = not all(excused(c) for c in failing)
            if workspace.changed and not last_batch_failed_edit and not failed:
                problem = finish(final=True)
                if problem is None:
                    return outcome("applied_unvalidated", finished="turn_limit")
                return outcome(
                    "failed", failure_stage="turn_limit", error=problem[:500]
                )
            return outcome(
                "failed",
                failure_stage="turn_limit",
                error=f"No verified change within {max_turns} model calls",
            )
        if time.perf_counter() >= deadline:
            return outcome(
                "failed", failure_stage="deadline", error="Editor deadline exhausted"
            )
        report(f"Model call {state['calls'] + 1}")
        reply = chat(messages, TOOLS, deadline)
        state["calls"] += 1
        calls = state["calls"]
        for key in state["usage"]:
            state["usage"][key] += int((reply.get("usage") or {}).get(key) or 0)
        message = reply.get("message") or {}
        tool_calls = [c for c in (message.get("tool_calls") or []) if c.get("function")]
        assistant = {"role": "assistant", "content": message.get("content") or ""}
        if tool_calls:
            assistant["tool_calls"] = [
                {
                    "id": c.get("id") or f"call_{calls}_{index}",
                    "type": "function",
                    "function": {
                        "name": _call_name(c),
                        "arguments": c["function"].get("arguments") or "{}",
                    },
                }
                for index, c in enumerate(tool_calls)
            ]
        messages.append(assistant)
        checkpoint()

        if reply.get("finish_reason") == "length":
            if tool_calls:
                for call in assistant["tool_calls"]:
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call["id"],
                            "content": "Not executed: the response hit the output token limit. "
                            "Re-issue it with shorter arguments (smaller oldText/newText).",
                        }
                    )
            else:
                messages.append(
                    {
                        "role": "user",
                        "content": "Your reply was cut off at the output limit. Continue with concise tool calls.",
                    }
                )
            continue

        if not tool_calls:
            if MALFORMED_CALL.search(assistant["content"]):
                # The server could not parse a tool call and returned it as text.
                messages.append(
                    {
                        "role": "user",
                        "content": "Your tool call could not be parsed. Re-issue it as a "
                        "proper tool call with valid JSON arguments.",
                    }
                )
                continue
            # A plain reply ends the task, as in other coding agents.
            if not workspace.changed:
                if not nudged:
                    nudged = True
                    messages.append(
                        {
                            "role": "user",
                            "content": "No file has been changed yet. Make the requested change with edit/write, verify it, then call done.",
                        }
                    )
                    continue
                return outcome(
                    "failed",
                    failure_stage="no_changes",
                    error="The model finished without changing any file",
                    summary=assistant["content"][:500],
                )
            problem = refuse_finish() or finish()
            if problem and calls < max_turns:
                messages.append(
                    {"role": "user", "content": problem + "\nFix this, then call done."}
                )
                continue
            if problem:
                return outcome(
                    "failed", failure_stage="verification", error=problem[:500]
                )
            return outcome("applied_unvalidated", summary=assistant["content"][:500])

        before_tools()  # the workspace copy must exist before any tool runs
        blocked = False
        batch_failed = False
        last_batch_failed_edit = False
        done_summary = None
        done_index = None
        for call in assistant["tool_calls"]:
            name = call["function"]["name"]
            step += 1
            result: ToolResult
            if blocked:
                result = ToolResult(
                    "Skipped: an earlier edit in this response failed.", ok=False
                )
            else:
                try:
                    checkpoint()
                    try:
                        arguments = repair_json(call["function"]["arguments"])
                    except ValueError as exc:
                        raise ToolError(
                            f"Could not parse {name} arguments as JSON ({exc}). "
                            "Re-issue the call with valid JSON."
                        ) from None
                    if not isinstance(arguments, dict):
                        raise ToolError("Tool arguments must be a JSON object")
                    if name == "read":
                        result = workspace.read(
                            str(arguments["path"]),
                            arguments.get("offset"),
                            arguments.get("limit"),
                        )
                    elif name == "edit":
                        result = workspace.edit(
                            str(arguments["path"]), normalize_edits(arguments)
                        )
                        state["last_edit_step"] = step
                        changed()
                    elif name == "write":
                        content = arguments.get("content")
                        if not isinstance(content, str):
                            raise ToolError("write needs string content")
                        append = arguments.get("append") in (True, "true", "True", 1)
                        result = workspace.write(
                            str(arguments["path"]), content, append
                        )
                        state["last_edit_step"] = step
                        changed()
                    elif name in ("python", "bash"):
                        text = arguments.get("code" if name == "python" else "command")
                        if not isinstance(text, str):
                            raise ToolError(
                                "python needs string code"
                                if name == "python"
                                else "bash needs a string command"
                            )
                        timeout = min(
                            float(arguments.get("timeout") or bash_timeout),
                            bash_timeout * 2,
                            max(1.0, deadline - time.perf_counter()),
                        )
                        previous = next(
                            (
                                c
                                for c in reversed(commands)
                                if c["command"].startswith("python:\n")
                            ),
                            None,
                        )
                        if (
                            name == "python"
                            and previous is not None
                            and previous["step"] > state["last_edit_step"]
                            and previous["command"] == "python:\n" + text
                        ):
                            raise ToolError(
                                "You already ran this exact check since your last edit "
                                f"(it {'passed' if previous['ok'] else 'failed'}). Change "
                                "the code or the check instead of re-running it."
                            )
                        record = None
                        weakened = timed_out = False
                        if name == "python":
                            report("Running check")
                            note = weakening_note(failed_assert, text)
                            weakened = bool(note)  # never auto-finish on it
                            if note and not bent(failed_assert, failed_code):
                                note = ""  # failed identically on the original
                            started = time.perf_counter()
                            result = workspace.python(text, timeout, checkpoint)
                            elapsed = time.perf_counter() - started
                            record = result.meta.get("record")
                            failed_assert = result.meta.get("failed_assert")
                            failed_code = text if failed_assert else None
                            if note:
                                result.text += note
                            counts = not result.changed and bool(ASSERTING.search(text))
                            if (
                                counts
                                and not result.ok
                                and record is not None
                                and workspace.changed
                                and not result.meta.get("timed_out")
                            ):
                                # Which assertions the change broke, fixed or
                                # never reached: one run on the original code.
                                base = on_original(text, timeout=max(3.0, 3 * elapsed))
                                if base is not None and not base.meta.get("timed_out"):
                                    result.text += verdict_text(
                                        compare_runs(record, base.meta.get("record"))
                                    )
                        else:
                            report("Running command")
                            result = workspace.bash(text, timeout, checkpoint)
                            kind = bash_check_kind(text)
                            if kind == "tests" and result.meta.get("timed_out"):
                                timed_out = True
                                result.text += (
                                    "\n[the test command did not finish, so it says "
                                    "nothing about your change: run a narrower command "
                                    "(one test file, -x or -k)]"
                                )
                            counts = not result.changed and (
                                kind is not None
                                or (not result.ok and bool(CHECK_COMMAND.search(text)))
                            )
                        if result.changed:
                            # The command edited files itself: that is an edit,
                            # not a check of the edited state.
                            state["last_edit_step"] = step
                            changed()
                        commands.append(
                            {
                                "command": ("python:\n" + text)
                                if name == "python"
                                else text,
                                "ok": result.ok,
                                "check": counts,
                                "turn": calls,
                                "step": step,
                                "output": result.text[-2000:],
                                "record": record,
                                "weakened": bool(name == "python" and weakened),
                                "timed_out": timed_out,
                            }
                        )
                    elif name == "done":
                        done_summary = (
                            str(arguments.get("summary", "")).strip() or "done"
                        )
                        result = ToolResult("ok")
                    else:
                        raise ToolError(
                            f"Unknown tool {name!r}. Available: read, python, bash, edit, write, done."
                        )
                except (InterruptedError, TimeoutError):
                    raise  # cancellation and the task deadline are not tool errors
                except ToolError as exc:
                    result = ToolResult(str(exc), ok=False)
                except (KeyError, TypeError) as exc:
                    result = ToolResult(
                        f"Invalid arguments for {name}: {exc}", ok=False
                    )
                except ValueError as exc:
                    # Path policy refusals (outside repo, excluded, unsupported).
                    result = ToolResult(f"{name} refused: {exc}", ok=False)
                except OSError as exc:
                    result = ToolResult(f"{name} failed: {exc}", ok=False)
                if not result.ok:
                    batch_failed = True
                    if name in MUTATING:
                        blocked = True
                        last_batch_failed_edit = True
            messages.append(
                {"role": "tool", "tool_call_id": call["id"], "content": result.text}
            )
            if name in ("python", "bash") and commands and commands[-1]["step"] == step:
                commands[-1]["message"] = len(messages) - 1
            if name == "done":
                done_index = len(messages) - 1
        trace()
        if done_summary is None or done_index is None:
            if batch_failed or not workspace.changed:
                continue
            # A passing check that fails on the original code proves the
            # change: finish now instead of spending a model call on done.
            last = commands[-1] if commands else None
            problem = auto_finish(
                last
                if last is not None and last["turn"] == calls and last["step"] == step
                else None
            )
            if problem is None:
                summary = assistant["content"].strip() or "Changed " + ", ".join(
                    sorted(set(workspace.changed))
                )
                return outcome(
                    "applied_unvalidated",
                    summary=summary[:500],
                    finished="verified_check",
                )
            if problem:
                # The check is this response's last call: its result is the
                # newest message.
                messages[-1]["content"] += "\n\nNot finished: " + problem.removeprefix(
                    "Not finished: "
                )
            continue
        if batch_failed:
            messages[done_index]["content"] = (
                "Not finished: an edit or command in this response failed. "
                "Fix it, or explain why it is expected, then call done."
            )
            continue
        if not workspace.changed:
            if not nudged:
                nudged = True
                messages[done_index]["content"] = (
                    "Not finished: no file has been changed yet. Make the "
                    "requested change with edit/write, then call done."
                )
                continue
            return outcome(
                "failed",
                failure_stage="no_changes",
                error="The model finished without changing any file",
                summary=done_summary[:500],
            )
        problem = refuse_finish() or finish()
        if problem and calls < max_turns:
            messages[done_index]["content"] = (
                problem
                if problem.startswith("Not finished")
                else "Not finished: " + problem
            )
            continue
        if problem:
            return outcome("failed", failure_stage="verification", error=problem[:500])
        return outcome("applied_unvalidated", summary=done_summary[:500])
