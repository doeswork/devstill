"""JS mode of the Quick loop: dependency protection, syntax gate, surfaced
guard results, evidence rules, feedback text, cheaper iterations, deadline
salvage and scratch test modules.

Uses the fake vitest/tsc of test_js_toolchain; tests marked `zustand` run the
real toolchain and skip when the prepared install is absent.
"""

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from devstill.runtime import agent_loop as loop_module
from devstill.runtime.agent_loop import (
    compare_tests,
    js_run_counts,
    js_verdict_text,
    uninformative,
)
from devstill.runtime.jsdeps import (
    DependencyPaths,
    dependency_manifest,
    dependency_write,
    manifest_changes,
)
from devstill.runtime.jssyntax import SyntaxGate, code_frame
from devstill.runtime.jstest import (
    JsGuard,
    describe_test_run,
    load_frame,
    repo_frames,
    run_test_module,
    scratch_type_errors,
)
from devstill.runtime.service import command_environment, process_deadline
from devstill.runtime.toolchain import (
    detect,
    has_jsx,
    link_dependencies,
    parse_vitest_config,
    plain_suffix,
    tsconfig_for,
    vitest_config,
)
from devstill.runtime.tools import ToolError, ToolResult, Workspace, render_hunks
from tests.test_js_toolchain import (
    EDIT,
    PROVES,
    ZUSTAND,
    call,
    copy_sources,
    js_loop,
    js_tools,
    make_jsrepo,
    node_path,
    originals_from,
    reply,
    scripted,
    tool_results,
    toolchain_for,
)


@pytest.fixture
def jsrepo(tmp_path):
    return make_jsrepo(tmp_path)


zustand = pytest.mark.skipif(
    not (ZUSTAND / "node_modules").is_dir()
    or shutil.which("node", path=node_path()) is None,
    reason="prepared zustand install or node not available",
)

# --- 1. linked dependencies are never modified -------------------------------------


@pytest.mark.parametrize(
    "command",
    [
        "rm -f node_modules/use-sync-external-store/cjs/x.js",
        "echo x > node_modules/a.js",
        "cat a | tee node_modules/b",
        "sed -i s/a/b/ node_modules/x.js",
        "x=$(rm -rf node_modules/a)",
        "cd node_modules/foo && rm x.js",
        "npm i",
        "npm install lodash",
        "pnpm add -D x",
        "yarn",
        "bun remove x",
        "npm ci",
        "perl -pi -e 's/a/b/' node_modules/z",
        "cp patched.js node_modules/x/y.js",
        "mv node_modules/x /tmp/",
        "find node_modules -name '*.map' -delete",
        "find node_modules -name x -exec rm {} \\;",
        "ls node_modules | xargs rm",
        "rm -rf node_mod*",
        "sudo rm -rf node_modules",
        "timeout 5 truncate -s0 node_modules/q",
        "ln -sf /tmp/x node_modules/y",
        "echo 1 2>> node_modules/log",
        # xargs with its own options, in-place editors behind it
        "grep -rl x node_modules/pkg | xargs sed -i 's/a/b/'",
        "find node_modules/x -name '*.js' | xargs perl -pi -e 's/a/b/'",
        "find node_modules/x -name '*.map' | xargs -n 1 rm",
        "find node_modules/x -name '*.map' | xargs -P 4 rm",
        "ls node_modules | xargs -I {} rm {}",
        # nested command lines
        "bash -c 'rm -f node_modules/x'",
        'sh -c "cd node_modules/x && rm a.js"',
        "eval rm -rf node_modules/x",
        # loops, brace expansion, command substitution
        "for f in node_modules/x/*.js; do rm $f; done",
        "rm -rf {node_modules,dist}",
        "rm $(find node_modules/x -name '*.map')",
        # package-manager options before the subcommand, package runners
        "pnpm --filter zustand add lodash",
        "pnpm -F x add y",
        "pnpm -C packages/a install",
        "pnpm --dir . install",
        "pnpm -w add foo",
        "npm -w a install",
        "npm --prefix . install",
        "yarn --cwd x add y",
        "yarn workspace a add b",
        "npx pnpm install",
        "bunx pnpm install",
        "corepack pnpm install",
        "nice -n 5 rm node_modules/x/package.json",
        "find -L . -name '*.map' -delete",
    ],
)
def test_commands_that_write_into_node_modules_are_recognized(tmp_path, command):
    (tmp_path / "node_modules").mkdir()
    assert dependency_write(command, DependencyPaths(tmp_path)) is not None


@pytest.mark.parametrize(
    "command",
    [
        "cat node_modules/zustand/package.json",
        "grep -rn foo node_modules/zustand",
        "ls node_modules/.bin",
        "npx vitest run tests/a.test.ts",
        "npx tsc --noEmit",
        "cp node_modules/x/y.js /tmp/y.js",
        "node_modules/.bin/vitest run 2>&1 | tail -20",
        "npm test",
        "pnpm test -- -t x",
        "echo node_modules > /tmp/x",
        "find node_modules -name x",
        "sed -n 1,20p node_modules/x.js",
        "npm ls react",
        "yarn run test",
        "rm tests/tmp.test.ts",
        "head -50 node_modules/a.js > /tmp/a",
        # find excluding node_modules (start paths are before the expression)
        "find . -name '*.orig' -not -path './node_modules/*' -delete",
        "find . -name '*.snap' -not -path '*/node_modules/*' -exec rm {} +",
        "find src -name '*.ts' -not -path '*/node_modules/*' -exec sed -i s/a/b/ {} +",
        "find . -type d -name node_modules -prune -o -name '*.orig' -exec rm {} +",
        "grep -rl foo . --exclude-dir=node_modules | xargs sed -i 's/foo/bar/'",
        # back out of node_modules, comments, help output
        "cd node_modules/x && cat package.json && cd ../.. && rm -rf dist",
        "rm -rf dist coverage # not node_modules",
        "npm install --help",
        "pnpm add --help",
        "npx vitest run tests/a.test.ts # node_modules/.bin",
    ],
)
def test_reading_and_running_dependencies_is_allowed(tmp_path, command):
    (tmp_path / "node_modules").mkdir()
    assert dependency_write(command, DependencyPaths(tmp_path)) is None


def test_resolved_dependency_paths_count_as_node_modules(jsrepo):
    paths = DependencyPaths(jsrepo)
    real = str((jsrepo / "node_modules").resolve())
    assert "node_modules" not in real.split("/")[-1:] or real in paths.resolved
    assert dependency_write(f"rm {real}/.bin/vitest", paths)


def test_manifest_sees_changes_and_skips_tool_caches(tmp_path):
    install = tmp_path / "node_modules"
    (install / "pkg").mkdir(parents=True)
    (install / "pkg" / "index.js").write_text("x")
    (install / ".bin").mkdir()
    before = dependency_manifest([("node_modules", install)])
    (install / ".vite").mkdir()
    (install / ".vite" / "results.json").write_text("{}")  # a cache: ignored
    (install / "pkg" / "index.js").unlink()
    (install / "pkg" / "new.js").write_text("y")
    after = dependency_manifest([("node_modules", install)])
    assert manifest_changes(before, after) == [
        ("removed", "node_modules/pkg/index.js"),
        ("added", "node_modules/pkg/new.js"),
    ]
    assert dependency_manifest([("node_modules", install)], limit=0) is None


