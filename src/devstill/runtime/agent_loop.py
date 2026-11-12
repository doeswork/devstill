"""Retrieval-primed tool loop for short coding tasks.

The first request already carries the retrieved source, so most tasks finish in
one or two model calls: edit, verify and call ``done`` in a single response.
The model can still read, search and run commands when the excerpts are not
enough. Generated verification commands inform the model; they are not a
separate gate, and their results are recorded for review.
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
import shlex
import threading
import time
from pathlib import Path, PurePosixPath

from devstill.runtime.artifacts import write
from devstill.runtime.jsdeps import (
    GLOB,
    DependencyPaths,
    changes_text,
    dependency_manifest,
    dependency_write,
    manifest_changes,
    mentions_dependencies,
    refusal_text,
)
from devstill.runtime.jssyntax import (
    SyntaxGate,
    boundary_candidates,
    close_brackets,
    empty_bodies,
)
from devstill.runtime.jstest import (
    LOAD_FAILURE,
    ScratchTypes,
    failing_from_output,
    parse_tsc,
    run_test_module,
    run_test_module_in,
    state_key,
    trace_type_errors,
    tsc_brief,
    tsc_text,
)
from devstill.runtime.loop_context import is_test_path
from devstill.runtime.toolchain import (
    JS_SUFFIXES,
    SCRATCH_PREFIX,
    is_scratch,
    without_scratch_number,
)
from devstill.runtime.tools import (
    _FAILURE,
    ToolError,
    ToolResult,
    Workspace,
    _decode,
    _kill,
    apply_text_edits,
    check_failure_line,
    render_hunks,
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


# --- JavaScript/TypeScript mode ---------------------------------------------------
# Same structure and rules as SYSTEM; the check is a short test module run by
# the project's own runner (vitest or jest). Built per run from the detected
# toolchain (constant per repository, so prefix caching still applies).

# The example's source (TypeScript, or plain JavaScript) and its edit.
JS_EXAMPLE = {
    True: (
        "export function ratio(num: number, den: number): number {\n  return num / den\n}",
        "number): number {\n  return num / den",
        "number): number | null {\n  if (den === 0) return null\n  return num / den",
    ),
    False: (
        "export function ratio(num, den) {\n  return num / den\n}",
        "  return num / den",
        "  if (den === 0) return null\n  return num / den",
    ),
}


def _js_check_bullet(toolchain):
    if not toolchain.runner:
        return (
            "- Check your change with bash: a short node script (node -e with "
            "node:assert, 2-6 asserts) that imports the changed code and asserts the "
            "requested behavior plus each behavior the request explicitly says to keep"
            + (
                ", and the type check (npx tsc --noEmit)"
                if toolchain.typescript
                else ""
            )
            + ". Only test what the request mentions."
        )
    where = toolchain.scratch_name(1).replace("_1.", "_<n>.")
    plain = getattr(toolchain, "plain_suffix", None)
    if plain:
        where = (
            toolchain.scratch_name(1, plain).replace("_1.", "_<n>.")
            + f" ({toolchain.test_suffix} when it contains JSX)"
        )
    folder = (
        toolchain.test_dir + "/"
        if toolchain.test_dir not in ("", ".")
        else "the repository root"
    )
    framework = (
        "describe, it and expect are globals"
        if toolchain.globals
        else "import describe, it and expect from 'vitest'"
    )
    bullet = (
        f"- Check your change with the test tool: a short {toolchain.runner} test "
        "module (under about 30 lines, no comments) that imports the changed code "
        "the way the project's tests do, sets things up once, calls the code "
        "directly and asserts the requested behavior plus each behavior the "
        "request explicitly says to keep: one short it() per scenario the request "
        "names, covering each alternative it lists (every value of an 'X or Y'). "
        f"It runs alone with the project's test runner as {where} (relative imports "
        f"start from {folder}; {framework}); a failing expect shows expected and "
        "received values."
    )
    if toolchain.testing_library:
        bullet += (
            " React components and hooks can be rendered with "
            "@testing-library/react (render, screen, act, renderHook)."
        )
    bullet += " Only test what the request mentions."
    if toolchain.import_sample:
        bullet += "\n  Existing tests import the project like this:\n" + "\n".join(
            "  " + line for line in toolchain.import_sample
        )
    return bullet


def js_system(toolchain):
    check = "test(code with expects)" if toolchain.runner else "bash(check command)"
    first = (
        "related tests and the type check run first"
        if toolchain.typescript
        else "related tests run first"
    )
    lines = [
        "You are Devstill Quick, a fast, careful coding agent working in a copy of the user's repository. Make the smallest correct change that fully satisfies the request.",
        "",
        "How to work:",
        "- The request comes with source excerpts retrieved from the repository. They are the current file contents: edit from them directly. Read or search only for something you need that is not shown.",
        "- Change files with edit (or write for a new file): give the path and edits, a list of {oldText, newText} blocks, and put all changes to one file in a single edit call. Each oldText must match the current file exactly and uniquely: copy it from the shown source, keep it short (the lines you change plus enough context to be unique) and never retype unchanged code.",
        _js_check_bullet(toolchain),
        (
            "- Send several tool calls in one response whenever you can; they run in "
            f"order. The fastest finish is a single response with three calls: "
            f"edit(...), {check}, done(summary). If the check fails, done is ignored "
            "and you see the failure. Make every change the request needs before that "
            "check: once a check passes after your last edit and would have failed "
            f"before your edits, the task finishes ({first})."
        ),
        "- A failing expect about requested or preserved behavior means the code is wrong: fix the code. Never delete or weaken an expectation to make it pass; change one only if it misstates the request.",
        "- Keep behavior the request does not mention unchanged. Do not edit tests or docs unless asked, and do not add changelog or version notes unless asked.",
    ]
    if toolchain.typescript:
        lines.append(
            "- This is a TypeScript project: its type check (tsc --noEmit) must keep "
            "passing, so keep types exact (declare new parameters and options in the "
            "existing types). Do not widen existing exported types to make a test "
            "compile (cast in the test instead); make a new runtime check a type "
            "predicate (value is T); if type errors appear at lines you did not "
            "change, fix the declaration you changed."
        )
    lines.append("- Finish by calling done with a one-line summary.")
    return "\n".join(lines)


def js_tools(toolchain):
    """TOOLS for JS mode: the python tool becomes `test` (or is dropped)."""
    runner = toolchain.runner
    example = (
        f"npx {runner} run {toolchain.scratch_name(1).replace('__devstill_check_1', 'file')}"
        if runner == "vitest"
        else f"npx {runner} {toolchain.scratch_name(1).replace('__devstill_check_1', 'file')}"
        if runner
        else 'node -e "..."'
    )
    tools = []
    for tool in json.loads(json.dumps(TOOLS)):
        name = tool["function"]["name"]
        if name == "python":
            if not runner:
                continue
            folder = (
                toolchain.test_dir + "/"
                if toolchain.test_dir not in ("", ".")
                else "the root"
            )
            tool["function"] = {
                "name": "test",
                "description": f"Run a short {runner} test module against the current "
                "code. Pass the module source with its own imports (describe/it/expect). "
                f"It is written to a temporary file in {folder} (relative imports start "
                "there), run alone with the project's test runner and deleted "
                "afterwards; a test that runs no expect fails. Returns each failing "
                "test with expected/received values and the failing check line, or "
                "'N passed'.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "code": {"type": "string", "description": "Test module source"},
                        "timeout": {
                            "type": "number",
                            "description": "Seconds (default 30)",
                        },
                    },
                    "required": ["code"],
                },
            }
        elif name == "bash":
            tool["function"]["description"] = (
                "Run a shell command in the repository root (e.g. grep -rn, "
                + ("npx tsc --noEmit, " if toolchain.typescript else "")
                + f"{example}). Returns output and exit code. Dependencies are "
                "installed (node_modules, a shared install): never install, "
                "reinstall or modify packages (reading them is fine). The "
                "workspace is a copy without git history, so git commands do not work. "
                "Files created by commands are not part of the change; create files "
                "with write."
            )
        tools.append(tool)
    return tools


def js_example(toolchain):
    """The finished example exchange for JS mode (TypeScript or JavaScript)."""
    import posixpath

    typed = toolchain.typescript or toolchain.test_suffix.endswith((".ts", ".tsx"))
    source, old, new = JS_EXAMPLE[typed]
    module = "src/ratio.ts" if typed else "src/ratio.js"
    folder = toolchain.test_dir if toolchain.test_dir not in ("", ".") else "."
    target = posixpath.relpath("src/ratio", folder)
    if not target.startswith("."):
        target = "./" + target
    header = (
        ""
        if toolchain.globals or toolchain.runner != "vitest"
        else "import { expect, it } from 'vitest'\n"
    )
    if toolchain.runner:
        check_call = (
            "test",
            {
                "code": header + f"import {{ ratio }} from '{target}'\n\n"
                "it('returns null for a zero denominator', () => {\n"
                "  expect(ratio(1, 0)).toBeNull()\n"
                "  expect(ratio(3, 4)).toBe(0.75)\n"
                "})\n"
            },
            "1 passed",
        )
    else:
        check_call = (
            "bash",
            {
                "command": 'node --input-type=module -e "import assert from '
                "'node:assert'; "
                f"import {{ ratio }} from './{module}'; "
                "assert.strictEqual(ratio(1, 0), null); "
                'assert.strictEqual(ratio(3, 4), 0.75)"'
            },
            "[exit code 0]",
        )
    return [
        {
            "role": "user",
            "content": "Example task (already finished):\n\nRequest:\nMake ratio "
            "return null for a zero denominator instead of Infinity. Keep normal "
            "ratios.\n\nRepository context (current file contents):\n\n### "
            f"{module} — 3 lines\nwhole file:\n```"
            + ("typescript" if typed else "javascript")
            + f"\n{source}\n```",
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
                                "path": module,
                                "edits": [{"oldText": old, "newText": new}],
                            }
                        ),
                    },
                },
                {
                    "id": "example_2",
                    "type": "function",
                    "function": {
                        "name": check_call[0],
                        "arguments": json.dumps(check_call[1]),
                    },
                },
                {
                    "id": "example_3",
                    "type": "function",
                    "function": {
                        "name": "done",
                        "arguments": json.dumps(
                            {"summary": "ratio returns null for a zero denominator"}
                        ),
                    },
                },
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "example_1",
            "content": f"Edited {module}: replaced 1 block.",
        },
        {"role": "tool", "tool_call_id": "example_2", "content": check_call[2]},
        {"role": "tool", "tool_call_id": "example_3", "content": "ok"},
    ]


def loop_prompt(toolchain=None):
    """(system prompt, tools, example exchange) for the run's language.

    Python projects (toolchain None or Python) get the module constants
    SYSTEM, TOOLS and EXAMPLE themselves, unchanged.
    """
    if toolchain is None or not toolchain.js:
        return SYSTEM, TOOLS, EXAMPLE
    return js_system(toolchain), js_tools(toolchain), js_example(toolchain)


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


def js_arguments(text):
    """JS mode: repair_json, then once more with the closing brackets the
    text lacks at its end (a call cut short after its last value)."""
    try:
        return repair_json(text)
    except (TypeError, ValueError):
        closed = close_brackets(text or "")
        if closed is None:
            raise
        return repair_json(closed)


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
# Python test runners for JS mode: TEST_RUNNER without `npm test`, which
# JS mode resolves through package.json scripts instead.
PY_TEST_RUNNER = re.compile(
    _COMMAND_START
    + r"(?:"
    + _PYTHON
    + r"\s+-m\s+(?:pytest|unittest|doctest|tox|nox|coverage\s+run\s+(?:-\S+\s+)*-m\s+(?:pytest|unittest))\b"
    r"|(?:\S*/)?(?:pytest|py\.test|tox|nox)\b"
    r"|coverage\s+run\s+(?:-\S+\s+)*-m\s+(?:pytest|unittest)\b"
    r"|make\s+(?:test|check)\b"
    r"|(?:" + _PYTHON + r"\s+)?\S*(?:runtests\.py|manage\.py\s+test)\b)"
)
# --- JS mode only: JavaScript commands -------------------------------------------
# A flag token with a single parse ("--a" never splits as "-" + "-a"), so a
# failing match cannot backtrack exponentially over many flags.
_FLAG = r"-{1,2}[^\s-]\S*"
_JS_END = r"(?![\w.-])"  # "vitest" but not "vitest-report.json"
_JS_LAUNCH = (
    r"(?:npx\s+(?:" + _FLAG + r"\s+)*|npm\s+exec\s+(?:--\s+)?"
    r"|(?:pnpm|yarn)\s+(?:exec\s+|dlx\s+)?|bunx\s+|bun\s+(?:x\s+)?)?"
)
# vitest/jest/mocha at command position (directly, through npx/pnpm/yarn/bun
# or node_modules/.bin), and node --test.
JS_TEST_RUNNER = re.compile(
    _COMMAND_START + _JS_LAUNCH + r"(?:\S*/)?(?P<runner>vitest|jest|mocha)" + _JS_END
)
NODE_TEST = re.compile(_COMMAND_START + r"(?:\S*/)?node\s+(?:-\S+\s+)*--test" + _JS_END)
# A package.json script run: npm test, pnpm test:spec, yarn run test, ...
PACKAGE_SCRIPT = re.compile(
    _COMMAND_START
    + r"(?:npm|pnpm|yarn|bun)\s+(?:run(?:-script)?\s+)?(?P<script>[^\s;&|()<>'\"]+)"
)
# Runner arguments (in the runner's own command) that list, describe or set
# up instead of running tests.
JS_NOT_A_RUN = re.compile(r"(?:^|\s)(?:--version|-v|--listTests|--showConfig)(?=\s|$)")
VITEST_NOT_A_RUN = re.compile(r"\s+(?:list|init|bench|typecheck)" + _JS_END)
_COMMAND_END = re.compile(r"[;&|\n]")
# Inline node scripts (node -e/--eval/-p or a heredoc) that can fail on a
# wrong result.
NODE_SCRIPT = re.compile(
    _COMMAND_START + r"(?:\S*/)?node(?:\s+-{1,2}\w[\w-]*(?:=\S+)?)*?\s+"
    r"(?:-e\b|--eval\b|-p\b|--print\b|-\s*<<|<<)"
)
NODE_ASSERTING = re.compile(
    r"(?<!console\.)\bassert\b|node:assert|\bthrow\b|process\.exit\(\s*1"
)
# A failing type check, node script or test run at command position is
# evidence against the change, like a failing pytest.
JS_CHECK_COMMAND = re.compile(
    _COMMAND_START
    + r"(?:"
    + _JS_LAUNCH
    + r"(?:\S*/)?(?:tsc|vue-tsc|node|vitest|jest|mocha|tsx|ts-node)"
    + _JS_END
    + r"|(?:npm|pnpm|yarn|bun)\s+(?:run\s+)?(?:test|typecheck|type-check)"
    + _JS_END
    + r")"
)
# One type-check invocation (tsc, or a package.json script that only runs it).
_TSC_CALL = re.compile(
    r"(?:\w+=\S*\s+)*" + _JS_LAUNCH + r"(?:\S*/)?(?:tsc|vue-tsc)" + _JS_END
)
_SCRIPT_CALL = re.compile(
    r"(?:\w+=\S*\s+)*(?:npm|pnpm|yarn|bun)\s+(?:run(?:-script)?\s+)?(?P<script>\S+)"
)
# A JS test module verifies only if it can fail on a wrong result
# (expectTypeOf is a no-op at run time).
JS_ASSERTING = re.compile(
    r"\bexpect(?:\.\w+)?\s*\(|(?<!console\.)\bassert(?:\.\w+)?\s*\("
)


def js_test_run(command, test_scripts=()):
    """A JS test run at command position (JS mode).

    Package-manager scripts count when they are `test` or run a test runner
    (`test_scripts`, from package.json): `pnpm test:types` (tsc) does not.
    """
    for match in JS_TEST_RUNNER.finditer(command):
        end = _COMMAND_END.search(command, match.end())
        rest = command[match.end() : end.start() if end else len(command)]
        if JS_NOT_A_RUN.search(rest) or (
            match.group("runner") == "vitest" and VITEST_NOT_A_RUN.match(rest)
        ):
            continue
        return True
    if NODE_TEST.search(command):
        return True
    scripts = {"test", *test_scripts}
    return any(m.group("script") in scripts for m in PACKAGE_SCRIPT.finditer(command))


def is_type_check(command, type_scripts=()):
    """The command only type-checks (tsc, or a script that runs just tsc)."""
    found = False
    for part in re.split(r"&&|\|\||;|\n", command):
        part = part.split("|")[0].strip()
        if not part or re.match(r"cd\s", part):
            continue
        if _TSC_CALL.match(part):
            found = True
            continue
        script = _SCRIPT_CALL.match(part)
        if script and script.group("script") in {
            "typecheck",
            "type-check",
            *type_scripts,
        }:
            found = True
            continue
        return False
    return found


def bash_check_kind(command, js=False, test_scripts=()):
    """'tests' for a test run, 'script' for an asserting inline script, else None.

    JS commands count only in JS mode (`js`); `test_scripts` names the
    package.json scripts that run the project's test runner.
    """
    if re.search(r"--collect-only|--co\b|--fixtures|--help", command):
        return None  # lists tests without running them
    if not js:
        if TEST_RUNNER.search(command):
            return "tests"
        if PY_SCRIPT.search(command) and ASSERTING.search(command):
            return "script"
        return None
    if PY_TEST_RUNNER.search(command) or js_test_run(command, test_scripts):
        return "tests"
    if PY_SCRIPT.search(command) and ASSERTING.search(command):
        return "script"
    if NODE_SCRIPT.search(command) and NODE_ASSERTING.search(command):
        return "script"
    return None


def compare_tests(now, before):
    """Per-test verdict of a failing JS test check against the original code.

    Messages are compared without the scratch module's number: the run on
    the original code uses another scratch file name.
    """
    if not now or not before:
        return None
    tests_now, tests_before = now.get("tests") or {}, before.get("tests") or {}

    def said(record):
        return {
            f["name"]: without_scratch_number(f.get("message"))
            for f in record.get("failures") or []
        }

    said_now, said_before = said(now), said(before)
    load_now = without_scratch_number(now.get("load_error"))
    failing = [name for name, status in tests_now.items() if status == "failed"]
    both = [name for name in failing if tests_before.get(name) == "failed"]
    same = [name for name in both if said_now.get(name) == said_before.get(name)]
    # Failures thrown inside repository source (not the test module): the
    # code under test crashes, maybe the very crash the request is about.
    in_source = {f["name"] for f in now.get("failures") or [] if f.get("source")}
    return {
        "broke": [name for name in failing if tests_before.get(name) == "passed"],
        "same": same,
        "changed": [
            name for name in both if said_now.get(name) != said_before.get(name)
        ],
        "fixed": [
            name
            for name, status in tests_now.items()
            if status == "passed" and tests_before.get(name) == "failed"
        ],
        "load_same": bool(load_now)
        and load_now == without_scratch_number(before.get("load_error")),
        # Of `same`: a runtime error (TypeError, ...) thrown inside the test
        # module itself, identically on both: usually a broken test setup.
        "setup": [
            name
            for name in same
            if SETUP_ERROR.match(said_now.get(name) or "") and name not in in_source
        ],
        # Of `same`: thrown inside repository source on both (a crash the
        # change does not reach yet, or a test that calls the code wrongly).
        "crash": [name for name in same if name in in_source],
    }


# A failure that is a runtime error, not a failed expectation.
SETUP_ERROR = re.compile(r"\s*(?:TypeError|ReferenceError|RangeError|SyntaxError)\b")


def js_verdict_text(verdict):
    if not verdict:
        return ""

    def names(items):
        shown = ", ".join(f"'{name}'" for name in items[:6])
        return shown + (f" and {len(items) - 6} more" if len(items) > 6 else "")

    notes = []
    if verdict["broke"]:
        notes.append(
            f"- {names(verdict['broke'])} passed on the original code and fail now: "
            "if a test checks behavior the request keeps, your change broke it (fix "
            "the code); if it checks old behavior the request changes, fix the test."
        )
    if verdict["changed"]:
        notes.append(
            f"- {names(verdict['changed'])} also failed on the original code, with "
            "another message: your change reached it, but the result is still not "
            "what the test expects."
        )
    same, fixed = verdict["same"], verdict["fixed"]
    if fixed:
        notes.append(
            f"- {names(fixed)} failed on the original code and pass now"
            + (", so your change works there." if same else ".")
        )
    if same:
        setup = [name for name in verdict.get("setup") or [] if name in same]
        crash = [name for name in verdict.get("crash") or [] if name in same]
        other = [name for name in same if name not in setup and name not in crash]
        if other:
            notes.append(
                f"- {names(other)} fail the same way on the original code (same "
                "message): either your change does not reach this scenario yet, or "
                "the test does not set it up the way the program really runs"
                + (" (compare it with the passing tests)." if fixed else ".")
            )
        if crash:
            notes.append(
                f"- {names(crash)} throw the same error inside the source on the "
                "original code: if the request covers this scenario, your change "
                "does not reach it yet (see where it is thrown above); otherwise the "
                "test calls the code in a way the program never does."
            )
        if setup:
            notes.append(
                f"- {names(setup)} throw the same error on the original code, inside "
                "the test module itself: usually the test is wrong (its setup, or how "
                "it calls the code; build things the way the existing tests do), "
                "unless the request is about exactly this error."
            )
    if verdict["load_same"]:
        notes.append(
            "- the module fails to load the same way on the original code, so it "
            "has not checked your change yet: fix the module (an import path, its "
            "syntax or setup), or create the file it imports if the request asks "
            "for it."
        )
    if not notes:
        return ""
    return (
        "\nThe results above are from your current, edited code (your files are "
        "unchanged). For comparison, the same module also ran on a pristine copy of "
        "the original code:\n" + "\n".join(notes)
    )


def uninformative(record, verdict):
    """JS: a failing test check that says nothing against the change: its
    module fails to load the same way on the original code, or every failing
    test fails there with the same message (nothing that passed there fails)
    and none of them crashes inside repository source (a reproduced crash
    the change has not fixed speaks against it)."""
    if not verdict or not record:
        return False
    if verdict["load_same"]:
        return True
    failing = [name for name, s in (record.get("tests") or {}).items() if s == "failed"]
    return (
        bool(failing)
        and not record.get("load_error")
        and not verdict["broke"]
        and not verdict["changed"]
        and set(failing) <= set(verdict["same"])
        and not set(failing) & set(verdict.get("crash") or ())
    )


_ERROR_HEAD = re.compile(r"\b([A-Z]\w*(?:Error|Exception))\b:?[ \t]*([^\n]*)")


def named_in_request(message, request):
    """JS: a failure's error is one the request itself names: its error type
    (TypeError, ...) or the leading phrase of its message ("Cannot read
    properties of undefined") appears in the request text."""
    if not message or not request:
        return False
    match = _ERROR_HEAD.search(message)
    if not match:
        return False
    kind, text = match.group(1), match.group(2)
    if re.search(r"\b" + re.escape(kind) + r"\b", request):
        return True
    phrase = re.split(r"[(\[{'\"`:]", text, maxsplit=1)[0].strip()
    return len(phrase) >= 12 and phrase.lower() in request.lower()


def named_failures(record, request):
    """Names of a record's failing tests whose error the request names."""
    return [
        failure.get("name")
        for failure in (record or {}).get("failures") or []
        if named_in_request(failure.get("message"), request)
    ]


def scratch_never_transforms(record):
    """The scratch module itself has a syntax error (a test bug, whatever the
    code under test does)."""
    error = (record or {}).get("load_error") or ""
    return bool(
        re.search(r"Transform failed|Failed to parse|SyntaxError|ERROR:", error)
        and re.search(re.escape(SCRATCH_PREFIX) + r"\d+\.[\w.]+:\d+:\d+", error)
    )


def _top_level_rest(command, start):
    """Text after the first top-level ;, &&, ||, & or newline at or after
    `start` (quotes respected), or None when the command ends first."""
    quote, index = None, start
    while index < len(command):
        char = command[index]
        if quote:
            if char == "\\" and quote == '"':
                index += 2
                continue
            if char == quote:
                quote = None
        elif char in "'\"":
            quote = char
        elif char == "\\":
            index += 2
            continue
        elif command.startswith(("&&", "||"), index):
            return command[index + 2 :]
        elif char in ";\n" or (
            char == "&"
            and not command.startswith("&>", index)
            and (index == 0 or command[index - 1] not in "<>|")
        ):
            return command[index + 1 :]
        index += 1
    return None


# Runner options whose value is the next word (not a test path).
_VALUE_OPTIONS = {
    "-t",
    "--testNamePattern",
    "-c",
    "--config",
    "--reporter",
    "--outputFile",
    "--root",
    "-r",
    "--dir",
    "--project",
    "--environment",
    "--pool",
    "--shard",
    "--testTimeout",
    "--maxWorkers",
    "--minWorkers",
    "--exclude",
    "--mode",
    "--testPathPattern",
    "--testPathIgnorePatterns",
    "--selectProjects",
}
_JS_PATH = re.compile(r"/|\.[cm]?[jt]sx?$")


def js_run_counts(command, known, test_scripts=()):
    """JS mode: a test command (bash_check_kind 'tests') counts as a check of
    the change only when its exit status is the runner's (nothing runs after
    the last runner with ;, &&, || or &) and, if it names test files, at
    least one is a repository file or a file the agent wrote (`known(path)`).
    `test_scripts`: package.json scripts that run the test runner."""
    runners = []
    for match in JS_TEST_RUNNER.finditer(command):
        end = _COMMAND_END.search(command, match.end())
        rest = command[match.end() : end.start() if end else len(command)]
        if JS_NOT_A_RUN.search(rest) or (
            match.group("runner") == "vitest" and VITEST_NOT_A_RUN.match(rest)
        ):
            continue
        runners.append((match.end(), rest))
    ends = [end for end, _ in runners]
    ends += [m.end() for m in NODE_TEST.finditer(command)]
    scripts = {"test", *test_scripts}
    ends += [
        m.end()
        for m in PACKAGE_SCRIPT.finditer(command)
        if m.group("script") in scripts
    ]
    if not ends:
        return True
    after = _top_level_rest(command, max(ends))
    if after is not None and after.strip():
        return False
    paths = []
    for _, rest in runners:
        try:
            words = shlex.split(rest)
        except ValueError:
            words = rest.split()
        skip = False
        for word in words:
            if skip:
                skip = False
                continue
            if word.startswith("-"):
                skip = word in _VALUE_OPTIONS
                continue
            if _JS_PATH.search(word):
                paths.append(word)
    return not paths or any(known(path) for path in paths)


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
    toolchain=None,
):
    """Run the tool loop; return an outcome compatible with the structured editor.

    Cancellation (InterruptedError) propagates; the task deadline (TimeoutError)
    ends the loop as a failed outcome; the background test guard is always shut
    down, whichever way the loop exits. `toolchain` (a JS/TS Toolchain) selects
    the JavaScript prompt, tools and checks; None keeps Python mode.
    """
    if toolchain is not None and not toolchain.js:
        toolchain = None
    system, tools, example = loop_prompt(toolchain)
    gate = None
    if toolchain is not None:
        # JS mode: edits that break a file's syntax are refused, and edit
        # confirmations show every changed region.
        gate = SyntaxGate(workspace.root, workspace.env)
        workspace.content_gate = gate
        workspace.change_view = render_hunks
    state = {
        "messages": [
            {"role": "system", "content": system},
            *json.loads(json.dumps(example)),
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
    if toolchain is not None:
        # JS mode only: the request (named errors are never excused), work
        # to stop when the loop ends, and counters of the JS mechanisms.
        state["request"] = query
        state["stops"] = []
        state["js_stats"] = {
            "guard_wait_seconds": 0.0,
            "guard_waits": 0,
            "original_runs_parallel": 0,
            "original_runs_swapped": 0,
            "scratch_reads": 0,
            "module_patches": 0,
            "insensitive_flags": 0,
            "traced_type_errors": 0,
            "mirror_resyncs": 0,
            "named_error_blocks": 0,
            "boundary_repairs": 0,
            "resend_refusals": 0,
            "empty_body_flags": 0,
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
            and not state["regressions"]
            # JS: no earlier failing check of the same edit still stands
            # (only failures a later passing check made moot).
            and (
                toolchain is None or all(c["ok"] or c.get("superseded") for c in checks)
            ),
            "regressions": state["regressions"],
            "usage": state["usage"],
            # JS mode: files of the linked dependency install that a command
            # changed (a damaged shared install; never set in Python mode).
            **(
                {"dependencies_changed": state["dependencies_changed"]}
                if state.get("dependencies_changed")
                else {}
            ),
            **({"js_stats": state["js_stats"]} if "js_stats" in state else {}),
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
            toolchain=toolchain,
            tools=tools,
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
        for stop in state.get("stops", ()):
            stop()
        if guard is not None:
            guard.close()
        if gate is not None:
            gate.close()
            workspace.content_gate = workspace.change_view = None


# JS mode budget: no model call starts within JS_FINISH_RESERVE seconds of
# the deadline and a started one must end JS_CALL_RESERVE seconds before it;
# tools stop JS_TOOL_RESERVE seconds before it. The final finish (re-run,
# related tests, an earlier snapshot) uses what is left.
JS_FINISH_RESERVE = 15.0
JS_CALL_RESERVE = 8.0
JS_TOOL_RESERVE = 6.0
# After a response with edits, wait this long at most for the background
# type check and related tests before the next model call (guards without
# timing estimates; JsGuard waits only when the type check is expected
# within JS_GUARD_SOON seconds, plus JS_GUARD_MARGIN).
JS_GUARD_WAIT = 4.0
JS_GUARD_SOON = 1.0
JS_GUARD_MARGIN = 0.3
# Earlier edit snapshots tried when the final state fails (JS mode).
JS_SALVAGE_CANDIDATES = 6
BROKE_TESTS = (
    "existing tests that pass on the original code now fail: your change broke "
    "them, so fix the source first (unless the request changes that behavior)"
)


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
    toolchain=None,
    tools=TOOLS,
):
    messages = state["messages"]
    js = toolchain is not None
    # Recorded agent checks: "python:\n<code>" in Python mode, "test:\n<code>"
    # (a scratch test module) in JS mode.
    CHECK = "test:\n" if js else "python:\n"
    # Guards that never run in the workspace need no pause around swaps.
    shared_guard = guard is not None and not getattr(guard, "isolated", False)
    tool_names = [t["function"]["name"] for t in tools]
    test_scripts = tuple(toolchain.test_scripts) if js else ()
    commands = state["commands"]
    nudged = False
    step = 0
    failed_assert = None  # the latest failing check comparison, if any
    failed_code = None  # the check that produced it
    guard_reports = 0
    failed_check_warned = None  # last_edit_step when we last refused a finish
    discrimination_asked = None  # last_edit_step when a non-testing check was refused
    # JS mode only ------------------------------------------------------------
    model_stop = deadline - JS_FINISH_RESERVE if js else deadline
    call_deadline = deadline - JS_CALL_RESERVE if js else deadline
    work_deadline = deadline - JS_TOOL_RESERVE if js else deadline
    # Changed files' bytes after each response that edited: {"turn", "files"}.
    snapshots = []
    salvaging = {}  # while an earlier snapshot is judged: {"code": its check}
    last_module = {}  # the latest test module: {"code", "name"}
    guard_shown = set()  # (guard generation, part) already reported
    guard_due = {"edited": False}  # an edit's guard results are not shown yet
    scratch_types = {"enabled": js and bool(toolchain.tsc_path)}
    dependencies = (
        DependencyPaths(workspace.root, toolchain.dependencies or ("node_modules",))
        if js
        else None
    )
    manifest = {"enabled": True}
    stats = state.get("js_stats", {})  # JS mode counters (empty in Python mode)
    if js:
        # The budget this loop really has, from its start: until its
        # deadline (the final finish ends by then) and until the last model
        # call may start.
        loop_start = time.perf_counter()
        stats["loop_budget"] = {
            "deadline_seconds": round(deadline - loop_start, 3),
            "model_stop_seconds": round(model_stop - loop_start, 3),
            "max_model_calls": max_turns,
        }
    request = state.get("request") or ""
    insensitive_flagged = set()  # JS: test names already flagged as insensitive
    rejected_edits = {}  # JS: (path, edits, file sha) -> why the gate refused it
    background = {"processes": set(), "closed": False}

    def stop_background():
        with original_lock:
            background["closed"] = True
            processes = list(background["processes"])
        for process in processes:
            _kill(process)

    original_lock = threading.Lock()
    if js:
        state["stops"].append(stop_background)

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
    # JS mode: check source -> (limit, result) of a run on the original
    # sources that timed out; it is not repeated with the same limit or less.
    original_timeouts = {}
    # JS mode: check source -> a run on the original code in a pristine copy
    # that is still running ({"done", "result", "limit"}).
    original_pending = {}

    def original_copy():
        """JS: the guard's pristine copy for original-code runs (None: not
        available, then the workspace's files are swapped as in Python)."""
        root_of = getattr(guard, "original_root", None) if guard is not None else None
        return root_of() if root_of is not None else None

    def start_original(code, limit):
        """JS: start running `code` on the original code in the pristine
        copy, alongside whatever runs now (no-op when known or running)."""
        if code in original_runs or code in original_pending:
            return original_pending.get(code)
        root = original_copy()
        if root is None:
            return None
        handle = {
            "done": threading.Event(),
            "result": None,
            "limit": limit,
            "started": time.perf_counter(),
            "processes": [],
            "killed": False,
        }

        def register(process):
            with original_lock:
                if background["closed"] or handle["killed"]:
                    return False
                background["processes"].add(process)
                handle["processes"].append(process)
            return True

        def work():
            try:
                result = run_test_module_in(
                    root, workspace.env, toolchain, code, limit, register
                )
            except Exception:  # noqa: BLE001 - unknown: the swap path runs it
                result = None
            # A stopped run's output says nothing about the original code.
            handle["result"] = None if handle["killed"] else result
            handle["done"].set()

        original_pending[code] = handle
        stats["original_runs_parallel"] = stats.get("original_runs_parallel", 0) + 1
        threading.Thread(target=work, daemon=True).start()
        return handle

    def stop_original(code, handle):
        """JS: give up on a pending original-code run and stop its processes
        (a comparison that does not wait for it no longer needs it)."""
        with original_lock:
            handle["killed"] = True
            processes = list(handle["processes"])
        if original_pending.get(code) is handle:
            original_pending.pop(code, None)
        for process in processes:
            _kill(process)
        stats["original_runs_stopped"] = stats.get("original_runs_stopped", 0) + 1

    def wait_original(code, handle, seconds):
        """JS: wait up to `seconds` for a pending original-code run, in short
        slices that honour cancellation (which stops the run)."""
        stop = time.perf_counter() + seconds
        while not handle["done"].wait(
            timeout=max(0.0, min(0.1, stop - time.perf_counter()))
        ):
            try:
                checkpoint()
            except BaseException:
                stop_original(code, handle)
                raise
            if time.perf_counter() >= stop:
                return False
        return True

    def run_check(code, timeout, typed=False):
        if js:
            return run_test_module(
                workspace,
                toolchain,
                code,
                timeout,
                checkpoint,
                type_start=scratch_type_start(code) if typed else None,
            )
        return workspace.python(code, timeout, checkpoint)

    def scratch_type_start(code):
        """JS (TypeScript): the scratch module's type check, started with
        its test run and kept only when the run fails (a type check that
        takes over 4 s is not tried again)."""

        def disable():
            scratch_types["enabled"] = False

        def start(name):
            if not scratch_types["enabled"] or time.perf_counter() > model_stop:
                return None
            return ScratchTypes(
                workspace, toolchain, name, code, timeout=4.0, on_timeout=disable
            )

        return start

    def on_original(code, timeout=None):
        """Run a check script against the original sources (no model call).

        The original state never changes, so each script runs there once
        (a run that timed out is not kept: it says nothing; in JS mode it is
        repeated only with a longer time limit).
        """
        if originals is None or not workspace.changed:
            return None
        if code in original_runs:
            return original_runs[code]

        def available():
            limit = min(bash_timeout, max(1.0, deadline - time.perf_counter() - 1))
            return limit if timeout is None else min(limit, timeout)

        if js and code in original_timeouts:
            tried, timed_out = original_timeouts[code]
            if available() <= tried + 0.5:
                return timed_out  # no more time than the run that timed out
        if js:
            # Run (or finish waiting for) it in the pristine copy: the
            # workspace keeps the agent's files.
            handle = original_pending.get(code) or start_original(code, available())
            if handle is not None:
                if not wait_original(code, handle, available() + 0.5):
                    # Still running after the time this comparison allows:
                    # stopped (as a timed-out run), repeated only with a
                    # longer limit.
                    waited = time.perf_counter() - handle["started"]
                    stop_original(code, handle)
                    result = ToolResult(
                        "[the run on the original code has not finished]",
                        ok=False,
                        meta={"timed_out": True, "record": None, "pending": True},
                    )
                    original_timeouts[code] = (waited, result)
                    return result
                original_pending.pop(code, None)
                result = handle["result"]
                if result is not None:
                    if not result.meta.get("timed_out"):
                        original_runs[code] = result
                    else:
                        original_timeouts[code] = (handle["limit"], result)
                    return result
            stats["original_runs_swapped"] = stats.get("original_runs_swapped", 0) + 1
        if shared_guard:
            guard.close()  # background test runs must not see swapped files
        limit = available()
        try:
            with workspace.original_state(originals):
                result = run_check(code, limit)
        finally:
            if shared_guard:
                guard.changed()  # restart runs for the restored current state
        if not result.meta.get("timed_out"):
            original_runs[code] = result
        elif js:
            original_timeouts[code] = (limit, result)
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

    def beyond_original(errors, wait):
        """Type errors of the agent's own tsc run that the original code does
        not have (file, code and message; counted), or None if unknown."""
        compare = getattr(guard, "type_errors_beyond_original", None)
        if compare is None or not errors:
            return None
        return compare(errors, workspace.root, wait)

    def preexisting(check):
        """Failing test ids of a test command that all fail on the original too."""
        if guard is None or originals is None or not workspace.changed:
            return []
        if js:
            if check.get("type_errors"):
                # The agent's own type check: errors the original code has too.
                new = beyond_original(
                    check["type_errors"],
                    max(0.0, min(guard.timeout, deadline - time.perf_counter() - 2)),
                )
                return [tsc_text(e) for e in check["type_errors"]] if new == [] else []
            known_failures = getattr(guard, "known_failures", None)
            if known_failures is None or not check.get("failing"):
                return []
            return known_failures(
                check["failing"],
                max(0.0, min(guard.timeout, deadline - time.perf_counter() - 2)),
            )
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

    def recheck(final=False):
        """Re-run the agent's latest python check if it edited afterwards.

        Costs no model call: a final edit that breaks what the agent already
        checked (e.g. deleting a helper it still uses) is caught here. JS
        mode, `final` (no model call is left): a failure identical on the
        original code is let through (the guard still decides).
        """
        nonlocal step
        python_checks = [
            c
            for c in commands
            if c["command"].startswith(CHECK) and not c.get("salvage")
        ]
        if not python_checks or python_checks[-1]["step"] > state["last_edit_step"]:
            return None
        code = python_checks[-1]["command"][len(CHECK) :]
        if js and salvaging.get("code") == code:
            return None  # salvage just ran this check on the restored state
        timeout = min(bash_timeout, max(1.0, deadline - time.perf_counter() - 1))
        report("Re-running the latest check")
        result = run_check(code, timeout)
        record = result.meta.get("record")
        step += 1
        commands.append(
            {
                "command": CHECK + code,
                "ok": result.ok,
                "check": not result.changed,
                "turn": state["calls"],
                "step": step,
                "output": result.text[-2000:],
                "record": record,
                "automatic": True,
                **({"state": state_now()} if js else {}),
            }
        )
        if result.ok:
            return None
        if not js and progressed(code, record, result.text):
            commands[-1]["check"] = False  # the check, not the change, is wrong
            commands[-1]["check_problem"] = True
            return None
        base = on_original(code)
        if js and known(base):
            verdict = compare_tests(record, base.meta.get("record"))
            commands[-1]["verdict"] = verdict
            if final and says_nothing(code, record, verdict):
                # No model call is left and every failure is the same on the
                # original code: the check says nothing against the change
                # (the guard still decides). With calls left, the agent hears
                # about it like any failing check.
                commands[-1]["check"] = False
                commands[-1]["check_problem"] = True
                return None
        compared = (
            (
                js_verdict_text(compare_tests(record, base.meta.get("record")))
                if js
                else verdict_text(compare_runs(record, base.meta.get("record")))
            )
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
        problem = recheck(final)
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
            if js and getattr(guard, "stale", None) is not None and guard.stale():
                # A change the loop never reported (e.g. a command removed a
                # file): resync the guard before reading its verdict.
                stats["mirror_resyncs"] = stats.get("mirror_resyncs", 0) + 1
                changed()
            # Slow suites must not dominate a quick task: wait briefly at most.
            wait = min(15.0, max(0.0, deadline - time.perf_counter() - 5))
            found = guard.regressions(wait, deadline)
            failing = (
                (found["regressions"] + found["added"] + found.get("type_errors", []))
                if found
                else []
            )
            state["regressions"] = failing
            if found and (guard_reports < 2 and not final) and js:
                guard_reports += 1
                return js_guard_feedback(found)
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
                if found.get("type_errors"):
                    parts.append(
                        "The type check (tsc --noEmit) reports new errors:\n"
                        + "\n".join(found["type_errors"][:10])
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

    # JS: type errors shown by the latest background note, and the message
    # it went into (the finish feedback then does not repeat them there).
    note_errors = {"message": None, "errors": set()}
    appending = {"last": False}  # JS: finish feedback goes into messages[-1]

    def type_error_lines(found, here):
        """JS: compact lines for the new type errors of `found` (each once
        per tool result), plus traces to the changed declarations."""
        items = found.get("type_error_items")
        if items is None:
            lines = list(found.get("type_errors") or [])
            return lines, "", []
        lines = [tsc_brief(error) for error in items]
        traced, kept = trace_type_errors(
            items[:6], workspace.root, original_of, list(workspace.changed)
        )
        if traced:
            stats["traced_type_errors"] = stats.get("traced_type_errors", 0) + len(kept)
        if here:
            lines = [line for line in lines if line not in note_errors["errors"]]
        return lines, traced, kept

    def js_guard_feedback(found):
        """JS: finish() feedback for guard findings: failing tests, each new
        type error once (compact, traced to a changed declaration when it is
        at a line the agent did not change) and the changes so far."""
        here = appending["last"] and note_errors["message"] == len(messages) - 1
        parts = []
        if found["regressions"]:
            parts.append(
                "Existing tests that passed before your change now fail:\n"
                + "\n".join(found["regressions"][:10])
            )
        if found["added"]:
            parts.append("Tests you added fail:\n" + "\n".join(found["added"][:10]))
        kept = []
        if found.get("type_errors"):
            lines, traced, kept = type_error_lines(found, here)
            if lines:
                parts.append(
                    "The type check (tsc --noEmit) reports new errors:\n"
                    + "\n".join(lines[:10])
                    + (f"\n[{len(lines) - 10} more]" if len(lines) > 10 else "")
                )
            else:
                parts.append(
                    "The type check (tsc --noEmit) reports the new errors shown above."
                )
            if traced:
                parts.append(traced)
        tail = found.get("output") or ""
        cut = tail.find("tsc --noEmit:\n")
        if cut >= 0:
            tail = tail[:cut]
        tail = tail.strip()
        js_diff = getattr(guard, "js_diff", None)
        diff = js_diff(kept) if js_diff is not None else guard.diff()
        return (
            "\n\n".join(parts)
            + (f"\n\nOutput (tail):\n{tail[-2500:]}" if tail else "")
            + (f"\n\nYour changes so far:\n```diff\n{diff}\n```" if diff else "")
            + "\nFix the code (or a test you added, if it is wrong) so they pass, "
            "unless the request explicitly asks to change that behavior."
        )

    def after_last_edit():
        return [c for c in commands if c["step"] > state["last_edit_step"]]

    def latest_passing_python():
        """The newest passing python check after the last edit, if any."""
        for check in reversed(after_last_edit()):
            if (
                check["command"].startswith(CHECK)
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
            and bash_check_kind(c["command"], js, test_scripts) == "tests"
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
        base = on_original(check["command"][len(CHECK) :])
        if base is None or not base.ok or base.meta.get("timed_out"):
            return None
        if js and any(
            fails_on_original(c)
            for c in after_last_edit()
            if c is not check
            and c["command"].startswith(CHECK)
            and c.get("check", True)
            and c["ok"]
        ):
            # An earlier passing check since the last edit already tells the
            # change apart from the original code.
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
            # JS: show this state's background results once they are ready.
            guard_due.update(edited=True, turn=state["calls"])

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
        if (
            js
            and check["command"].startswith(CHECK)
            and says_nothing(
                check["command"][len(CHECK) :],
                check.get("record"),
                check.get("verdict"),
            )
        ):
            # JS: every failure is identical on the original code; the guard
            # (related tests, type check) decides in finish(final=True).
            check["check"] = False
            check["check_problem"] = True
            return True
        return False

    def excused_before_named(check):
        """JS, shadow only: excused() without the named-error guard and
        without marking the check."""
        if check.get("timed_out"):
            return True
        if check["command"].startswith("python:\n") and progressed(
            check["command"][len("python:\n") :], check.get("record"), check["output"]
        ):
            return True
        return bool(
            check["command"].startswith(CHECK)
            and says_nothing(
                check["command"][len(CHECK) :],
                check.get("record"),
                check.get("verdict"),
                named=False,
            )
        )

    def says_nothing(code, record, verdict, named=True):
        """JS: a failing test check that says nothing against the change
        (uninformative) and whose failing tests never passed in an earlier
        run of the same module (then a later edit broke what it checks).
        With `named`, never when a failure's error is one the request names
        (the test reproduces the reported error, so it may well judge the
        change)."""
        if not uninformative(record, verdict):
            return False
        if named and named_failures(record, request):
            if says_nothing(code, record, verdict, named=False):
                stats["named_error_blocks"] = stats.get("named_error_blocks", 0) + 1
            return False
        failing = [
            name for name, s in (record.get("tests") or {}).items() if s == "failed"
        ]
        for command in commands:
            if command["command"] != CHECK + code or command.get("salvage"):
                continue  # salvage runs judged other versions of the change
            tests = (command.get("record") or {}).get("tests") or {}
            if any(tests.get(name) == "passed" for name in failing) or (
                verdict["load_same"] and "passed" in tests.values()
            ):
                return False
        return True

    def fails_on_original(check):
        """JS: a test check fails for real on the original code (a failing
        test or a module that does not load there; not merely unknown)."""
        base = on_original(check["command"][len(CHECK) :])
        if base is None or base.ok or base.meta.get("timed_out"):
            return False
        before = base.meta.get("record") or {}
        return bool(before.get("failed") or before.get("load_error"))

    def superseded(earlier, latest):
        """JS: an earlier failing test check after the last edit that the
        latest passing check `latest` makes moot: its module fails to load the
        same way on the original code, or each of its failing tests failed
        identically there (never one the change broke or changed) and passes
        under the same name in `latest`, either with its failing line
        unchanged (the test's setup was fixed) or after a runtime error
        thrown inside the test module itself (a setup bug)."""
        if not earlier["command"].startswith(CHECK):
            return False
        record, verdict = earlier.get("record") or {}, earlier.get("verdict") or {}
        code = earlier["command"][len(CHECK) :]
        if record.get("load_error"):
            return bool(verdict.get("load_same")) and says_nothing(
                code, record, verdict
            )
        failures = record.get("failures") or []
        if not failures:
            return False
        source = code.split("\n")
        latest_code = latest["command"][len(CHECK) :]
        latest_lines = {line.strip() for line in latest_code.split("\n")}
        passing = (latest.get("record") or {}).get("tests") or {}
        same = set(verdict.get("same") or ()) - set(verdict.get("crash") or ())
        for failure in failures:
            name, line = failure.get("name"), failure.get("line")
            if name not in same or passing.get(name) != "passed":
                return False
            if name in (verdict.get("setup") or []):
                continue
            text = (
                source[line - 1].strip()
                if isinstance(line, int) and 0 < line <= len(source)
                else ""
            )
            if not text or text not in latest_lines:
                return False
        return True

    def refuse_finish():
        """Refuse one finish while the latest check after the last edit failed."""
        nonlocal failed_check_warned
        check = latest_check()
        if (
            check is not None
            and check["ok"]
            and check.get("weakened")
            and failed_check_warned != state["last_edit_step"]
        ):
            failed_check_warned = state["last_edit_step"]
            return (
                "Not finished: your latest check dropped or changed an assertion that "
                "just failed. If that assertion states what the request requires or "
                "keeps, restore it and fix the code; if it was wrong, say so and call "
                "done again."
            )
        if (
            check is None
            or check["ok"]
            or failed_check_warned == state["last_edit_step"]
        ):
            return None
        failed_check_warned = state["last_edit_step"]
        if check.get("timed_out"):
            return "Not finished: your latest test command did not finish. Run a " + (
                "narrower command (one test file, or -t with a test name), then "
                "call done."
                if js
                else "narrower command (one test file, -x or -k), then call done."
            )
        if not check["command"].startswith(CHECK):
            old = preexisting(check)
            if old and check.get("type_errors"):
                return (
                    "Not finished: your latest type check failed, but only with errors "
                    "the original code has too: " + "; ".join(old[:5]) + ". If they "
                    "are not what the request is about, call done again; otherwise fix "
                    "the code."
                )
            if old:
                return (
                    "Not finished: your latest test run failed, but these tests also "
                    "fail on the original code: " + ", ".join(old[:8]) + ". If they "
                    "are not what the request is about, call done again; otherwise fix "
                    "the code."
                )
        if js and (check.get("verdict") or {}).get("load_same"):
            return (
                "Not finished: your latest test module failed to load, the same way "
                "on the original code, so it has not checked your change yet. Fix "
                "the module (or create the file it imports, if the request asks for "
                "it), run it again, then call done."
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
            or not check["command"].startswith(CHECK)
            or check.get("weakened")
            or any(
                not c["ok"] and not (js and superseded(c, check))
                for c in after_last_edit()
                if c.get("check", True)
            )
        ):
            return False
        for earlier in after_last_edit():
            if js and earlier.get("check", True) and not earlier["ok"]:
                earlier["superseded"] = True  # moot given `check` (see above)
        record = check.get("record") or {}
        code = check["command"][len(CHECK) :]
        if js:
            # At least one test passed, nothing failed or errored, and the
            # runner required every test to run an expect.
            if (
                not JS_ASSERTING.search(code)
                or not record.get("passed")
                or record.get("failed")
                or record.get("load_error")
                or not record.get("assertions_required")
            ):
                return False
        elif (
            not ASSERTING.search(code)
            or record.get("stop")
            or not any(ran for ran, _ in assert_counts(record).values())
        ):
            return False
        base = on_original(code)
        if base is None or base.ok or base.meta.get("timed_out"):
            return False
        if js:
            # A real failure there: a failing test or a module that does not
            # load (not "all skipped" or a run that wrote no report).
            before = base.meta.get("record") or {}
            if not (before.get("failed") or before.get("load_error")):
                return False
        return finish()

    def state_now():
        """JS: the state_key of the workspace as it is now."""
        return state_key(capture(), original_of)

    def insensitive(record, verdict, key):
        """JS: notes for failing tests that fail with the same message on
        the original code and on 2 or more distinct edited states: their
        assertion does not observe the edits."""
        notes = []
        if not verdict or not record:
            return ""
        said = {
            f.get("name"): without_scratch_number(f.get("message"))
            for f in record.get("failures") or []
        }
        for name in verdict.get("same") or []:
            if name in insensitive_flagged or name == LOAD_FAILURE:
                continue
            states = {key}
            for command in commands:
                if not command["command"].startswith(CHECK) or command.get("salvage"):
                    continue
                earlier = command.get("record") or {}
                if name not in ((command.get("verdict") or {}).get("same") or ()):
                    continue
                if any(
                    f.get("name") == name
                    and without_scratch_number(f.get("message")) == said.get(name)
                    for f in earlier.get("failures") or []
                ):
                    states.add(command.get("state"))
            states.discard(None)
            if len(states) >= 2:
                insensitive_flagged.add(name)
                notes.append(
                    f"- '{name}' fails with the same message on the original code and "
                    f"on {len(states)} different versions of your change: its "
                    "assertion does not observe your edits. Compare what it measures "
                    "with what the request asks for."
                )
        if notes:
            stats["insensitive_flags"] = stats.get("insensitive_flags", 0) + len(notes)
        return ("\n" + "\n".join(notes)) if notes else ""

    def test_call(text, requested, calls, patched=False):
        """JS: run a test module the agent sent (the test tool) and record it."""
        timeout = min(
            float(requested or bash_timeout),
            bash_timeout * 2,
            max(1.0, work_deadline - time.perf_counter()),
        )
        previous = next(
            (c for c in reversed(commands) if c["command"].startswith(CHECK)), None
        )
        if (
            previous is not None
            and previous["step"] > state["last_edit_step"]
            and previous["command"] == CHECK + text
        ):
            raise ToolError(
                "You already ran this exact check since your last edit"
                + (
                    " (it was re-run automatically after the edit)"
                    if previous.get("automatic")
                    else ""
                )
                + f": it {'passed' if previous['ok'] else 'failed'} on your current, "
                "edited code (the comparison with the original code was a separate "
                "run). Change the code or the check instead of re-running it."
            )
        report("Running check")
        if workspace.changed and JS_ASSERTING.search(text):
            # Its run on the original code starts now, alongside this one:
            # needed after a failure (the comparison) and after a pass
            # (auto_finish).
            start_original(
                text, min(bash_timeout, max(1.0, deadline - time.perf_counter() - 1))
            )
        started = time.perf_counter()
        result = run_check(text, timeout, typed=True)
        elapsed = time.perf_counter() - started
        record = result.meta.get("record")
        last_module.update(code=text, name=result.meta.get("scratch"))
        key = state_now()
        counts = not result.changed and bool(JS_ASSERTING.search(text))
        verdict, problem = None, False
        if (
            counts
            and not result.ok
            and record is not None
            and not result.meta.get("timed_out")
        ):
            if scratch_never_transforms(record):
                # A syntax error in the test module: a test bug, whatever the
                # code under test does (no run on the original code needed).
                problem = True
                result.text += (
                    "\n[the test module itself does not parse, so this says nothing "
                    "about your change: fix the marked line of the test]"
                )
            elif workspace.changed:
                # Which tests the change broke, fixed or never reached: one
                # run on the original code.
                base = on_original(text, timeout=max(5.0, 3 * elapsed))
                if base is not None and not base.meta.get("timed_out"):
                    # A module that fails to load identically there still
                    # counts (with its verdict): a `done` with calls left is
                    # refused once, and only the final finish excuses it.
                    verdict = compare_tests(record, base.meta.get("record"))
                    result.text += js_verdict_text(verdict)
                    result.text += insensitive(record, verdict, key)
        if not result.ok and record is not None and not patched:
            name = result.meta.get("scratch")
            if name:
                result.text += (
                    f"\n[to change this test module, edit {name} with only the lines "
                    "that need it (it is patched and run again; read shows it)]"
                )
        if result.changed:
            state["last_edit_step"] = step
            changed()
        commands.append(
            {
                "command": CHECK + text,
                "ok": result.ok,
                # A module that never parses is a broken check, not evidence.
                "check": counts and not problem,
                "turn": calls,
                "step": step,
                "output": result.text[-2000:],
                "record": record,
                "weakened": False,
                "timed_out": False,
                "verdict": verdict,
                "state": key,
                **({"check_problem": True} if problem else {}),
            }
        )
        return result

    def patch_module(arguments, calls):
        """JS: an edit of the last test module patches its source and runs it
        again (the module file itself is deleted after each run)."""
        edits = normalize_edits(arguments)
        label = last_module.get("name") or str(arguments.get("path"))
        code = apply_text_edits(last_module["code"], edits, label)
        stats["module_patches"] = stats.get("module_patches", 0) + 1
        result = test_call(code, arguments.get("timeout"), calls, patched=True)
        noun = "block" if len(edits) == 1 else "blocks"
        result.text = (
            f"Patched your last test module ({len(edits)} {noun}) and ran it again:\n"
            + result.text
        )
        return result

    def js_edit(arguments):
        """JS: edit, saying plainly that a refused multi-block edit changed
        nothing and which blocks matched."""
        if "path" not in arguments:
            raise ToolError(
                "edit needs path (the file to change) and edits; nothing was "
                "applied. Re-issue the call with both."
            )
        path = str(arguments["path"])
        edits = normalize_edits(arguments)
        try:
            name = workspace.name(path)
            current = workspace.resolve(name).read_bytes()
        except (OSError, ValueError):
            name, current = path, None
        sent = (name, json.dumps(edits), hashlib.sha256(current or b"").hexdigest())
        if current is not None and sent in rejected_edits:
            stats["resend_refusals"] = stats.get("resend_refusals", 0) + 1
            raise ToolError(
                "Rejected again: this is the same edit the syntax check already "
                f"refused ({rejected_edits[sent]}), and {name} has not changed since. "
                "Nothing was written. Change newText (or oldText) so the result parses."
            )
        try:
            result = workspace.edit(path, edits)
        except ToolError as exc:
            message = str(exc)
            repaired = (
                repair_boundaries(name, current, edits)
                if message.startswith("Rejected:") and current is not None
                else None
            )
            if repaired is None:
                if message.startswith("Rejected:") and current is not None:
                    rejected_edits[sent] = message.split("\n", 1)[0][
                        len("Rejected: ") :
                    ]
                raise ToolError(message + unapplied(path, edits)) from None
            fixed, notes = repaired
            result = workspace.edit(path, fixed)
            stats["boundary_repairs"] = stats.get("boundary_repairs", 0) + 1
            result.text = (
                "[As sent, this edit would not parse; "
                + "; ".join(notes)
                + ". The result parses: check the change below.]\n"
                + result.text
            )
        empty = [line for edit in edits for line in empty_bodies(edit["newText"])]
        if empty:
            stats["empty_body_flags"] = stats.get("empty_body_flags", 0) + 1
            result.text += (
                "\n[note: "
                + "; ".join(f"`{line[:80]}`" for line in empty[:3])
                + " has an empty body, so it controls nothing: the code after it "
                "always runs]"
            )
        return result

    def repair_boundaries(name, raw, edits):
        """JS: an edit the syntax check refused, with oldText boundaries
        repaired (see boundary_candidates): (edits, notes) when exactly one
        combination of repaired blocks parses, else None. All blocks still
        apply together or not at all."""
        gate_errors = getattr(workspace.content_gate, "errors", None)
        if gate_errors is None:
            return None
        text, _ = _decode(raw, name)
        options = []
        for edit in edits:
            choices = [(edit, None)]
            for extended, reason in boundary_candidates(
                text, edit["oldText"], edit["newText"]
            ):
                choices.append(
                    ({"oldText": extended, "newText": edit["newText"]}, reason)
                )
            options.append(choices)
        if all(len(choices) == 1 for choices in options):
            return None
        parsing = {}
        for index, choices in enumerate(options):
            for edit, reason in choices[1:]:
                trial = [options[i][0][0] for i in range(len(edits))]
                trial[index] = edit
                try:
                    updated = apply_text_edits(text, trial, name)
                except ToolError:
                    continue
                if gate_errors(name, updated) == []:
                    parsing.setdefault(updated, (trial, [f"edits[{index}]: {reason}"]))
        if len(parsing) != 1:
            return None
        return next(iter(parsing.values()))

    def unapplied(path, edits):
        if len(edits) < 2:
            return ""
        try:
            name = workspace.name(path)
            text, _ = _decode(workspace.resolve(name).read_bytes(), name)
        except (OSError, ValueError):
            return ""
        matched = []
        for index, edit in enumerate(edits):
            try:
                apply_text_edits(text, [edit], name)
            except ToolError:
                continue
            matched.append(index)
        note = (
            "\nNo edit in this call was applied (the edits of one call apply all "
            f"or nothing): {name} is unchanged."
        )
        if matched and len(matched) < len(edits):
            note += (
                " "
                + ", ".join(f"edits[{index}]" for index in matched)
                + " matched and can be resent unchanged; resend the whole call with "
                "the others corrected."
            )
        return note

    linked = (
        [
            (name, (workspace.root / name).resolve())
            for name in toolchain.dependencies
            if (workspace.root / name).is_dir()
        ]
        if js
        else []
    )

    def dependencies_before(command):
        """JS: the dependency manifest before a command that mentions
        node_modules or a package manager (None: not checked)."""
        if not linked or not manifest["enabled"]:
            return None
        if not mentions_dependencies(command, dependencies):
            return None
        found = dependency_manifest(linked)
        if found is None:
            manifest["enabled"] = False  # too large to list quickly
        return found

    def dependencies_after(before, result):
        after = dependency_manifest(linked)
        if after is None:
            return
        found = manifest_changes(before, after)
        if found:
            result.text += changes_text(found)
            listed = state.setdefault("dependencies_changed", [])
            listed.extend(
                f"{kind} {name}" for kind, name in found[: max(0, 50 - len(listed))]
            )

    def known_path(path):
        """JS: a path a test command names is a file of the repository or
        one the agent wrote (not a scratch file elsewhere)."""
        root = workspace.root
        if GLOB.search(path) and not path.startswith("/"):
            return True
        target = Path(path) if path.startswith("/") else root / path
        try:
            relative = target.resolve().relative_to(root.resolve()).as_posix()
        except (OSError, ValueError):
            return False
        if relative in workspace.changed or target.is_dir():
            return True
        if originals is None:
            return target.exists()
        return original_of(relative) is not None

    def rerun_after_edit(calls):
        """JS: after a response of only edits, re-run the latest test check if
        it failed, and show the result with that response (no model call)."""
        nonlocal step
        latest = next(
            (c for c in reversed(commands) if c["command"].startswith(CHECK)), None
        )
        if (
            latest is None
            or latest["ok"]
            or not latest.get("check", True)
            # it never finished (or wrote no report): re-running costs as much
            or latest.get("record") is None
            or latest["step"] > state["last_edit_step"]
            or model_stop - time.perf_counter() < 10.0
        ):
            return
        code = latest["command"][len(CHECK) :]
        report("Re-running the latest check")
        result = run_check(
            code, min(bash_timeout, max(1.0, work_deadline - time.perf_counter()))
        )
        record = result.meta.get("record")
        step += 1
        text, verdict = result.text, None
        if not result.ok and record is not None and not result.meta.get("timed_out"):
            base = on_original(code)
            if known(base):
                verdict = compare_tests(record, base.meta.get("record"))
                text += js_verdict_text(verdict)
        messages[-1]["content"] += (
            "\n\n[Your latest test module, re-run automatically on the edited code:]\n"
            + text
        )
        commands.append(
            {
                "command": CHECK + code,
                "ok": result.ok,
                "check": not result.changed,
                "turn": calls,
                "step": step,
                "output": text[-2000:],
                "record": record,
                "weakened": False,
                "timed_out": False,
                "automatic": True,
                "verdict": verdict,
                "message": len(messages) - 1,
                "state": state_now(),
            }
        )
        if result.changed:
            state["last_edit_step"] = step
            changed()

    def final_finish(stage, failed_edit):
        """No model call is left (the turn limit; in JS mode also the
        deadline): deliver the change if nothing speaks against it. In JS
        mode an earlier edit snapshot is delivered when the final state fails."""
        if not js:
            return judge_final(stage, failed_edit)
        started = time.perf_counter()
        result = judge_final(stage, failed_edit)
        result["final_finish_seconds"] = round(time.perf_counter() - started, 3)
        result["stop_reason"] = stage
        return result

    def judge_final(stage, failed_edit):
        failed = check_failed()
        let_through = False  # the latest check failed, but was excused
        # JS: the policy before the recorded tier and the named-error guard
        # would have excused the failing checks and judged the final state
        # (shadow attribution only; never changes what is delivered).
        old_excused = False
        if failed:
            failing = [
                c for c in after_last_edit() if c.get("check", True) and not c["ok"]
            ]
            if js:
                old_excused = all(excused_before_named(c) for c in failing)
            failed = not all(excused(c) for c in failing)
            let_through = not failed
        old_judged_final = (
            js and failed and old_excused and workspace.changed and not failed_edit
        )
        problem = None
        judged = False
        # JS, recorded tier: the final state is a candidate unless its last
        # response had a failed edit; when finish() judges it below, its
        # result counts, otherwise its recorded guard verdict alone (waiting
        # for the guard here would take the time salvage needs).
        final_problem = "not judged" if failed_edit else None
        if workspace.changed and not failed_edit and not failed:
            # Judged first, while the guard's results for this state are
            # complete (salvage restarts them for other states).
            problem = finish(final=True)
            judged = True
            if problem is None and not (js and let_through):
                return outcome("applied_unvalidated", finished=stage)
        if js and workspace.changed:
            # JS: the final state fails, or passes with only excused failures
            # (no evidence for it): an earlier state on which the agent's own
            # check passes (and fails on the original code) is better.
            rescued = salvage(stage)
            if rescued is not None:
                return rescued
        if judged and problem is None:
            return outcome("applied_unvalidated", finished=stage)
        error = (
            problem[:500]
            if problem is not None
            else f"No verified change within {max_turns} model calls"
            if stage == "turn_limit"
            else "No verified change before the deadline"
        )
        if js and workspace.changed:
            # JS: the earlier tiers deliver nothing. A state with a clean,
            # complete recorded guard verdict whose own checks say nothing
            # against it is delivered (unvalidated), preferring one with
            # positive evidence.
            tiered = recorded_tier(
                stage,
                problem if judged else final_problem,
                old_shadow(stage) if old_judged_final else None,
            )
            if tiered is not None:
                return tiered
        if old_judged_final:
            # The named-error guard kept a failure the old policy excused:
            # that policy would have judged (and maybe delivered) the final
            # state, so this failure may be a lost delivery.
            return outcome(
                "failed", failure_stage=stage, error=error, shadow=old_shadow(stage)
            )
        return outcome("failed", failure_stage=stage, error=error)

    def old_shadow(stage):
        """JS: what the old policy did where the named-error guard blocked an
        excuse: it judged the final state with finish(final=True) and
        delivered it when that found nothing. Not re-run here: the final
        state's recorded guard verdict is the estimate."""
        verdict_of = getattr(guard, "verdict", None) if guard is not None else None
        key = state_key(capture(), original_of) if originals is not None else None
        found = verdict_of(key) if verdict_of is not None and key else None
        return {
            "old_policy": "final_state",
            "failure_stage": stage,
            "state": key,
            "final_guard": (
                None
                if not found
                else "clean"
                if found["clean"]
                else "incomplete"
                if not found["complete"]
                else "problems"
            ),
        }

    def edits_source(files):
        """JS: a non-test JS/TS source file differs from the original."""
        return any(
            not is_scratch(name)
            and not is_test_path(name)
            and PurePosixPath(name).suffix in JS_SUFFIXES
            and data != original_of(name)
            for name, data in files.items()
        )

    def against(command):
        """JS: a check that ran on a state speaks against it: a failing test
        check whose comparison is unknown, that broke a test passing on the
        original code, crashed inside repository source, fails to load only
        with the change, or fails with an error the request names; or any
        other failing check that counts."""
        if command["ok"] or command.get("timed_out"):
            return False
        if not command["command"].startswith(CHECK):
            return bool(command.get("check")) and not command["ok"]
        record, verdict = command.get("record"), command.get("verdict")
        if record is None:
            return bool(command.get("check"))
        if scratch_never_transforms(record):
            return False  # the module itself does not parse: a test bug
        if verdict is None:
            return True
        if verdict.get("broke") or any(
            f.get("source") for f in record.get("failures") or []
        ):
            return True
        if record.get("load_error") and not verdict.get("load_same"):
            return True
        return bool(named_failures(record, request))

    def evidence(command):
        """JS: a check on a state shows the change working: a passing test
        check that fails on the original code, a test the change fixed, or
        a test that now fails at a later expect line than on the original
        code (it progressed)."""
        if not command["command"].startswith(CHECK):
            return False
        record = command.get("record") or {}
        base = original_runs.get(command["command"][len(CHECK) :])
        if command["ok"]:
            before = (base.meta.get("record") if base is not None else None) or {}
            return bool(
                record.get("passed")
                and base is not None
                and not base.ok
                and (before.get("failed") or before.get("load_error"))
            )
        verdict = command.get("verdict") or {}
        if verdict.get("fixed"):
            return True
        before = {
            f.get("name"): f.get("line")
            for f in ((base.meta.get("record") if base else None) or {}).get("failures")
            or []
        }
        return any(
            isinstance(f.get("line"), int)
            and isinstance(before.get(f.get("name")), int)
            and f["line"] > before[f["name"]]
            for f in record.get("failures") or []
        )

    def recorded_tier(stage, final_problem, shadow=None):
        """JS: the final state or an earlier snapshot (newest first) whose
        recorded guard verdict is complete and clean, whose checks say
        nothing against it and that changes a source file, once the agent's
        own test module has loaded and run at all; one with positive
        evidence first. Nothing is re-run; None when there is none."""
        verdict_of = getattr(guard, "verdict", None) if guard is not None else None
        if verdict_of is None or originals is None:
            return None
        final = capture()
        candidates, seen = [], set()
        for files, turn in [(final, None)] + [
            (snapshot["files"], snapshot["turn"]) for snapshot in reversed(snapshots)
        ]:
            key = state_key(files, original_of)
            if key not in seen:
                seen.add(key)
                candidates.append((key, files, turn))
        # Why each candidate was passed over (instrumentation only).
        skipped = stats.setdefault("tier_candidates", [])
        if not any(
            c["command"].startswith(CHECK)
            and not c.get("salvage")
            and c.get("record") is not None
            and not c["record"].get("load_error")
            for c in commands
        ):
            # The agent's own test module never loaded and ran: nothing it
            # wrote exercised the change (the tier is for code whose test
            # cannot judge it, not for code no test reached).
            skipped.append(["all", "no test module of the agent ever ran"])
            return None
        chosen = {}
        for key, files, turn in candidates:
            label = "final" if turn is None else turn
            if not edits_source(files):
                skipped.append([label, "no source change"])
                continue
            if turn is None and final_problem is not None:
                skipped.append([label, "final finish problem"])
                continue  # the final state's own finish found a problem
            found = verdict_of(key)
            if not found or not found["clean"]:
                reason = (
                    "no guard verdict"
                    if not found
                    else "guard incomplete"
                    if not found["complete"]
                    else "guard found problems"
                )
                skipped.append([label, reason])
                continue
            ran = [
                c for c in commands if c.get("state") == key and not c.get("salvage")
            ]
            if any(against(c) for c in ran):
                skipped.append([label, "a check speaks against it"])
                continue
            # Salvage re-ran the agent's check on this very state: a strong
            # signal there (a crash in source, a break) counts too.
            if any(
                salvage_against(c)
                for c in commands
                if c.get("salvage") is not None and c.get("state") == key
            ):
                skipped.append([label, "a salvage run speaks against it"])
                continue
            tier = "progress" if any(evidence(c) for c in ran) else "guard_clean"
            chosen.setdefault(tier, (files, turn))
            if tier == "progress":
                break
        pick = next((t for t in ("progress", "guard_clean") if t in chosen), None)
        if pick is None:
            return None
        files, turn = chosen[pick]
        if turn is not None:
            restore(files)
            state["last_edit_step"] = step
            changed()
        if required_checks:
            failures = [
                r
                for r in (check_runner or (lambda *a: []))(
                    workspace.root, list(required_checks), deadline
                )
                if r.get("returncode") != 0
            ]
            if failures:
                if turn is not None:
                    restore(final)
                    changed()
                return None
        return outcome(
            "applied_unvalidated",
            finished=pick,
            snapshot_turn=turn if turn is not None else state["calls"],
            final_stage=stage,
            # What the policy without this tier delivered: nothing, unless
            # the named-error guard blocked its excuse of the final state.
            shadow=shadow or {"old_policy": "failed", "failure_stage": stage},
        )

    def salvage_against(command):
        """JS: a salvage run of the agent's check on a state (no comparison
        verdict of its own) speaks against that state: it crashed inside
        repository source, broke a test that passes on the original code,
        fails to load where the original loads, or fails with an error the
        request names. A missing verdict alone does not count."""
        if (
            command["ok"]
            or command.get("timed_out")
            or not command["command"].startswith(CHECK)
        ):
            return False
        record = command.get("record")
        if record is None or scratch_never_transforms(record):
            return False
        if any(f.get("source") for f in record.get("failures") or []):
            return True
        if named_failures(record, request):
            return True
        base = original_runs.get(command["command"][len(CHECK) :])
        before = base.meta.get("record") if base is not None else None
        if before is None:
            return False
        if record.get("load_error") and not before.get("load_error"):
            return True
        return bool((compare_tests(record, before) or {}).get("broke"))

    original_bytes = {}

    def original_of(name):
        if name not in original_bytes:
            try:
                original_bytes[name] = originals(name)
            except (OSError, ValueError):
                original_bytes[name] = None
        return original_bytes[name]

    def capture():
        """Bytes of every changed file now (None: absent)."""
        files = {}
        for name in workspace.changed:
            path = workspace.root / name
            try:
                files[name] = path.read_bytes() if path.is_file() else None
            except OSError:
                files[name] = None
        return files

    def same_files(first, second):
        return all(
            first.get(name, original_of(name)) == second.get(name, original_of(name))
            for name in set(first) | set(second)
        )

    def restore(files):
        """Put the changed files back to `files` (others to the original)."""
        for name in list(workspace.changed):
            data = files[name] if name in files else original_of(name)
            path = workspace.root / name
            if data is None:
                path.unlink(missing_ok=True)
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)

    def guard_settled():
        """JS: the guard has finished every run for the current state (its
        verdict is known, not merely not ready)."""
        peek = getattr(guard, "peek", None) if guard is not None else None
        if peek is None:
            return True
        found = peek(0.0)
        return found is None or not found["pending"]

    def salvage(stage):
        """JS: the final state fails (or has only excused failures). Deliver
        the newest earlier edit snapshot on which the agent's latest test
        check that fails on the original code passes, and that the guard
        finds clean (every guard run finished); None (the final state is
        restored, already judged) when there is none. Every record made
        while a rejected snapshot is judged is marked and never counts."""
        nonlocal step
        if originals is None or not snapshots:
            return None
        recent = [
            c
            for c in reversed(commands)
            if c["command"].startswith(CHECK)
            and c.get("record") is not None
            and not c.get("timed_out")
            and not c.get("salvage")
            # a module that never loads cannot pass on any version
            and not scratch_never_transforms(c["record"])
            and not (c.get("verdict") or {}).get("load_same")
        ][:2]
        check = next((c for c in recent if fails_on_original(c)), None)
        if check is None:
            return None
        code = check["command"][len(CHECK) :]
        final = capture()
        seen, candidates = [final], []
        for snapshot in reversed(snapshots):
            if any(same_files(snapshot["files"], other) for other in seen):
                continue
            seen.append(snapshot["files"])
            candidates.append(snapshot)
            if len(candidates) >= JS_SALVAGE_CANDIDATES:
                break
        if not candidates:
            return None
        regressions, edited = state["regressions"], state["last_edit_step"]
        report("Trying earlier versions of the change")
        for snapshot in candidates:
            if deadline - time.perf_counter() < 6.0:
                break
            start = len(commands)
            snapshot_key = state_key(snapshot["files"], original_of)
            restore(snapshot["files"])
            step += 1
            state["last_edit_step"] = step
            changed()
            timeout = min(bash_timeout, max(1.0, deadline - time.perf_counter() - 4.0))
            result = run_check(code, timeout)
            record = result.meta.get("record") or {}
            step += 1
            commands.append(
                {
                    "command": CHECK + code,
                    "ok": result.ok,
                    "check": True,
                    "turn": state["calls"],
                    "step": step,
                    "output": result.text[-2000:],
                    "record": result.meta.get("record"),
                    "automatic": True,
                    "salvage": snapshot["turn"],
                    "state": snapshot_key,
                }
            )
            accepted = False
            if (
                result.ok
                and record.get("passed")
                and not record.get("failed")
                and not record.get("load_error")
            ):
                salvaging["code"] = code  # recheck: just ran on this state
                try:
                    accepted = finish(final=True) is None and guard_settled()
                finally:
                    salvaging.clear()
            for command in commands[start:]:
                # Runs on this snapshot (recheck's too) judge another state.
                command.setdefault("salvage", snapshot["turn"])
                command.setdefault("state", snapshot_key)
                if not accepted:
                    command["check"] = False
            if accepted:
                return outcome(
                    "applied_unvalidated",
                    finished="earlier_snapshot",
                    snapshot_turn=snapshot["turn"],
                    final_stage=stage,
                )
        # Back to the final state, judged before the salvage.
        restore(final)
        state["last_edit_step"] = edited
        state["regressions"] = regressions
        changed()
        return None

    def guard_note(wait=0.0, target=None):
        """JS: the background type check and tests of the latest edit, each
        part once, as soon as it is ready (waits up to `wait` seconds).
        `target`: index of the message it goes into."""
        if not js or guard is None or not guard_due["edited"]:
            return ""
        peek = getattr(guard, "peek", None)
        # Waiting is for the type check (quick, and what edits break most);
        # test results come with a later tool result when they take longer.
        found = peek(wait, ("tsc",)) if peek is not None else None
        if found is None:
            guard_due["edited"] = False
            return ""
        generation, parts = found["generation"], []
        if "tsc" not in found["pending"] and (generation, "tsc") not in guard_shown:
            guard_shown.add((generation, "tsc"))
            errors = found["type_errors"]
            if errors:
                lines, traced, _ = type_error_lines(found, False)
                note_errors.update(
                    message=len(messages) if target is None else target,
                    errors=set(lines[:6]),
                )
                parts.append(
                    f"- tsc --noEmit: {len(errors)} new error(s) vs the original "
                    "code:\n  "
                    + "\n  ".join(lines[:6])
                    + (f"\n  [{len(lines) - 6} more]" if len(lines) > 6 else "")
                    + (f"\n{traced}" if traced else "")
                )
            elif errors == []:
                parts.append("- tsc --noEmit: no new errors")
        if "tests" not in found["pending"] and (generation, "tests") not in guard_shown:
            guard_shown.add((generation, "tests"))
            new, added = found["regressions"], found["added"]
            for label, names in (
                (BROKE_TESTS, new),
                ("tests you added fail", added),
            ):
                if names:
                    shown = []
                    for name in names[:3]:
                        message = (found["messages"].get(name) or "").strip()
                        first = message.split("\n")[0][:200] if message else ""
                        shown.append(
                            f"  FAIL {name}" + (f"\n    {first}" if first else "")
                        )
                    parts.append(
                        f"- {label} ({len(names)}):\n"
                        + "\n".join(shown)
                        + (f"\n  [{len(names) - 3} more]" if len(names) > 3 else "")
                    )
            if new == [] and added == []:
                parts.append("- existing tests: no new failures")
        if not found["pending"]:
            guard_due["edited"] = False
        if not parts:
            return ""
        return "\n[Background checks of your edited code:\n" + "\n".join(parts) + "]"

    def guard_wait():
        """JS: how long to hold the next model call for the type check of
        the latest edit: only when it is expected within JS_GUARD_SOON
        seconds (its results otherwise come with a later tool result)."""
        eta = getattr(guard, "eta", None) if guard is not None else None
        if eta is None:
            return JS_GUARD_WAIT  # a guard without timing estimates
        expected = eta(("tsc",))
        if expected is None or expected > JS_GUARD_SOON:
            return 0.0
        return expected + JS_GUARD_MARGIN

    def budget_note():
        """JS: the remaining model calls and time, when they run short."""
        notes = []
        if max_turns - state["calls"] <= 3:
            notes.append(f"model call {state['calls'] + 1} of {max_turns} comes next")
        seconds = model_stop - time.perf_counter()
        if seconds < 30:
            notes.append(f"about {max(0, int(seconds))} s are left")
        if not notes:
            return ""
        return (
            "\n[" + "; ".join(notes) + ": finish now, with your remaining edits, "
            "the test and done in one response]"
        )

    last_batch_failed_edit = False
    while True:
        checkpoint()
        if state["calls"] >= max_turns:
            return final_finish("turn_limit", last_batch_failed_edit)
        if time.perf_counter() >= deadline:
            return outcome(
                "failed", failure_stage="deadline", error="Editor deadline exhausted"
            )
        if js and time.perf_counter() >= model_stop:
            return final_finish("deadline", last_batch_failed_edit)
        if js and state["calls"] > 0:
            wait = 0.0
            if guard_due["edited"] and guard_due.get("turn") == state["calls"]:
                wait = max(0.0, min(guard_wait(), model_stop - time.perf_counter()))
            waited = time.perf_counter()
            notes = guard_note(wait, len(messages) - 1) + budget_note()
            if wait:
                stats["guard_waits"] = stats.get("guard_waits", 0) + 1
                stats["guard_wait_seconds"] = round(
                    stats.get("guard_wait_seconds", 0.0) + time.perf_counter() - waited,
                    3,
                )
            if notes:
                messages[-1]["content"] += notes
        report(f"Model call {state['calls'] + 1}")
        if js:
            try:
                reply = chat(messages, tools, call_deadline)
            except InterruptedError:
                raise
            except Exception:
                if time.perf_counter() < call_deadline - 1.0:
                    raise
                # The call ran into the time reserved for the final finish.
                return final_finish("deadline", last_batch_failed_edit)
        else:
            reply = chat(messages, tools, deadline)
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
            if problem and js:
                # JS: no model call is left; judge as at the turn limit
                # (excused checks, an earlier snapshot).
                return final_finish("turn_limit", last_batch_failed_edit)
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
        edited_before = state["last_edit_step"]
        ran = []  # the tools this response ran (JS: an edit of a test module is "test")
        for call in assistant["tool_calls"]:
            name = call["function"]["name"]
            step += 1
            result: ToolResult
            # JS mode: a failing type check with only errors the original code
            # has too says nothing against the change.
            harmless = False
            effective = name
            if blocked:
                result = ToolResult(
                    "Skipped: an earlier edit in this response failed.", ok=False
                )
            else:
                try:
                    checkpoint()
                    try:
                        arguments = (js_arguments if js else repair_json)(
                            call["function"]["arguments"]
                        )
                    except ValueError as exc:
                        raise ToolError(
                            f"Could not parse {name} arguments as JSON ({exc}). "
                            "Re-issue the call with valid JSON."
                        ) from None
                    if not isinstance(arguments, dict):
                        raise ToolError("Tool arguments must be a JSON object")
                    if (
                        js
                        and name == "read"
                        and is_scratch(str(arguments.get("path", "")))
                        and last_module.get("code")
                    ):
                        # The last test module (deleted after its run), with
                        # line numbers, to patch it with edit.
                        stats["scratch_reads"] = stats.get("scratch_reads", 0) + 1
                        shown = last_module["code"].rstrip("\n").split("\n")
                        width = len(str(len(shown)))
                        result = ToolResult(
                            f"[your last test module ({last_module.get('name')}), "
                            f"{len(shown)} lines; edit this path to patch it and run "
                            "it again]\n"
                            + "\n".join(
                                f"{number:>{width}} | {line}"
                                for number, line in enumerate(shown, 1)
                            )
                        )
                    elif (
                        js
                        and name in ("read", "edit", "write")
                        and is_scratch(str(arguments.get("path", "")))
                    ):
                        if name != "edit" or not last_module.get("code"):
                            raise ToolError(
                                f"{arguments.get('path')} was a temporary test module: "
                                "it is deleted after each run. "
                                + (
                                    "Change your last test module with edit (it is "
                                    "patched and run again), or call test again "
                                    "with the complete corrected module."
                                    if last_module.get("code")
                                    else "Call test again with the complete "
                                    "corrected module."
                                )
                            )
                        effective = "test"
                        result = patch_module(arguments, calls)
                    elif name == "read":
                        result = workspace.read(
                            str(arguments["path"]),
                            arguments.get("offset"),
                            arguments.get("limit"),
                        )
                    elif name == "edit":
                        if js:
                            result = js_edit(arguments)
                        else:
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
                    elif name == "test" and "test" in tool_names:
                        text = arguments.get("code")
                        if not isinstance(text, str):
                            raise ToolError("test needs string code")
                        result = test_call(text, arguments.get("timeout"), calls)
                    elif name == "bash" or (name == "python" and not js):
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
                            max(1.0, work_deadline - time.perf_counter()),
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
                            installed = None
                            if js:
                                # node_modules is a shared install: refuse
                                # commands that would write there.
                                reason = dependency_write(text, dependencies)
                                if reason:
                                    raise ToolError(refusal_text(reason))
                                installed = dependencies_before(text)
                            result = workspace.bash(text, timeout, checkpoint)
                            if installed is not None:
                                dependencies_after(installed, result)
                            kind = bash_check_kind(text, js, test_scripts)
                            # JS: a test run whose status is another command's,
                            # or that runs only files outside the repository's
                            # tests and the agent's files, is no check.
                            aside = (
                                js
                                and kind == "tests"
                                and not js_run_counts(text, known_path, test_scripts)
                            )
                            if kind == "tests" and result.meta.get("timed_out"):
                                timed_out = not aside
                                result.text += (
                                    "\n[the test command did not finish, so it says "
                                    "nothing about your change: run a narrower command "
                                    + (
                                        "(one test file, or -t with a test name)]"
                                        if js
                                        else "(one test file, -x or -k)]"
                                    )
                                )
                            counts = (
                                not result.changed
                                and not aside
                                and (
                                    kind is not None
                                    or (
                                        not result.ok
                                        and bool(
                                            CHECK_COMMAND.search(text)
                                            or (js and JS_CHECK_COMMAND.search(text))
                                        )
                                    )
                                )
                            )
                            if aside:
                                result.text += (
                                    "\n[not counted as a check of your change: the "
                                    "exit status is another command's, or it runs "
                                    "files that are neither the repository's tests "
                                    "nor files you wrote; use the test tool]"
                                )
                        type_errors = None
                        if (
                            js
                            and name == "bash"
                            and counts
                            and not result.ok
                            and not result.meta.get("timed_out")
                            and is_type_check(text, toolchain.type_scripts)
                            and not result.text.startswith("[output truncated")
                        ):
                            # The agent's own type check: compare its errors
                            # with the original code's (pristine baseline).
                            type_errors = parse_tsc(result.text, workspace.root)
                            new = beyond_original(
                                type_errors,
                                max(
                                    0.0, min(10.0, deadline - time.perf_counter() - 10)
                                ),
                            )
                            if new == []:
                                counts = False  # not evidence against the change
                                harmless = True
                                result.text += (
                                    "\n[this type error also occurs on the original "
                                    "code: your change did not cause it]"
                                    if len(type_errors) == 1
                                    else f"\n[all {len(type_errors)} type errors also "
                                    "occur on the original code: your change did not "
                                    "cause them]"
                                )
                            elif new:
                                result.text += (
                                    f"\n[{len(type_errors) - len(new)} of these errors "
                                    "also occur on the original code; new with your "
                                    "change:\n"
                                    + "\n".join(tsc_text(e) for e in new[:10])
                                    + "]"
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
                        if js and name == "bash" and not result.ok and counts:
                            # Failing test ids, for "also fails on the original".
                            commands[-1]["failing"] = failing_from_output(result.text)
                            if type_errors:
                                commands[-1]["type_errors"] = type_errors
                    elif name == "done":
                        done_summary = (
                            str(arguments.get("summary", "")).strip() or "done"
                        )
                        result = ToolResult("ok")
                    elif js:
                        raise ToolError(
                            f"Unknown tool {name!r}. Available: "
                            + ", ".join(tool_names)
                            + "."
                        )
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
                if not result.ok and not harmless:
                    batch_failed = True
                    if effective in MUTATING:
                        blocked = True
                        last_batch_failed_edit = True
            ran.append((effective, result.ok))
            if js and effective != "done":
                # Background type check and tests of an earlier edit, if ready.
                result.text += guard_note()
            messages.append(
                {"role": "tool", "tool_call_id": call["id"], "content": result.text}
            )
            if (
                effective in ("python", "bash", "test")
                and commands
                and commands[-1]["step"] == step
            ):
                commands[-1]["message"] = len(messages) - 1
                if js and "state" not in commands[-1]:
                    commands[-1]["state"] = state_now()
            if name == "done":
                done_index = len(messages) - 1
        trace()
        if js and state["last_edit_step"] > edited_before and workspace.changed:
            # The state this response left, for a fallback at the end.
            files = capture()
            if not snapshots or not same_files(snapshots[-1]["files"], files):
                snapshots.append({"turn": calls, "files": files})
        if done_summary is None or done_index is None:
            if batch_failed or not workspace.changed:
                continue
            if js and ran and all(kind in MUTATING for kind, _ in ran):
                # Only edits: show the latest failing check on the new code.
                rerun_after_edit(calls)
            # A passing check that fails on the original code proves the
            # change: finish now instead of spending a model call on done.
            last = commands[-1] if commands else None
            appending["last"] = js  # its feedback goes into the newest message
            try:
                problem = auto_finish(
                    last
                    if last is not None
                    and last["turn"] == calls
                    and last["step"] == step
                    else None
                )
            finally:
                appending["last"] = False
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
        if problem and js:
            # JS: no model call is left; judge as at the turn limit (excused
            # checks, an earlier snapshot).
            return final_finish("turn_limit", last_batch_failed_edit)
        if problem:
            return outcome("failed", failure_stage="verification", error=problem[:500])
        return outcome("applied_unvalidated", summary=done_summary[:500])
