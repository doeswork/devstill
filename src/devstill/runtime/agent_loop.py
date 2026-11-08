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
from devstill.runtime.tools import ToolError, ToolResult, Workspace

SYSTEM = """You are Devstill Quick, a fast, careful coding agent working in a copy of the user's repository. Make the smallest correct change that fully satisfies the request.

How to work:
- The request comes with source excerpts retrieved from the repository. They are the current file contents: edit from them directly. Read or search only for something you need that is not shown.
- Change files with edit (or write for a new file). oldText must match the current file exactly and uniquely: copy it from the shown source, keep it short (the lines you change plus enough context to be unique) and never retype unchanged code.
- Check your change with the python tool: a short script (2-6 asserts) that calls the changed function or method directly with minimal setup and asserts the requested behavior plus each behavior the request explicitly says to keep. It runs in the repository root with the project interpreter (src/ is importable); a failing assert prints both sides. Only test what the request mentions.
- Send several tool calls in one response whenever you can; they run in order. The fastest finish is a single response with three calls: edit(...), python(code with asserts), done(summary). If the check fails, done is ignored and you see the failure.
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
            "description": "Run a shell command in the repository root (e.g. grep -rn, python -m pytest -q path). Returns output and exit code. Files created by commands are not part of the change; create files with write.",
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
            "description": "Replace one exact, unique piece of text in a file. For several changes, call edit several times (they apply in order).",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "oldText": {
                        "type": "string",
                        "description": "Exact current text to replace: short, unique, copied from the file",
                    },
                    "newText": {"type": "string", "description": "Replacement text"},
                },
                "required": ["path", "oldText", "newText"],
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
                            "oldText": "    return int(num) / int(den)",
                            "newText": "    if int(den) == 0:\n        return None\n"
                            "    return int(num) / int(den)",
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
    advice = (
        f" If {right} is what the request requires or keeps, the code is wrong: "
        "restore the assertion and fix the code."
    )
    rights = found.get(left)
    if rights is None:
        return (
            f"\nWarning: your previous check asserted {shown}, which failed (actual "
            f"value {previous['left']}); this check no longer asserts it." + advice
        )
    if previous.get("op") in {"Eq", "Is"} and any(
        source != right and _literal_repr(source) == previous["left"]
        for source in rights
    ):
        return (
            f"\nWarning: your previous check asserted {shown}, which failed (actual "
            f"value {previous['left']}); this check now expects that actual value."
            + advice
        )
    return ""


def _call_name(call):
    return (call.get("function") or {}).get("name", "")


CHECK_COMMAND = re.compile(r"\b(pytest|python3?|unittest|tox|nox|make\s+test|npm\s+test)\b")
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
        checks = [
            c
            for c in commands
            if c.get("check", True) and c["step"] > state["last_edit_step"]
        ]
        write(
            output / "agent-trace.json",
            {"messages": state["messages"], "commands": commands},
        )
        return {
            "status": status,
            "engine": "loop",
            "model_calls": state["calls"],
            "changed_files": sorted(set(workspace.changed)),
            "commands": commands,
            # The agent's latest check after its last edit passed, and the
            # regression guard reported nothing outstanding.
            "verified": bool(checks) and checks[-1]["ok"] and not state["regressions"],
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
):
    messages = state["messages"]
    commands = state["commands"]
    nudged = False
    step = 0
    failed_assert = None  # the latest failing check comparison, if any
    guard_reports = 0
    failed_check_warned = None  # last_edit_step when we last refused a finish

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

    def finish(final=False):
        """None when the change may be accepted; otherwise feedback for the model."""
        nonlocal guard_reports
        before_tools()
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
            state["regressions"] = list(found[0]) if found else []
            if found and (guard_reports < 2 and not final):
                guard_reports += 1
                new, test_output = found
                diff = guard.diff()
                return (
                    "Existing tests that passed before your change now fail:\n"
                    + "\n".join(new[:10])
                    + "\n\nOutput (tail):\n"
                    + test_output[-2500:]
                    + (f"\n\nYour changes so far:\n```diff\n{diff}\n```" if diff else "")
                    + "\nFix the code so they pass again, unless the request explicitly "
                    "asks to change that behavior."
                )
            if found:
                return "Existing tests still fail: " + ", ".join(found[0][:10])
        return None

    def changed():
        if guard is not None:
            guard.changed()

    def refuse_finish():
        """Refuse one finish while the latest check after the last edit failed."""
        nonlocal failed_check_warned
        if check_failed() and failed_check_warned != state["last_edit_step"]:
            failed_check_warned = state["last_edit_step"]
            return (
                "Not finished: your latest check failed. Fix the code (or the check, "
                "only if it misstates the request) and run it again, then call done."
            )
        return None

    last_batch_failed_edit = False
    while True:
        checkpoint()
        if state["calls"] >= max_turns:
            if workspace.changed and not last_batch_failed_edit and not check_failed():
                problem = finish(final=True)
                if problem is None:
                    return outcome("applied_unvalidated", finished="turn_limit")
                return outcome("failed", failure_stage="turn_limit", error=problem[:500])
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
                        result = workspace.write(str(arguments["path"]), content, append)
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
                        if name == "python":
                            report("Running check")
                            note = weakening_note(failed_assert, text)
                            result = workspace.python(text, timeout, checkpoint)
                            failed_assert = result.meta.get("failed_assert")
                            if note:
                                result.text += note
                        else:
                            report("Running command")
                            result = workspace.bash(text, timeout, checkpoint)
                        if result.changed:
                            # The command edited files itself: that is an edit,
                            # not a check of the edited state.
                            state["last_edit_step"] = step
                            changed()
                        commands.append(
                            {
                                "command": ("python:\n" + text) if name == "python" else text,
                                "ok": result.ok,
                                "check": not result.changed
                                and (name == "python" or bool(CHECK_COMMAND.search(text))),
                                "turn": calls,
                                "step": step,
                                "output": result.text[-2000:],
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
            if name == "done":
                done_index = len(messages) - 1
        trace()
        if done_summary is None or done_index is None:
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
                problem if problem.startswith("Not finished") else "Not finished: " + problem
            )
            continue
        if problem:
            return outcome("failed", failure_stage="verification", error=problem[:500])
        return outcome("applied_unvalidated", summary=done_summary[:500])