def test_loop_refuses_writes_into_node_modules_and_reports_damage(jsrepo, tmp_path):
    install = (jsrepo / "node_modules").resolve()
    (install / "keep.js").write_text("installed\n")
    chat = scripted(
        reply(EDIT, call("bash", 1, command="rm -f node_modules/keep.js")),
        reply(
            call(
                "bash",
                0,
                command="python3 -c \"open('node_modules/new.js','w').write('x')\"",
            )
        ),
        reply(call("test", 0, code=PROVES)),
    )
    result, _ = js_loop(jsrepo, tmp_path, chat)
    refused = tool_results(chat, 1)[-1]
    assert refused.startswith("Refused: this command would modify the installed")
    assert "`rm` on node_modules/keep.js" in refused
    assert (install / "keep.js").read_text() == "installed\n"  # never ran
    warned = tool_results(chat, 2)[-1]
    assert "WARNING: this command modified the installed dependencies" in warned
    assert "added node_modules/new.js" in warned
    assert result["dependencies_changed"] == ["added node_modules/new.js"]
    assert result["finished"] == "verified_check"


# --- 3. syntax gate and surfaced guard results --------------------------------------


def test_workspace_hooks_gate_writes_and_render_every_hunk(tmp_path):
    (tmp_path / "a.ts").write_text("".join(f"line {n}\n" for n in range(1, 41)))
    tools = Workspace(tmp_path)
    seen = []

    def gate(name, before, after):
        seen.append((name, before is None))
        if "BROKEN" in after:
            raise ToolError("Rejected: a.ts would not parse. No changes were written.")

    tools.content_gate = gate
    tools.change_view = render_hunks
    result = tools.edit(
        "a.ts",
        [
            {"oldText": "line 2\n", "newText": "line two\n"},
            {"oldText": "line 30\n", "newText": "line thirty\n"},
        ],
    )
    assert "Lines 1-3 now read:\nline 1\nline two\nline 3" in result.text
    assert "Lines 29-31 now read:\nline 29\nline thirty\nline 31" in result.text
    with pytest.raises(ToolError, match="No changes were written"):
        tools.edit("a.ts", [{"oldText": "line 5\n", "newText": "BROKEN\n"}])
    assert "BROKEN" not in (tmp_path / "a.ts").read_text()
    tools.write("b.ts", "const b = 1\n")
    assert seen[-1] == ("b.ts", True)
    # Python mode (no hooks): one window from the first change, as before.
    plain = Workspace(tmp_path)
    shown = plain.edit(
        "a.ts",
        [
            {"oldText": "line 3\n", "newText": "line three\n"},
            {"oldText": "line 31\n", "newText": "line thirty-one\n"},
        ],
    ).text
    assert "Lines 2-25 now read" in shown and "thirty-one" not in shown


def test_render_hunks_limits_regions_and_lines():
    before = "\n".join(f"l{n}" for n in range(100))
    after = before.replace("l10", "\n".join(f"new{n}" for n in range(30)))
    after = after.replace("l50\n", "")
    shown = render_hunks(before, after)
    assert "[... 16 more lines of this region]" in shown
    assert "l49\nl51" in shown  # the deletion with a line around it


def test_code_frame_marks_the_line_and_column():
    frame = code_frame(["a", "b(", "c", "d"], 3, 2, before=2, after=1)
    assert frame == ("  1 | a\n  2 | b(\n> 3 | c\n    |  ^\n  4 | d")


@zustand
def test_syntax_gate_uses_the_project_typescript(tmp_path):
    gate = SyntaxGate(ZUSTAND, {"PATH": node_path()})
    try:
        text = (ZUSTAND / "src" / "traditional.ts").read_text()
        broken = text.replace(
            "equalityFn?: (a: U, b: U) => boolean,",
            "equalityFn?: (a: U, b: U) => boolean,,",
        )
        started = time.perf_counter()
        with pytest.raises(ToolError) as refused:
            gate("src/traditional.ts", text, broken)
        assert time.perf_counter() - started < 3
        assert "Rejected: src/traditional.ts would not parse (TS1138" in str(
            refused.value
        )
        assert "No changes were written" in str(refused.value)
        assert ">" in str(refused.value) and "^" in str(refused.value)
        gate("src/traditional.ts", text, text)  # parses: allowed
        gate("src/x.ts", "let x = (", "let x = ((")  # did not parse before
        gate("README.md", "", "((((")  # not JS
        assert gate.errors("a.ts", "const f = <T,>(x: T) => x\n") == []
        assert gate.errors("a.tsx", "const x = <div>hi</div>\n") == []
    finally:
        gate.close()


def started_guard(jsrepo, tmp_path):
    work = copy_sources(jsrepo, tmp_path / "work")
    tools = js_tools(work, tmp_path / "runs.jsonl")
    guard = JsGuard(tools, toolchain_for(), originals_from(jsrepo))
    guard.start(copy_sources(jsrepo, tmp_path / "pristine"))
    return guard, tools


def test_guard_peek_reports_new_type_errors_and_regressions(jsrepo, tmp_path):
    guard, tools = started_guard(jsrepo, tmp_path)
    try:
        assert guard.peek(0) is None  # nothing changed yet
        tools.edit(
            "src/m.js",
            [
                {"oldText": "export function add", "newText": "function add"},
                {"oldText": "return a - b", "newText": "return a - b // TYPE_ERROR(x)"},
            ],
        )
        guard.changed()
        found = guard.peek(30)
        assert found["pending"] == []
        assert found["type_errors"] == [
            "src/m.js(2,1): error TS2322: Type 'x' is wrong."
        ]
        assert found["regressions"] == ["tests/m.test.js > keeps export"]
        assert "expected file to contain" in found["messages"][found["regressions"][0]]
    finally:
        guard.cleanup()


def test_guard_results_are_shown_after_an_edit(jsrepo, tmp_path):
    edit = call(
        "edit", 0, path="src/m.js", oldText="a - b", newText="a + b // TYPE_ERROR(new)"
    )
    script = scripted(
        reply(edit),
        reply(call("read", 0, path="src/m.js")),
        reply(call("done", 0, summary="x")),
        reply(call("done", 0, summary="x")),
    )
    work = copy_sources(jsrepo, tmp_path / "work")
    tools = js_tools(work, tmp_path / "runs.jsonl")
    guard = JsGuard(tools, toolchain_for(), originals_from(jsrepo))
    guard.start(copy_sources(jsrepo, tmp_path / "pristine"))

    def chat(messages, tools_, limit):
        if len(script.seen) == 1:
            # Every run for the edit has finished before the next tool runs
            # (the test results may come with the edit's or the read's result).
            assert guard.peek(30)["pending"] == []
        return script(messages, tools_, limit)

    try:
        loop_module.agent_loop(
            tools,
            "Make add add",
            [],
            chat,
            tmp_path,
            1e18,
            originals=originals_from(jsrepo),
            toolchain=toolchain_for(),
            guard=guard,
            max_turns=4,
        )
    finally:
        guard.cleanup()
    shown = tool_results(script, 1)[-1]  # the edit's result: tsc is waited for
    assert "[Background checks of your edited code:" in shown
    assert "tsc --noEmit: 1 new error(s) vs the original code" in shown
    assert "src/m.js(2,1): error TS2322: Type 'new' is wrong." in shown
    everything = "".join(tool_results(script, len(script.seen) - 1))
    # Each part is shown exactly once, with whichever result it was ready for.
    assert everything.count("tsc --noEmit: 1 new error(s)") == 1
    assert everything.count("existing tests: no new failures") == 1


# --- 2. evidence rules ---------------------------------------------------------------


def run_record(tests, failures=None, load_error=None):
    return {
        "tests": tests,
        "failures": failures or [],
        "load_error": load_error,
    }


def test_verdicts_name_setup_errors_and_partial_fixes():
    now = run_record(
        {"a": "passed", "b": "failed", "c": "failed"},
        [
            {"name": "b", "message": "TypeError: createState is not a function"},
            {"name": "c", "message": "AssertionError: expected 1 to be 2"},
        ],
    )
    before = run_record(
        {"a": "failed", "b": "failed", "c": "failed"},
        [
            {"name": "a", "message": "AssertionError: x"},
            {"name": "b", "message": "TypeError: createState is not a function"},
            {"name": "c", "message": "AssertionError: expected 1 to be 2"},
        ],
    )
    verdict = compare_tests(now, before)
    assert verdict["setup"] == ["b"] and verdict["same"] == ["b", "c"]
    assert verdict["crash"] == []
    text = js_verdict_text(verdict)
    assert text.startswith("\nThe results above are from your current, edited code")
    assert "'a' failed on the original code and pass now, so your change works" in text
    # Both explanations stay open: never "drop the test" or "leave the source".
    assert "'c' fail the same way on the original code (same message): either " in text
    assert "(compare it with the passing tests)" in text
    assert "'b' throw the same error on the original code, inside the test" in text
    assert "unless the request is about exactly this error" in text
    assert "do not change the source" not in text and "drop" not in text
    assert uninformative(now, verdict)
    broke = dict(verdict, broke=["z"])
    assert not uninformative(now, broke)


def test_a_same_crash_inside_the_source_is_never_called_a_test_bug():
    crash = {
        "name": "b",
        "message": "TypeError: isEqual is not a function",
        "source": True,  # thrown in repository source, not the test module
    }
    now = run_record({"a": "passed", "b": "failed"}, [crash])
    before = run_record(
        {"a": "failed", "b": "failed"},
        [{"name": "a", "message": "x"}, dict(crash, source=True)],
    )
    verdict = compare_tests(now, before)
    assert verdict["crash"] == ["b"] and verdict["setup"] == []
    text = js_verdict_text(verdict)
    assert (
        "'b' throw the same error inside the source on the original code: if " in text
    )
    assert "your change does not reach it yet" in text
    assert "test itself" not in text
    # A reproduced crash the change has not fixed speaks against it.
    assert not uninformative(now, verdict)


def test_failures_thrown_in_repository_source_are_marked(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "m.ts").write_text("export const f = (x) => x.y.z\n")
    name = "tests/__devstill_check_1.test.ts"

    def entry(title, frames):
        return {
            "ancestorTitles": [],
            "title": title,
            "fullName": title,
            "status": "failed",
            "failureMessages": [
                "TypeError: Cannot read properties of undefined (reading 'z')\n"
                + "\n".join(f"    at {frame}" for frame in frames)
            ],
        }

    data = {
        "testResults": [
            {
                "name": str(tmp_path / name),
                "status": "failed",
                "message": "",
                "assertionResults": [
                    entry("in source", [f"f ({tmp_path}/src/m.ts:1:30)"]),
                    entry("in test", [f"{tmp_path}/{name}:3:9"]),
                ],
            }
        ]
    }
    described = describe_test_run(
        ToolResult("", ok=False), data, tmp_path, name, "a\nb\nc\n"
    )
    failures = {f["name"]: f for f in described.meta["record"]["failures"]}
    assert failures["in source"]["source"] is True
    assert "source" not in failures["in test"]
    assert "thrown in src/m.ts:1 (f)" in described.text


ALWAYS_FAILS = "it('needs never', () => expect(read('src/m.js')).toContain('NEVER'))\n"
SETUP_BUG = (
    "it('setup', () => { guard(read('src/m.js'), 'NEVER'); expect(1).toBe(1) })\n"
)
WEAK = "it('exports', () => expect(read('src/m.js')).toContain('export'))\n"


def test_identical_failures_are_excused_at_the_turn_limit(jsrepo, tmp_path):
    chat = scripted(reply(EDIT, call("test", 1, code=ALWAYS_FAILS)))
    result, _ = js_loop(jsrepo, tmp_path, chat, max_turns=1)
    assert result["status"] == "applied_unvalidated", result
    assert result["finished"] == "turn_limit" and not result["verified"]
    check = result["commands"][0]
    assert check["check_problem"] and check["verdict"]["same"] == ["needs never"]


def test_done_refused_on_the_last_call_is_judged_like_the_turn_limit(jsrepo, tmp_path):
    chat = scripted(
        reply(EDIT, call("test", 1, code=ALWAYS_FAILS)),
        reply(call("done", 0, summary="fixed")),
    )
    result, _ = js_loop(jsrepo, tmp_path, chat, max_turns=2)
    assert result["status"] == "applied_unvalidated", result
    assert result["finished"] == "turn_limit" and result["model_calls"] == 2


def test_a_failing_setup_check_does_not_block_auto_finish(jsrepo, tmp_path):
    # The setup bug throws the same TypeError inside the test module on the
    # original code; once the same test passes after the same edit, it no
    # longer holds back auto-finish.
    fixed_setup = "it('setup', () => expect(read('src/m.js')).toContain('a + b'))\n"
    chat = scripted(
        reply(EDIT, call("test", 1, code=SETUP_BUG)),
        reply(call("test", 0, code=fixed_setup + PROVES)),
    )
    result, _ = js_loop(jsrepo, tmp_path, chat)
    assert (
        "'setup' throw the same error on the original code, inside the test module"
        in tool_results(chat, 1)[-1]
    )
    assert result["finished"] == "verified_check" and result["model_calls"] == 2
    assert result["commands"][0]["verdict"]["setup"] == ["setup"]
    assert result["commands"][0]["superseded"] and result["verified"]


def test_a_setup_error_the_latest_check_dropped_still_blocks_auto_finish(
    jsrepo, tmp_path
):
    # The failing test is gone from the passing check: it may be the very
    # scenario the request is about, so the model decides (done).
    chat = scripted(
        reply(EDIT, call("test", 1, code=SETUP_BUG)),
        reply(call("test", 0, code=PROVES)),
        reply(call("done", 0, summary="fixed")),
    )
    result, _ = js_loop(jsrepo, tmp_path, chat)
    assert result.get("finished") is None and result["model_calls"] == 3
    assert result["status"] == "applied_unvalidated"


def fake_runner(monkeypatch, judge):
    """Replace the test tool's runner: judge(code, source of src/m.js) gives
    (tests, failures, load_error) for a module, on the edited code as on the
    original."""

    def run(workspace, toolchain, code, timeout, checkpoint=None, type_check=None, **_):
        source = (Path(workspace.root) / "src" / "m.js").read_text()
        tests, failures, load_error = judge(code, source)
        passed = sum(s == "passed" for s in tests.values())
        failed = sum(s == "failed" for s in tests.values()) + bool(load_error)
        record = {
            "tests": tests,
            "failures": failures,
            "load_error": load_error,
            "passed": passed,
            "failed": failed,
            "assertions_required": True,
        }
        text = "\n".join(f"FAIL {f['name']}\n{f['message']}" for f in failures)
        result = ToolResult(
            text + f"\n[{failed} failed, {passed} passed]", ok=passed and not failed
        )
        result.ok = bool(result.ok)
        result.meta.update(record=record, scratch="tests/__devstill_check_1.test.js")
        return result

    monkeypatch.setattr(loop_module, "run_test_module", run)


def crash_judge(code, source):
    """'no crash' throws inside src/m.js until it is guarded; 'adds' needs
    a + b. Only the tests the module names run."""
    tests, failures = {}, []
    if "no crash" in code:
        if "guarded" in source:
            tests["no crash"] = "passed"
        else:
            tests["no crash"] = "failed"
            failures.append(
                {
                    "name": "no crash",
                    "message": "TypeError: items is undefined",
                    "line": 1,
                    "source": True,
                }
            )
    if "adds" in code:
        tests["adds"] = "passed" if "a + b" in source else "failed"
        if tests["adds"] == "failed":
            failures.append({"name": "adds", "message": "expected a + b", "line": 2})
    return tests, failures, None


ADDS_ONLY = "it('adds', () => expect(add(1, 2)).toBe(3))\n"
CRASH_AND_ADDS = "it('no crash', () => expect(total()).toBe(0))\n" + ADDS_ONLY


def test_an_unfixed_crash_in_the_source_is_not_excused(jsrepo, tmp_path, monkeypatch):
    fake_runner(monkeypatch, crash_judge)
    chat = scripted(reply(EDIT, call("test", 1, code=CRASH_AND_ADDS)))
    result, _ = js_loop(jsrepo, tmp_path, chat, max_turns=1)
    assert result["status"] == "failed" and result["failure_stage"] == "turn_limit"
    check = result["commands"][0]
    assert check["verdict"]["crash"] == ["no crash"] and check["check"]


def test_an_unfixed_crash_in_the_source_blocks_auto_finish(
    jsrepo, tmp_path, monkeypatch
):
    fake_runner(monkeypatch, crash_judge)
    chat = scripted(
        reply(EDIT, call("test", 1, code=CRASH_AND_ADDS)),
        reply(call("test", 0, code=ADDS_ONLY)),
        reply(call("done", 0, summary="fixed")),
    )
    result, _ = js_loop(jsrepo, tmp_path, chat)
    assert "throw the same error inside the source" in tool_results(chat, 1)[-1]
    assert result.get("finished") is None and result["model_calls"] == 3
    # Delivered on the model's done, but not labelled verified: the crash
    # check of the same edit still stands.
    assert result["status"] == "applied_unvalidated" and not result["verified"]


def test_a_test_the_change_broke_is_never_superseded(jsrepo, tmp_path, monkeypatch):
    # The same test name and failing line pass later with another setup:
    # the change still broke what the first version checked.
    def judge(code, source):
        negative = "negative" in code
        passes = ("a - b" in source) if negative else ("a + b" in source)
        failures = [] if passes else [{"name": "label", "message": "x", "line": 3}]
        return {"label": "passed" if passes else "failed"}, failures, None

    fake_runner(monkeypatch, judge)
    chat = scripted(
        reply(
            EDIT,
            call("test", 1, code="it('label')\nsetup negative\nexpect(got).toBe(want)"),
        ),
        reply(
            call("test", 0, code="it('label')\nsetup positive\nexpect(got).toBe(want)")
        ),
        reply(call("done", 0, summary="fixed")),
    )
    result, _ = js_loop(jsrepo, tmp_path, chat)
    assert result["commands"][0]["verdict"]["broke"] == ["label"]
    assert result.get("finished") is None and result["model_calls"] == 3


def test_a_same_failure_does_not_excuse_a_done_with_calls_left(jsrepo, tmp_path):
    chat = scripted(
        reply(EDIT, call("test", 1, code=ALWAYS_FAILS)),
        reply(
            call("edit", 0, path="src/m.js", oldText="a + b", newText="b + a"),
            call("done", 1, summary="fixed"),
        ),
        reply(call("done", 0, summary="fixed")),
        reply(call("done", 0, summary="fixed")),
    )
    result, _ = js_loop(jsrepo, tmp_path, chat, max_turns=4)
    refused = tool_results(chat, 2)[-1]
    assert refused.startswith("Not finished: Your latest check fails after your last")
    assert "'needs never' fail the same way on the original code" in refused
    assert result["model_calls"] == 4 and result["status"] == "applied_unvalidated"
    assert not result["verified"]


def load_judge(code, source):
    """A module that imports a file nobody wrote: it fails to load the
    same way on every version."""
    if "IMPORT_MISSING" in code:
        message = "Error: Failed to load url ../src/utils/format (resolved id: x)"
        failure = {"name": "(module failed to load)", "message": message, "line": 1}
        return {}, [failure], message
    return crash_judge(code, source)


MISSING_IMPORT = (
    "import { f } from 'IMPORT_MISSING'\nit('f', () => expect(f()).toBe(1))\n"
)


def test_a_module_failing_to_load_identically_still_counts_until_the_end(
    jsrepo, tmp_path, monkeypatch
):
    fake_runner(monkeypatch, load_judge)
    chat = scripted(
        reply(EDIT, call("test", 1, code=MISSING_IMPORT)),
        reply(call("done", 0, summary="fixed")),
        reply(call("done", 0, summary="fixed")),
    )
    result, _ = js_loop(jsrepo, tmp_path, chat)
    refused = tool_results(chat, 2)[-1]
    assert refused.startswith("Not finished: your latest test module failed to load")
    assert result["model_calls"] == 3 and not result["verified"]
    check = result["commands"][0]
    assert check["check"] and check["verdict"]["load_same"]
    # With no call left it says nothing against the change: let through.
    chat = scripted(reply(EDIT, call("test", 1, code=MISSING_IMPORT)))
    result, _ = js_loop(jsrepo, tmp_path / "again", chat, max_turns=1)
    assert result["status"] == "applied_unvalidated"
    assert result["finished"] == "turn_limit"


def test_a_failing_assertion_check_still_blocks_auto_finish(jsrepo, tmp_path):
    chat = scripted(
        reply(EDIT, call("test", 1, code=ALWAYS_FAILS)),
        reply(call("test", 0, code=PROVES)),
        reply(call("done", 0, summary="fixed")),
    )
    result, _ = js_loop(jsrepo, tmp_path, chat)
    assert result.get("finished") is None and result["model_calls"] == 3


def test_any_passing_check_that_fails_on_the_original_discriminates(jsrepo, tmp_path):
    chat = scripted(
        reply(
            EDIT,
            call(
                "test", 1, code=PROVES + "it('x', () => expect(1).toBe(1)) // " + "\n"
            ),
            call("test", 2, code=WEAK),
            call("done", 3, summary="fixed"),
        ),
        reply(call("done", 0, summary="fixed")),
    )
    result, _ = js_loop(jsrepo, tmp_path, chat)
    assert result["status"] == "applied_unvalidated" and result["model_calls"] == 1


def test_bash_test_runs_count_only_with_their_own_status_and_repo_files():
    def known(path):
        return path.startswith("tests/") and "tmp" not in path

    assert js_run_counts("npx vitest run tests/a.test.ts", known)
    assert js_run_counts("npx vitest run tests/a.test.ts 2>&1 | tail -20", known)
    assert js_run_counts("cd sub && npx vitest run -t 'a; b'", known)
    assert js_run_counts("npx vitest run persist", known)  # a name filter
    assert not js_run_counts("npx vitest run /tmp/debug.test.ts", known)
    assert not js_run_counts(
        "npx vitest run tests/tmp/p.test.tsx | tail -15; rm -rf tests/tmp", known
    )
    assert not js_run_counts("npx vitest run tests/a.test.ts || true", known)
    assert not js_run_counts("npm test; echo done", known)
    assert js_run_counts("npm run build && npx vitest run tests/a.test.ts", known)
    assert not js_run_counts("pnpm test:unit; ls", known, ["test:unit"])
    assert js_run_counts("pnpm test:unit 2>&1 | tail", known, ["test:unit"])


def test_loop_ignores_bash_runs_of_foreign_test_files(jsrepo, tmp_path):
    outside = tmp_path / "outside.test.js"
    outside.write_text(
        "it('adds', () => expect(read('src/m.js')).toContain('a + b'))\n"
    )
    chat = scripted(
        reply(EDIT, call("test", 1, code=PROVES.replace("a + b", "a * b"))),
        reply(
            call(
                "bash",
                0,
                command=f"node_modules/.bin/vitest run {outside} "
                f"--outputFile.json={tmp_path / 'report.json'}",
            ),
            call("done", 1, summary="x"),
        ),
        reply(call("done", 0, summary="x")),
    )
    result, _ = js_loop(jsrepo, tmp_path, chat, max_turns=3)
    run = result["commands"][1]
    assert run["ok"] and not run["check"]
    assert "not counted as a check" in run["output"]
    # The failing test check is still the latest check: done is refused once.
    assert "your latest check failed" in tool_results(chat, 2)[-1]


# --- 4. feedback text ----------------------------------------------------------------


def test_refused_multi_block_edit_says_nothing_was_applied(jsrepo, tmp_path):
    edit = call(
        "edit",
        0,
        path="src/m.js",
        edits=[
            {"oldText": "a - b", "newText": "a + b"},
            {"oldText": "NOT THERE", "newText": "x"},
        ],
    )
    chat = scripted(reply(edit), reply(EDIT, call("test", 1, code=PROVES)))
    js_loop(jsrepo, tmp_path, chat)
    shown = tool_results(chat, 1)[-1]
    assert "edits[1] was not found in src/m.js" in shown
    assert "No edit in this call was applied" in shown
    assert (
        "src/m.js is unchanged. edits[0] matched and can be resent unchanged" in shown
    )


def test_duplicate_check_refusal_names_the_edited_code(jsrepo, tmp_path):
    chat = scripted(
        reply(EDIT, call("test", 1, code=ALWAYS_FAILS)),
        reply(call("test", 0, code=ALWAYS_FAILS)),
        reply(call("test", 0, code=PROVES), call("done", 1, summary="fixed")),
    )
    js_loop(jsrepo, tmp_path, chat)
    shown = tool_results(chat, 2)[-1]
    assert "it failed on your current, edited code" in shown


def test_load_errors_get_a_code_frame_with_a_caret(tmp_path):
    code = "import x from 'y'\n\nit('a', () => {\n  const s = f(\n  expect(s).toBe(1)\n})\n"
    message = (
        "Transform failed with 1 error:\ntests/__devstill_check_3.test.ts:5:2: "
        'ERROR: Expected ")" but found "expect"'
    )
    frame = load_frame(
        message, "tests/__devstill_check_3.test.ts", code.split("\n"), tmp_path
    )
    assert frame.startswith("At your test module line 5, column 3:")  # 1-based
    assert ">  5 |   expect(s).toBe(1)" not in frame  # width fits the numbers
    assert "> 5 |   expect(s).toBe(1)\n    |   ^" in frame
    assert "an unclosed ( [ { or a missing comma" in frame
    data = {
        "testResults": [
            {
                "name": str(tmp_path / "tests/__devstill_check_3.test.ts"),
                "status": "failed",
                "message": message,
                "assertionResults": [],
            }
        ]
    }
    described = describe_test_run(
        ToolResult("", ok=False),
        data,
        tmp_path,
        "tests/__devstill_check_3.test.ts",
        code,
    )
    assert "> 5 |   expect(s).toBe(1)" in described.text
    assert described.meta["record"]["load_error"] == message  # display only


def test_failures_keep_the_innermost_repository_frames(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "vanilla.ts").write_text("a\nconst x = initialState\nc\n")
    root = str(tmp_path)
    message = (
        "ReferenceError: Cannot access 'initialState' before initialization\n"
        f"    at getInitialState ({root}/src/vanilla.ts:2:11)\n"
        f"    at {root}/node_modules/dep/index.js:1:1\n"
        f"    at listener ({root}/src/middleware/select.ts:57:3)\n"
        f"    at {root}/tests/__devstill_check_1.test.ts:9:5\n"
        "    at file:///x/node_modules/@vitest/runner/dist/index.js:1:1"
    )
    shown = repo_frames(message, tmp_path, "tests/__devstill_check_1.test.ts")
    assert shown == (
        "thrown in src/vanilla.ts:2 (getInitialState), called from "
        "src/middleware/select.ts:57 (listener)\n    2 | const x = initialState"
    )
    assert (
        repo_frames(
            f"Error\n    at {root}/tests/__devstill_check_1.test.ts:3:1", tmp_path, "x"
        )
        == ""
    )


# --- 5. cheaper iterations -----------------------------------------------------------


WRONG = call("edit", 0, path="src/m.js", oldText="a - b", newText="a * b")


def test_edit_only_response_reruns_the_failing_check(jsrepo, tmp_path):
    chat = scripted(
        reply(WRONG, call("test", 1, code=PROVES)),
        reply(call("edit", 0, path="src/m.js", oldText="a * b", newText="a + b")),
    )
    result, _ = js_loop(jsrepo, tmp_path, chat)
    assert result["finished"] == "verified_check" and result["model_calls"] == 2
    rerun = result["commands"][-1]
    assert rerun["automatic"] and rerun["ok"]


def test_edit_only_response_shows_a_still_failing_rerun(jsrepo, tmp_path):
    chat = scripted(
        reply(WRONG, call("test", 1, code=PROVES)),
        reply(call("edit", 0, path="src/m.js", oldText="a * b", newText="a / b")),
        reply(call("test", 0, code=PROVES)),
        reply(call("edit", 0, path="src/m.js", oldText="a / b", newText="a + b")),
    )
    result, _ = js_loop(jsrepo, tmp_path, chat)
    shown = tool_results(chat, 2)[-1]
    assert (
        "[Your latest test module, re-run automatically on the edited code:]" in shown
    )
    assert "FAIL adds" in shown
    duplicate = tool_results(chat, 3)[-1]
    assert "re-run automatically after the edit" in duplicate
    assert result["finished"] == "verified_check" and result["model_calls"] == 4


def test_a_check_that_never_finished_is_not_rerun_after_an_edit(jsrepo, tmp_path):
    hang = call("edit", 0, path="src/m.js", oldText="a - b", newText="a + b // HANG")
    chat = scripted(
        reply(hang, call("test", 1, code=PROVES)),
        reply(call("edit", 0, path="src/m.js", oldText=" // HANG", newText="")),
        reply(call("test", 0, code=PROVES)),
    )
    started = time.perf_counter()
    result, _ = js_loop(jsrepo, tmp_path, chat, bash_timeout=2.0)
    assert "re-run automatically" not in tool_results(chat, 2)[-1]
    assert result["finished"] == "verified_check" and result["model_calls"] == 3
    assert time.perf_counter() - started < 20


def test_editing_the_last_test_module_patches_and_reruns_it(jsrepo, tmp_path):
    wrong_test = PROVES.replace("a + b", "a * b")
    chat = scripted(
        reply(EDIT, call("test", 1, code=wrong_test)),
        reply(
            call(
                "edit",
                0,
                path="tests/__devstill_check_1.test.js",
                oldText="'a * b'",
                newText="'a + b'",
            )
        ),
        reply(call("done", 0, summary="fixed")),
    )
    result, work = js_loop(jsrepo, tmp_path, chat)
    patched = tool_results(chat, 2)[-1]
    assert patched.startswith("Patched your last test module (1 block) and ran it")
    assert patched.endswith("1 passed")
    assert result["status"] == "applied_unvalidated" and result["model_calls"] == 3
    assert result["commands"][-1]["command"] == "test:\n" + PROVES
    assert "a + b" in (work / "src" / "m.js").read_text()
    assert not any("__devstill_check" in p.name for p in (work / "tests").iterdir())


def test_budget_note_near_the_call_limit(jsrepo, tmp_path):
    chat = scripted(
        reply(call("read", 0, path="src/m.js")),
        reply(EDIT, call("test", 1, code=PROVES)),
    )
    js_loop(jsrepo, tmp_path, chat, max_turns=4)
    assert "[model call 2 of 4 comes next: finish now" in tool_results(chat, 1)[-1]


# --- 6. deadline salvage -------------------------------------------------------------


def test_soft_deadline_stops_model_calls_and_delivers(jsrepo, tmp_path, monkeypatch):
    monkeypatch.setattr(loop_module, "JS_FINISH_RESERVE", 30.0)
    deadline = time.perf_counter() + 31.0
    calls = []

    def chat(messages, tools, limit):
        calls.append(limit)
        time.sleep(1.2)
        return reply(EDIT)

    work = copy_sources(jsrepo, tmp_path / "work")
    result = loop_module.agent_loop(
        js_tools(work),
        "Make add add",
        [],
        chat,
        tmp_path,
        deadline,
        originals=originals_from(jsrepo),
        toolchain=toolchain_for(),
    )
    assert len(calls) == 1 and calls[0] == pytest.approx(deadline - 8.0)
    assert result["status"] == "applied_unvalidated"
    assert result["finished"] == "deadline"


def test_a_model_call_cut_by_the_deadline_still_delivers(jsrepo, tmp_path, monkeypatch):
    monkeypatch.setattr(loop_module, "JS_FINISH_RESERVE", 2.0)
    monkeypatch.setattr(loop_module, "JS_CALL_RESERVE", 8.0)
    deadline = time.perf_counter() + 11.0
    limits = []

    def chat(messages, tools, limit):
        limits.append(limit)
        if len(limits) == 1:
            return reply(EDIT)
        time.sleep(max(0.0, limit - time.perf_counter()))  # the call times out
        raise TimeoutError("Editor deadline exhausted")

    work = copy_sources(jsrepo, tmp_path / "work")
    result = loop_module.agent_loop(
        js_tools(work),
        "Make add add",
        [],
        chat,
        tmp_path,
        deadline,
        originals=originals_from(jsrepo),
        toolchain=toolchain_for(),
    )
    assert len(limits) == 2 and limits[1] == pytest.approx(deadline - 8.0)
    assert result["status"] == "applied_unvalidated", result
    assert result["finished"] == "deadline" and result["model_calls"] == 1


def test_an_earlier_snapshot_that_passes_the_check_is_delivered(jsrepo, tmp_path):
    chat = scripted(
        reply(EDIT),  # a + b: right
        reply(call("edit", 0, path="src/m.js", oldText="a + b", newText="a * b")),
        reply(call("bash", 0, command="true")),
        reply(call("test", 0, code=PROVES)),
    )
    result, work = js_loop(jsrepo, tmp_path, chat, max_turns=4)
    assert result["status"] == "applied_unvalidated", result
    assert result["finished"] == "earlier_snapshot" and result["snapshot_turn"] == 1
    assert "a + b" in (work / "src" / "m.js").read_text()
    assert result["verified"]
    runs = [
        json.loads(line) for line in (tmp_path / "runs.jsonl").read_text().splitlines()
    ]
    scratch = [r for r in runs if any("__devstill_check" in f for f in r["files"])]
    # The check, its run on the original code, then once on the snapshot (the
    # final finish does not re-run what salvage just ran there).
    assert len(scratch) == 3


def test_no_snapshot_passes_and_the_final_state_is_kept(jsrepo, tmp_path):
    chat = scripted(
        reply(WRONG),
        reply(call("edit", 0, path="src/m.js", oldText="a * b", newText="a / b")),
        reply(
            call(
                "test",
                0,
                code=PROVES
                + "it('keeps', () => expect(read('src/m.js')).toContain('export'))\n",
            )
        ),
    )
    result, work = js_loop(jsrepo, tmp_path, chat, max_turns=3)
    assert "a / b" in (work / "src" / "m.js").read_text()
    salvaged = [c for c in result["commands"] if c.get("salvage")]
    assert salvaged and not any(c["check"] for c in salvaged)


class StubGuard:
    """A guard whose type check finds TYPE_ERROR(new) in src/m.js; `settled`
    False keeps its runs pending (results never known)."""

    isolated = True
    timeout = 30.0

    def __init__(self, work, settled=True):
        self.work, self.settled, self.judged = work, settled, []

    def changed(self):
        pass

    def close(self):
        pass

    def diff(self):
        return ""

    def _errors(self):
        text = (self.work / "src" / "m.js").read_text()
        return ["src/m.js(2,1): error TS2322: new"] if "TYPE_ERROR(new)" in text else []

    def regressions(self, wait, deadline=None):
        self.judged.append((self.work / "src" / "m.js").read_text())
        errors = self._errors()
        if not errors or not self.settled:
            return None
        return {"regressions": [], "added": [], "type_errors": errors, "output": ""}

    def peek(self, wait=0.0, parts=None):
        return {
            "generation": 1,
            "pending": [] if self.settled else ["tsc"],
            "type_errors": self._errors() if self.settled else None,
            "regressions": [] if self.settled else None,
            "added": [] if self.settled else None,
            "messages": {},
        }


def salvage_run(jsrepo, tmp_path, settled):
    """a + b first (right), then a * b with a new type error; the agent's
    check fails identically on the final state and the original code."""
    chat = scripted(
        reply(EDIT),
        reply(
            call(
                "edit",
                0,
                path="src/m.js",
                oldText="a + b",
                newText="a * b // TYPE_ERROR(new)",
            )
        ),
        reply(call("test", 0, code=PROVES)),
    )
    work = copy_sources(jsrepo, tmp_path / "work")
    guard = StubGuard(work, settled)
    result = loop_module.agent_loop(
        js_tools(work),
        "Make add add",
        [],
        chat,
        tmp_path,
        1e18,
        originals=originals_from(jsrepo),
        toolchain=toolchain_for(),
        guard=guard,
        max_turns=3,
    )
    return result, work, guard


def test_the_final_state_is_judged_before_any_snapshot(jsrepo, tmp_path):
    result, _, guard = salvage_run(jsrepo, tmp_path, settled=True)
    # The final state's own guard results first, then the snapshot's.
    assert "TYPE_ERROR(new)" in guard.judged[0]
    assert "a + b" in guard.judged[1] and "TYPE_ERROR(new)" not in guard.judged[1]
    assert result["finished"] == "earlier_snapshot" and result["snapshot_turn"] == 1
    assert result["verified"] and result["regressions"] == []


def test_a_snapshot_whose_guard_never_settled_is_not_delivered(jsrepo, tmp_path):
    result, work, _ = salvage_run(jsrepo, tmp_path, settled=False)
    assert result.get("finished") != "earlier_snapshot"
    assert "TYPE_ERROR(new)" in (work / "src" / "m.js").read_text()  # the final
    salvaged = [c for c in result["commands"] if c.get("salvage")]
    # Every run made for the rejected snapshot is marked and never counts.
    assert salvaged and not any(c["check"] for c in salvaged)
    assert not result["verified"]


def test_records_of_a_rejected_snapshot_never_judge_the_final_state(jsrepo, tmp_path):
    # Snapshot 1 passes the check but the guard rejects it; the final state
    # fails that check identically and its only passing check also passes
    # on the original code: it must not borrow the snapshot's evidence.
    keeps = "it('keeps', () => expect(read('src/m.js')).toContain('export'))\n"
    chat = scripted(
        reply(
            call(
                "edit",
                0,
                path="src/m.js",
                oldText="a - b",
                newText="a + b // TYPE_ERROR(new)",
            )
        ),
        reply(
            call(
                "edit",
                0,
                path="src/m.js",
                oldText="a + b // TYPE_ERROR(new)",
                newText="a * b",
            ),
            call("test", 1, code=keeps),
            call("test", 2, code=PROVES),
        ),
        reply(call("read", 0, path="src/m.js")),
    )
    work = copy_sources(jsrepo, tmp_path / "work")
    result = loop_module.agent_loop(
        js_tools(work),
        "Make add add",
        [],
        chat,
        tmp_path,
        1e18,
        originals=originals_from(jsrepo),
        toolchain=toolchain_for(),
        guard=StubGuard(work),
        max_turns=3,
    )
    assert result["status"] == "failed", result
    assert "also passes on the original code" in result["error"]
    assert "a * b" in (work / "src" / "m.js").read_text()
    salvaged = [c for c in result["commands"] if c.get("salvage")]
    assert salvaged and not any(c["check"] for c in salvaged)


def test_process_deadline_from_the_environment():
    assert process_deadline(500.0, {}, now=100.0, clock=1000.0) == 500.0
    assert process_deadline(500.0, {"DEVSTILL_DEADLINE": "bad"}, 100.0, 1000.0) == 500.0
    moved = process_deadline(
        500.0, {"DEVSTILL_DEADLINE": "1100"}, now=100.0, clock=1000.0
    )
    assert moved == pytest.approx(196.0)  # 100 s from now, less 4 s after the loop
    assert (
        process_deadline(150.0, {"DEVSTILL_DEADLINE": "1100"}, 100.0, 1000.0) == 150.0
    )


def test_commands_never_see_the_harness_stop_time():
    env = command_environment(
        {"PATH": "/bin", "DEVSTILL_DEADLINE": "1", "MY_API_KEY": "x", "LLM": "k"},
        "LLM",
    )
    assert env == {"PATH": "/bin"}


# --- 7. scratch test modules ----------------------------------------------------------


def test_scratch_modules_without_jsx_use_the_plain_suffix(jsrepo, tmp_path):
    assert has_jsx("render(<App />)") and has_jsx("const x = <div>a</div>")
    assert not has_jsx("const f = <T,>(x: T): T => x\nif (a < b && c > d) {}")
    assert plain_suffix({}, "tests", ".test.tsx") == ".test.ts"
    assert (
        plain_suffix({"include": ["tests/**/*.test.tsx"]}, "tests", ".test.tsx") is None
    )
    assert plain_suffix({}, "tests", ".test.ts") is None
    toolchain = toolchain_for(test_suffix=".test.jsx", plain_suffix=".test.js")
    work = copy_sources(jsrepo, tmp_path / "work")
    log = tmp_path / "runs.jsonl"
    tools = js_tools(work, log)
    run_test_module(tools, toolchain, PROVES, 30)
    run_test_module(tools, toolchain, "const x = <b/>\n" + PROVES, 30)
    runs = [json.loads(line)["files"] for line in log.read_text().splitlines()]
    assert runs == [
        ["tests/__devstill_check_1.test.js"],
        ["tests/__devstill_check_2.test.jsx"],
    ]
    system = loop_module.js_system(toolchain)
    assert (
        "tests/__devstill_check_<n>.test.js (.test.jsx when it contains JSX)" in system
    )
    assert "one short it() per scenario the request names" in system


def test_scratch_suffix_keeps_the_jsx_one_when_environments_differ(tmp_path):
    config = parse_vitest_config(
        "export default defineConfig({ test: { environment: 'node', "
        "environmentMatchGlobs: [['**/*.tsx', 'jsdom']] } })"
    )
    assert config["environments"]
    assert plain_suffix(config, "tests", ".test.tsx") is None
    assert parse_vitest_config("defineConfig({ test: { projects: ['a'] } })")[
        "environments"
    ]
    (tmp_path / "vitest.config.ts").write_text("export default { test: {} }\n")
    assert "environments" not in vitest_config(tmp_path)
    (tmp_path / "vitest.workspace.ts").write_text("export default ['a']\n")
    assert vitest_config(tmp_path)["environments"]


def test_solution_style_tsconfig_picks_the_referenced_project(tmp_path):
    (tmp_path / "tsconfig.json").write_text(
        '{\n  // as in Vite templates\n  "files": [],\n  "references": [\n'
        '    { "path": "./tsconfig.app.json" },\n    { "path": "./tsconfig.node.json" },\n'
        "  ]\n}\n"
    )
    (tmp_path / "tsconfig.app.json").write_text(
        '{ "compilerOptions": { "jsx": "react-jsx" }, "include": ["src"] }'
    )
    (tmp_path / "tsconfig.node.json").write_text('{ "include": ["vite.config.ts"] }')
    (tmp_path / "packages" / "a").mkdir(parents=True)
    (tmp_path / "packages" / "a" / "tsconfig.json").write_text(
        '{ "include": ["src/**/*.ts"] }'
    )
    assert tsconfig_for(tmp_path, "src/__devstill_check_1.test.tsx") == (
        "tsconfig.app.json"
    )
    assert tsconfig_for(tmp_path, "vite.config.ts") == "tsconfig.node.json"
    assert tsconfig_for(tmp_path, "tests/__devstill_check_1.test.ts") is None
    (tmp_path / "tsconfig.json").write_text(
        '{ "files": [], "references": [{ "path": "packages/a" }] }'
    )
    assert tsconfig_for(tmp_path, "packages/a/src/x.ts") == "packages/a/tsconfig.json"
    (tmp_path / "tsconfig.json").write_text('{ "compilerOptions": {} }')
    assert tsconfig_for(tmp_path, "tests/x.test.ts") == "tsconfig.json"


def test_scratch_type_check_is_skipped_without_a_covering_tsconfig(jsrepo, tmp_path):
    work = copy_sources(jsrepo, tmp_path / "work")
    (work / "tsconfig.json").write_text(
        '{ "files": [], "references": [{ "path": "./tsconfig.app.json" }] }'
    )
    (work / "tsconfig.app.json").write_text('{ "include": ["src"] }')
    name = "tests/__devstill_check_1.test.ts"
    assert scratch_type_errors(js_tools(work), toolchain_for(), name, "x\n") is None
    assert not (work / "__devstill_check_tsconfig.json").exists()


def test_failing_scratch_module_is_type_checked(jsrepo, tmp_path):
    work = copy_sources(jsrepo, tmp_path / "work")
    tools = js_tools(work)
    calls = []

    def typed(name):
        calls.append((name, (work / name).is_file()))
        return "Type errors in your test module (tsc; ...)"

    toolchain = toolchain_for()
    failing = run_test_module(tools, toolchain, PROVES, 30, type_check=typed)
    assert failing.text.endswith("Type errors in your test module (tsc; ...)")
    assert calls == [("tests/__devstill_check_1.test.js", True)]
    run_test_module(
        tools, toolchain, PROVES.replace("a + b", "a - b"), 30, type_check=typed
    )
    run_test_module(tools, toolchain, "SYNTAX ERROR expect(", 30, type_check=typed)
    assert len(calls) == 1  # not after a passing run or a module that never loaded


@zustand
def test_scratch_type_errors_on_zustand(tmp_path):
    repo = tmp_path / "zustand"
    subprocess.run(
        ["git", "clone", "--shared", "--quiet", str(ZUSTAND), str(repo)], check=True
    )
    link_dependencies(ZUSTAND, repo)
    toolchain = detect(repo, [{"path": "src/vanilla.ts"}], node_path())
    assert toolchain.test_suffix == ".test.tsx" and toolchain.plain_suffix == ".test.ts"
    tools = Workspace(repo, env=dict(os.environ, PATH=node_path()))
    code = (
        "import { createStore } from 'zustand/vanilla'\n\n"
        "it('reads', () => {\n"
        "  const store = createStore(() => ({ a: 1 }))\n"
        "  expect(store.getState().value).toBe(1)\n"
        "})\n"
    )
    name = "tests/__devstill_check_9.test.ts"
    (repo / name).write_text(code)
    try:
        text = scratch_type_errors(tools, toolchain, name, code, timeout=20)
    finally:
        (repo / name).unlink()
    assert text.startswith("Type errors in your test module")
    assert "check line 5: TS2339 Property 'value' does not exist" in text
    assert "Cannot find name 'it'" not in text and "TS2593" not in text
    assert not list(repo.glob("__devstill_check_tsconfig.json"))
    result = run_test_module(
        tools,
        toolchain,
        "import { createStore } from 'zustand/vanilla'\n"
        "const f = <T,>(x: T): T => x\n"
        "it('a', () => { expect(f(createStore(() => ({ a: 1 })).getState().a)).toBe(1) })\n",
        60,
    )
    assert result.ok, result.text  # a generic arrow parses in a .ts module
    assert result.meta["scratch"].endswith(".test.ts")
    # A solution-style tsconfig.json (Vite's layout): the referenced config
    # that covers tests/ supplies jsx, paths and types; no false errors.
    (repo / "tsconfig.app.json").write_text((repo / "tsconfig.json").read_text())
    (repo / "tsconfig.json").write_text(
        '{ "files": [], "references": [{ "path": "./tsconfig.app.json" }] }'
    )
    jsx = (
        "import { create } from 'zustand'\n"
        "import { render } from '@testing-library/react'\n"
        "it('r', () => {\n"
        "  const useS = create<{ a: number }>()(() => ({ a: 1 }))\n"
        "  function C() { const a = useS((s) => s.a); return <div>{a}</div> }\n"
        "  expect(render(<C />).container.textContent).toBe('2')\n"
        "})\n"
    )
    for module, text in ((jsx, None), (code, "TS2339 Property 'value'")):
        name = "tests/__devstill_check_9.test." + ("tsx" if module is jsx else "ts")
        (repo / name).write_text(module)
        try:
            found = scratch_type_errors(tools, toolchain, name, module, timeout=20)
        finally:
            (repo / name).unlink()
        assert found is None if text is None else text in found, found


@zustand
def test_repo_frames_from_a_real_vitest_failure(tmp_path):
    repo = tmp_path / "zustand"
    subprocess.run(
        ["git", "clone", "--shared", "--quiet", str(ZUSTAND), str(repo)], check=True
    )
    link_dependencies(ZUSTAND, repo)
    toolchain = detect(repo, [{"path": "src/vanilla.ts"}], node_path())
    tools = Workspace(repo, env=dict(os.environ, PATH=node_path()))
    code = (
        "import { createStore } from 'zustand/vanilla'\n"
        "import { subscribeWithSelector } from 'zustand/middleware'\n"
        "it('x', () => {\n"
        "  const s = createStore(subscribeWithSelector(() => ({ a: 1 })))\n"
        "  s.subscribe((st) => st.a, () => {}, { equalityFn: 5 as any })\n"
        "  s.setState({ a: 2 })\n"
        "  expect(s.getState().a).toBe(2)\n"
        "})\n"
        "it('y', () => {\n"
        "  const s = createStore(() => ({ a: 1 })\n"
        "})\n"
    )
    result = run_test_module(tools, toolchain, code, 60)
    assert "The test module failed to load" in result.text
    assert "At your test module line 11" in result.text and "^" in result.text
    fixed = code.replace(
        "({ a: 1 })\n})\n", "({ a: 1 }))\n  expect(s).toBeTruthy()\n})\n"
    )
    result = run_test_module(tools, toolchain, fixed, 60)
    assert "thrown in src/middleware/subscribeWithSelector.ts:" in result.text
    assert "called from src/vanilla.ts:" in result.text
    assert "at check line 6" in result.text


def test_python_mode_outcomes_have_no_js_keys(tmp_path):
    (tmp_path / "m.py").write_text("x = 1\n")
    chat = scripted(reply(content="nothing"), reply(content="still nothing"))
    tools = Workspace(tmp_path)
    result = loop_module.agent_loop(tools, "Fix", [], chat, tmp_path, 1e18)
    assert "dependencies_changed" not in result
    assert tools.content_gate is None and tools.change_view is None


def test_js_hooks_are_removed_after_the_loop(jsrepo, tmp_path):
    work = copy_sources(jsrepo, tmp_path / "work")
    tools = js_tools(work)
    chat = scripted(reply(EDIT, call("test", 1, code=PROVES)))
    loop_module.agent_loop(
        tools,
        "x",
        [],
        chat,
        tmp_path,
        1e18,
        originals=originals_from(jsrepo),
        toolchain=toolchain_for(),
    )
    assert tools.content_gate is None and tools.change_view is None
    assert Path(work / "src" / "m.js").read_text().count("a + b") == 1
