"""JS mode, round 2: efficiency and delivery changes (all gated on JS mode).

P1 adaptive guard wait, P2 original-code runs in a pristine copy, P3 per-state
recorded verdicts and the recorded delivery tier, P4 time-based call budget,
P5 stale-mirror fix, P6 readable/patchable test modules and test setup
context, P7 compact traceable type errors, P8 edit boundary repairs; plus
the benchmark harness counterparts (H1-H4).
"""

from __future__ import annotations

import json
import subprocess
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from devstill.config import DevstillConfig, EditingConfig, js_turn_limit
from devstill.runtime import agent_loop as loop_module
from devstill.runtime.agent_loop import js_arguments, named_in_request
from devstill.runtime.jssyntax import (
    boundary_candidates,
    bracket_balance,
    close_brackets,
    empty_bodies,
)
from devstill.runtime.jstest import (
    JsGuard,
    js_diff,
    repo_frames,
    state_key,
    trace_type_errors,
    tsc_brief,
)
from devstill.runtime.loop_context import js_test_setup
from devstill.runtime.tools import ToolError, Workspace
from tests.test_js_loop_safety import fake_runner
from tests.test_js_toolchain import (
    EDIT,
    call,
    copy_sources,
    js_loop,
    js_tools,
    make_jsrepo,
    originals_from,
    reply,
    scripted,
    tool_results,
    toolchain_for,
)

FAR = 1e18


@pytest.fixture
def jsrepo(tmp_path):
    return make_jsrepo(tmp_path)


# --- P3: repo_frames and named errors ---------------------------------------------


def test_an_error_thrown_in_a_test_callback_called_by_the_source_is_a_source_error(
    tmp_path,
):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "m.ts").write_text("a\nb\n  listener(state)\n")
    scratch = "tests/__devstill_check_3.test.ts"
    message = (
        "TypeError: Cannot read properties of undefined\n"
        f"    at listener ({tmp_path}/{scratch}:9:20)\n"
        f"    at notify ({tmp_path}/src/m.ts:3:3)\n"
        f"    at {tmp_path}/{scratch}:12:5\n"
    )
    where = repo_frames(message, tmp_path, scratch)
    assert where.startswith(
        "thrown in your test module (check line 9), called from src/m.ts:3 (notify)"
    )
    assert "listener(state)" in where
    # An error thrown by the test module itself (no source frame) is not.
    own = f"TypeError: x\n    at {tmp_path}/{scratch}:4:1\n"
    assert repo_frames(own, tmp_path, scratch) == ""


def test_errors_the_request_names_are_recognized():
    request = "calling it in the creator throws a TypeError: the selector runs early"
    assert named_in_request("TypeError: state is undefined", request)
    other = 'rendering throws "Cannot read properties of undefined" when it subscribes'
    assert named_in_request(
        "TypeError: Cannot read properties of undefined (reading 'x')", other
    )
    assert not named_in_request("AssertionError: expected 1 to be 2", request)
    assert not named_in_request("TypeError: x is not a function", "make add add")
    assert not named_in_request("", request)


# --- P3: state keys and recorded verdicts -------------------------------------


def test_state_keys_ignore_scratch_files_and_unchanged_files():
    original = {"src/a.ts": b"old", "src/b.ts": b"same"}.get
    one = state_key({"src/a.ts": b"new", "src/b.ts": b"same"}, original)
    assert one == state_key({"src/a.ts": b"new"}, original)
    assert one == state_key(
        {"src/a.ts": b"new", "tests/__devstill_check_1.test.ts": b"x"}, original
    )
    assert one != state_key({"src/a.ts": b"newer"}, original)
    assert state_key({"src/a.ts": b"old"}, original) == state_key({}, original)


def guard_with_records(tmp_path, base_tsc=(), base_tests=None):
    work = tmp_path / "work"
    work.mkdir()
    tools = Workspace(work)
    guard = JsGuard(tools, toolchain_for(), lambda name: None)
    guard.pristine = guard.current = work
    guard._base["tsc"].update(result={"errors": list(base_tsc), "seconds": 3.0})
    guard._base["tsc"]["done"].set()
    guard._base["tests"].update(
        result={"tests": base_tests or {"t > a": "passed"}, "seconds": 2.0}
    )
    guard._base["tests"]["done"].set()
    return guard


def test_recorded_verdicts_are_kept_per_state(tmp_path):
    guard = guard_with_records(tmp_path)
    guard._records["k1"] = {
        "expected": {"tsc", "tests"},
        "results": {
            "tsc": {"errors": [], "seconds": 1.0},
            "tests": {"tests": {"t > a": "passed"}, "seconds": 1.0},
        },
        "files": {"src/m.js"},
    }
    guard._records["k2"] = {
        "expected": {"tsc", "tests"},
        "results": {
            "tsc": {
                "errors": [
                    {
                        "file": "src/m.js",
                        "line": 1,
                        "col": 1,
                        "code": "TS1",
                        "message": "x",
                    }
                ],
                "seconds": 1.0,
            },
            "tests": {"tests": {"t > a": "failed"}, "seconds": 1.0},
        },
        "files": {"src/m.js"},
    }
    guard._records["k3"] = {"expected": {"tsc"}, "results": {}, "files": set()}
    clean = guard.verdict("k1")
    assert clean["complete"] and clean["clean"]
    bad = guard.verdict("k2")
    assert bad["complete"] and not bad["clean"]
    assert bad["regressions"] == ["t > a"] and len(bad["type_errors"]) == 1
    unknown = guard.verdict("k3")
    assert not unknown["complete"] and not unknown["clean"]
    assert guard.verdict("missing") is None


def test_eta_comes_from_measured_durations(tmp_path):
    guard = guard_with_records(tmp_path)
    run = {"done": threading.Event(), "result": None}
    guard._runs = {"tsc": run}
    guard._generation_started = time.perf_counter()
    # No current-state measurement yet: the baseline's 3 s.
    assert 2.5 < guard.eta(("tsc",)) <= 3.0
    guard._seconds["tsc"] = 0.5
    assert 0.0 < guard.eta(("tsc",)) <= 0.5
    run["done"].set()
    assert guard.eta(("tsc",)) == 0.0
    guard._runs = {}
    assert guard.eta(("tsc",)) == 0.0


# --- P1: the adaptive wait ------------------------------------------------------


class TimedGuard:
    """A guard whose type check is expected in `eta` seconds."""

    isolated = True
    timeout = 30.0

    def __init__(self, eta):
        self._eta, self.waits = eta, []

    def changed(self):
        pass

    def close(self):
        pass

    def diff(self):
        return ""

    def regressions(self, wait, deadline=None):
        return None

    def eta(self, parts=("tsc",)):
        return self._eta

    def peek(self, wait=0.0, parts=None):
        self.waits.append(wait)
        # Still running when the edit's own result is shown (the first peek).
        return {
            "generation": 1,
            "pending": ["tsc"] if len(self.waits) == 1 else [],
            "type_errors": [],
            "regressions": [],
            "added": [],
            "messages": {},
        }


@pytest.mark.parametrize(
    "eta, expected", [(5.0, 0.0), (None, 0.0), (0.5, 0.5 + loop_module.JS_GUARD_MARGIN)]
)
def test_the_next_call_waits_only_for_a_type_check_that_is_about_to_finish(
    jsrepo, tmp_path, eta, expected
):
    chat = scripted(reply(EDIT), reply(call("done", 0, summary="fixed")))
    guard = TimedGuard(eta)
    work = copy_sources(jsrepo, tmp_path / "work")
    loop_module.agent_loop(
        js_tools(work),
        "Make add add",
        [],
        chat,
        tmp_path,
        FAR,
        originals=originals_from(jsrepo),
        toolchain=toolchain_for(),
        guard=guard,
        max_turns=3,
    )
    assert guard.waits[0] == 0.0  # the edit's own result never waits
    assert guard.waits[1] == pytest.approx(expected)


# --- P2: original-code runs in a pristine copy ------------------------------------


class CopyGuard(TimedGuard):
    def __init__(self, original):
        super().__init__(0.0)
        self.original = original

    def original_root(self, wait=0.0):
        return self.original


PROVES = "it('adds', () => expect(read('src/m.js')).toContain('a + b'))\n"


def test_original_runs_use_the_pristine_copy_and_never_swap_files(jsrepo, tmp_path):
    original = copy_sources(jsrepo, tmp_path / "original")
    work = copy_sources(jsrepo, tmp_path / "work")
    tools = js_tools(work, tmp_path / "runs.jsonl")
    chat = scripted(reply(EDIT, call("test", 1, code=PROVES)))
    result = loop_module.agent_loop(
        tools,
        "Make add add",
        [],
        chat,
        tmp_path,
        FAR,
        originals=originals_from(jsrepo),
        toolchain=toolchain_for(),
        guard=CopyGuard(original),
        max_turns=2,
    )
    assert result["finished"] == "verified_check", result
    assert result["js_stats"]["original_runs_parallel"] == 1
    assert result["js_stats"]["original_runs_swapped"] == 0
    runs = [
        json.loads(line)
        for line in (tmp_path / "runs.jsonl").read_text().split("\n")
        if line
    ]
    folders = {Path(r["cwd"]).name for r in runs if "cwd" in r}
    assert {"work", "original"} <= folders
    # The copy is left clean (no scratch module).
    assert not any("__devstill_check" in p.name for p in (original / "tests").iterdir())


# --- P3: the recorded delivery tier ----------------------------------------------


class RecordingGuard(TimedGuard):
    """Records a clean verdict for every state except those whose src/m.js
    contains DIRTY."""

    def __init__(self, work, originals):
        super().__init__(0.0)
        self.work, self.originals = work, originals
        self.dirty = set()

    def verdict(self, key):
        return {
            "complete": True,
            "clean": key not in self.dirty,
            "type_errors": [],
            "regressions": [],
            "added": [],
        }

    def changed(self):
        text = (self.work / "src" / "m.js").read_text()
        key = state_key({"src/m.js": text.encode()}, self.originals)
        if "DIRTY" in text:
            self.dirty.add(key)


WRONG_TEST = "it('adds', () => expect(read('src/m.js')).toContain('a * b'))\n"


def recorded_run(jsrepo, tmp_path, chat, query="Make add add", turns=2):
    work = copy_sources(jsrepo, tmp_path / "work")
    original = originals_from(jsrepo)
    guard = RecordingGuard(work, original)
    result = loop_module.agent_loop(
        js_tools(work),
        query,
        [],
        chat,
        tmp_path,
        FAR,
        originals=original,
        toolchain=toolchain_for(),
        guard=guard,
        max_turns=turns,
    )
    return result, work


def wrong_expectation(code, source):
    """The agent's test expects a wrong value: it fails on every version,
    with the value the code computes (so the change reaches it)."""
    got = "3" if "a + b" in source else "-1"
    failure = {"name": "adds", "message": f"expected 4, got {got}", "line": 1}
    return {"adds": "failed"}, [failure], None


def test_a_clean_state_is_delivered_when_only_the_agents_own_test_is_wrong(
    jsrepo, tmp_path, monkeypatch
):
    # The edit is right; the agent's test expects a wrong value until the
    # turn limit (it fails on the original code with another value, so no
    # earlier tier excuses it).
    fake_runner(monkeypatch, wrong_expectation)
    chat = scripted(
        reply(EDIT, call("test", 1, code=WRONG_TEST)),
        reply(call("read", 0, path="src/m.js")),
    )
    result, work = recorded_run(jsrepo, tmp_path, chat)
    assert result["status"] == "applied_unvalidated", result
    assert result["finished"] == "guard_clean" and not result["verified"]
    assert result["shadow"] == {"old_policy": "failed", "failure_stage": "turn_limit"}
    assert result["stop_reason"] == "turn_limit"
    assert "a + b" in (work / "src" / "m.js").read_text()


def test_a_dirty_final_state_falls_back_to_a_clean_snapshot(
    jsrepo, tmp_path, monkeypatch
):
    fake_runner(monkeypatch, wrong_expectation)
    chat = scripted(
        reply(EDIT, call("test", 1, code=WRONG_TEST)),
        reply(
            call("edit", 0, path="src/m.js", oldText="a + b", newText="a + b // DIRTY")
        ),
    )
    result, work = recorded_run(jsrepo, tmp_path, chat)
    assert result["finished"] == "guard_clean" and result["snapshot_turn"] == 1
    assert "DIRTY" not in (work / "src" / "m.js").read_text()


def test_nothing_is_delivered_when_no_test_of_the_agent_ever_ran(jsrepo, tmp_path):
    # Clean guard verdicts, but the agent never ran a test module: nothing
    # exercised the change, so the recorded tier delivers nothing.
    # (The last response's edit fails, so earlier tiers deliver nothing.)
    missing = call("edit", 0, path="src/m.js", oldText="not there", newText="x")
    chat = scripted(reply(EDIT), reply(missing))
    result, _ = recorded_run(jsrepo, tmp_path, chat)
    assert result["status"] == "failed" and result["failure_stage"] == "turn_limit"


def test_a_check_that_crashes_in_the_source_blocks_the_tier(
    jsrepo, tmp_path, monkeypatch
):
    def run(workspace, toolchain, code, timeout, checkpoint=None, **_):
        from devstill.runtime.tools import ToolResult

        record = {
            "tests": {"crash": "failed"},
            "failures": [
                {"name": "crash", "message": "TypeError: x", "line": 1, "source": True}
            ],
            "passed": 0,
            "failed": 1,
            "load_error": None,
            "assertions_required": True,
        }
        result = ToolResult("FAIL crash\nTypeError: x", ok=False)
        result.meta.update(record=record, scratch="tests/__devstill_check_1.test.js")
        return result

    monkeypatch.setattr(loop_module, "run_test_module", run)
    chat = scripted(
        reply(EDIT, call("test", 1, code=WRONG_TEST)),
        reply(call("read", 0, path="src/m.js")),
    )
    result, _ = recorded_run(jsrepo, tmp_path, chat)
    assert result["status"] == "failed"


def test_a_failure_the_request_names_is_never_excused(jsrepo, tmp_path, monkeypatch):
    def run(workspace, toolchain, code, timeout, checkpoint=None, **_):
        from devstill.runtime.tools import ToolResult

        record = {
            "tests": {"t": "failed"},
            "failures": [{"name": "t", "message": "TypeError: early", "line": 1}],
            "passed": 0,
            "failed": 1,
            "load_error": None,
            "assertions_required": True,
        }
        result = ToolResult("FAIL t\nTypeError: early", ok=False)
        result.meta.update(record=record, scratch="tests/__devstill_check_1.test.js")
        return result

    monkeypatch.setattr(loop_module, "run_test_module", run)
    chat = scripted(reply(EDIT, call("test", 1, code=WRONG_TEST)))
    result, _ = js_loop(
        jsrepo, tmp_path, chat, max_turns=1
    )  # no guard: only the excuse could deliver
    assert result["status"] == "applied_unvalidated"  # unrelated request: excused
    chat = scripted(reply(EDIT, call("test", 1, code=WRONG_TEST)))
    work = copy_sources(jsrepo, tmp_path / "named")
    named = loop_module.agent_loop(
        js_tools(work),
        "Calling add early throws a TypeError",
        [],
        chat,
        tmp_path,
        FAR,
        originals=originals_from(jsrepo),
        toolchain=toolchain_for(),
        max_turns=1,
    )
    assert named["status"] == "failed"
    assert named["js_stats"]["named_error_blocks"] >= 1


# --- P4: the time-based JS budget ---------------------------------------------------


def test_the_js_call_cap_follows_the_time_budget():
    assert js_turn_limit(EditingConfig()) == 30  # 120 s
    assert js_turn_limit(EditingConfig(timeout_seconds=240)) == 60
    assert js_turn_limit(EditingConfig(timeout_seconds=600)) == 60
    assert js_turn_limit(EditingConfig(timeout_seconds=30)) == 20  # never below
    assert js_turn_limit(EditingConfig(js_max_turns=12)) == 12
    assert EditingConfig().max_turns == 20  # Python keeps its cap


def test_service_uses_the_js_cap_only_for_js(monkeypatch, tmp_path):
    from devstill.runtime import service

    seen = []
    monkeypatch.setattr(
        service, "agent_loop", lambda *a, **k: seen.append(k["max_turns"]) or {}
    )
    monkeypatch.setattr(service, "delivery_checks", lambda requirements: ())
    session = SimpleNamespace(
        config=DevstillConfig(), repo=tmp_path, checkpoint=lambda deadline: None
    )
    tools = SimpleNamespace(tracked=[])
    for toolchain, expected in ((None, 20), (toolchain_for(), 30)):
        monkeypatch.setattr(service.workspace, "snapshot", lambda *a: {"src/m.js": {}})
        service.RuntimeSession._loop_with_snapshot(
            session, tmp_path, "q", [], {}, lambda *a: None, tmp_path, FAR,
            lambda m: None, tools, None, None, {}, None, toolchain, None,
        )  # fmt: skip
        assert seen[-1] == expected


# --- P5: the stale mirror ---------------------------------------------------------


def test_js_workspaces_notice_a_command_removing_a_written_file(tmp_path):
    (tmp_path / "src").mkdir()
    js = Workspace(tmp_path)
    js.track_changed = True
    js.write("src/new.ts", "export const a = 1\n")
    result = js.bash("rm src/new.ts", 10)
    assert result.changed == ["src/new.ts"]
    js.write("src/other.ts", "export const b = 1\n")
    result = js.bash("echo '// x' >> src/other.ts", 10)
    assert result.changed == ["src/other.ts"]
    # Python mode (no flag): neither is noticed, as before.
    python = Workspace(tmp_path)
    python.write("src/p.py", "a = 1\n")
    assert python.bash("rm src/p.py", 10).changed == []


def test_the_guard_notices_contents_it_never_mirrored(tmp_path):
    work = tmp_path / "work"
    (work / "src").mkdir(parents=True)
    tools = Workspace(work)
    guard = JsGuard(tools, toolchain_for(runner=None, runner_path=None), lambda n: None)
    tools.write("src/a.js", "x\n")
    guard._mirrored = guard.contents()
    assert not guard.stale()
    (work / "src" / "a.js").unlink()
    assert guard.stale()


# --- P6: test modules and test setup ------------------------------------------


def test_the_last_test_module_can_be_read_with_line_numbers(jsrepo, tmp_path):
    failing = "it('adds', () => expect(read('src/m.js')).toContain('nope'))\n"
    chat = scripted(
        reply(EDIT, call("test", 1, code=failing)),
        reply(call("read", 0, path="tests/__devstill_check_1.test.js")),
        reply(call("done", 0, summary="x")),
    )
    result, _ = js_loop(jsrepo, tmp_path, chat, max_turns=3)
    first = tool_results(chat, 1)[-1]
    assert "edit tests/__devstill_check_1.test.js with only the lines" in first
    shown = tool_results(chat, 2)[-1]
    assert shown.startswith("[your last test module (tests/__devstill_check_1.test.js)")
    assert "1 | it('adds'" in shown
    assert result["js_stats"]["scratch_reads"] == 1


def test_a_test_that_never_observes_the_edits_is_flagged(jsrepo, tmp_path):
    blind = "it('adds', () => expect(read('src/m.js')).toContain('never'))\n"
    chat = scripted(
        reply(EDIT, call("test", 1, code=blind)),
        reply(
            call("edit", 0, path="src/m.js", oldText="a + b", newText="b + a"),
            call("test", 1, code=blind + "\n"),
        ),
        reply(call("done", 0, summary="x")),
    )
    result, _ = js_loop(jsrepo, tmp_path, chat, max_turns=3)
    assert "does not observe your edits" not in tool_results(chat, 1)[-1]
    assert "on 2 different versions of your change" in tool_results(chat, 2)[-1]
    assert result["js_stats"]["insensitive_flags"] == 1


def test_setup_context_shows_imports_and_small_helpers():
    text = (
        "import { a } from './a'\nimport {\n  b,\n} from './b'\n\n"
        "type T = { x: number }\n"
        "const make = (n: number) => {\n  const s = n\n\n  return s\n}\n"
        "beforeEach(() => {\n  reset()\n})\n"
        "describe('x', () => {})\nconst late = 1\n"
    )
    shown = js_test_setup(text)
    assert shown.startswith("import { a } from './a'\nimport {\n  b,\n} from './b'\n")
    assert "const make = (n: number) => {\n  const s = n\n\n  return s\n}" in shown
    assert "beforeEach(() => {\n  reset()\n})" in shown
    assert "// also defined at the top: T (line 6)" in shown
    assert "late" not in shown
    assert js_test_setup("describe('x', () => {})\n") == ""


# --- P7: type errors ------------------------------------------------------------


def test_type_errors_are_compact_and_traced_to_the_changed_declaration(tmp_path):
    (tmp_path / "src").mkdir()
    before = "export function format(a: number) {\n  return a\n}\n"
    after = "export function format(a: string) {\n  return a\n}\n"
    (tmp_path / "src" / "f.ts").write_text(after)
    (tmp_path / "src" / "use.ts").write_text(
        "import { format } from './f'\nformat(1)\n"
    )
    use = (tmp_path / "src" / "use.ts").read_bytes()
    originals = {"src/f.ts": before.encode(), "src/use.ts": use}.get
    error = {
        "file": "src/use.ts",
        "line": 2,
        "col": 3,
        "code": "TS2345",
        "message": "Argument of type 'number' is not assignable to parameter of type "
        "'string'.\nType 'number' is not assignable to type 'string'.",
    }
    assert tsc_brief(error) == (
        "src/use.ts(2,3): error TS2345: Argument of type 'number' is not assignable "
        "to parameter of type 'string'. … Type 'number' is not assignable to type "
        "'string'."
    )
    traced, kept = trace_type_errors([error], tmp_path, originals, ["src/f.ts"])
    assert "comes from your change to 'format' in src/f.ts (line 1)" in traced
    assert "- export function format(a: number) {" in traced
    assert "+ export function format(a: string) {" in traced
    assert kept == [("src/f.ts", 1)]
    # An error at a line the agent changed is its own.
    own = dict(error, file="src/f.ts", line=1)
    assert trace_type_errors([own], tmp_path, originals, ["src/f.ts"])[0] == ""


def test_the_js_diff_keeps_traced_hunks_whole(tmp_path):
    (tmp_path / "a.ts").write_text("".join(f"line{i}x\n" for i in range(200)))
    tools = Workspace(tmp_path)
    tools.changed = ["a.ts"]
    original = "".join(f"line{i}\n" for i in range(200)).encode()
    full = js_diff(tools, lambda name: original, limit=10**6)
    assert full.count("@@") == 2  # one hunk (n=1 merges adjacent lines)
    cut = js_diff(tools, lambda name: original, limit=100)
    assert "more changed region(s) not shown" in cut
    kept = js_diff(tools, lambda name: original, keep=[("a.ts", 1)], limit=100)
    assert "line199x" in kept and "not shown" not in kept


# --- P8: edits ------------------------------------------------------------------


SOURCE = "function f(a) {\n  if (a) {\n    x()\n  }\n  return 1\n}\n"


def test_boundary_candidates_extend_old_text_over_lines_new_text_closes():
    found = boundary_candidates(
        SOURCE, "  if (a) {\n    x()\n", "  if (a) {\n    y()\n  }\n"
    )
    assert [old for old, _ in found] == ["  if (a) {\n    x()\n  }\n"]
    found = boundary_candidates(SOURCE, "    x()\n", "  if (a) {\n    y()\n")
    assert found[0][0] == "  if (a) {\n    x()\n"
    assert boundary_candidates(SOURCE, "    x()\n", "    y()\n") == []
    assert boundary_candidates(SOURCE, "missing", "x") == []
    assert bracket_balance("f('(', \"{\") // {") == 0


def test_empty_bodies_and_json_closing():
    assert empty_bodies("if (x) ;\nfor (;;) {}\nif (a) b()\nwhile (x) { y() }") == [
        "if (x) ;",
        "for (;;) {}",
    ]
    assert close_brackets('{"a": [1, {"b": "}"}') == '{"a": [1, {"b": "}"}]}'
    assert close_brackets('{"a": "open') is None
    assert js_arguments('{"path": "a", "edits": [{"oldText": "x", "newText": "y"}') == {
        "path": "a",
        "edits": [{"oldText": "x", "newText": "y"}],
    }
    with pytest.raises(ValueError):
        js_arguments('{"a": "open')


class FakeGate:
    """A syntax gate: text parses when its brackets balance."""

    def __init__(self, root, env):
        pass

    def errors(self, name, text):
        return [] if bracket_balance(text) == 0 else [{"line": 1, "message": "x"}]

    def __call__(self, name, before, after):
        if self.errors(name, after) and (
            before is None or not self.errors(name, before)
        ):
            raise ToolError(f"Rejected: {name} would not parse (x; line 1).")

    def close(self):
        pass


def test_an_edit_with_an_off_boundary_is_repaired_once_and_a_resend_refused(
    jsrepo, tmp_path, monkeypatch
):
    monkeypatch.setattr(loop_module, "SyntaxGate", FakeGate)
    (jsrepo / "src" / "g.js").write_text(SOURCE)
    repaired = call(
        "edit",
        0,
        path="src/g.js",
        oldText="  if (a) {\n    x()\n",
        newText="  if (a) {\n    y()\n  }\n",
    )
    broken = call("edit", 0, path="src/g.js", oldText="  return 1\n", newText="  {\n")
    chat = scripted(
        reply(repaired),
        reply(broken),
        reply(broken),
        reply(call("done", 0, summary="x")),
    )
    result, work = js_loop(jsrepo, tmp_path, chat, max_turns=4)
    assert (work / "src" / "g.js").read_text() == SOURCE.replace("x()", "y()")
    assert tool_results(chat, 1)[-1].startswith("[As sent, this edit would not parse;")
    assert tool_results(chat, 2)[-1].startswith("Rejected: src/g.js")
    assert tool_results(chat, 3)[-1].startswith("Rejected again: this is the same edit")
    stats = result["js_stats"]
    assert stats["boundary_repairs"] == 1 and stats["resend_refusals"] == 1


def test_an_empty_if_body_is_flagged(jsrepo, tmp_path):
    flagged = call(
        "edit", 0, path="src/m.js", oldText="a - b", newText="a + b\n  if (a) ;"
    )
    chat = scripted(reply(flagged), reply(call("done", 0, summary="x")))
    js_loop(jsrepo, tmp_path, chat, max_turns=2)
    assert "has an empty body" in tool_results(chat, 1)[-1]


# --- Harness (H1-H4) -------------------------------------------------------------


def test_the_runtime_worker_gets_the_trial_budget_for_js_corpora():
    from benchmarks.retrieval.runtime_candidate import editing_budget

    editing = EditingConfig()
    assert editing_budget(editing, {}) is None and editing.timeout_seconds == 120
    stop = time.time() + 240
    budget = editing_budget(editing, {"DEVSTILL_DEADLINE": f"{stop:.3f}"})
    assert 238 < editing.timeout_seconds <= 240.01
    assert (
        budget["max_model_calls"] == 60 and budget["configured_timeout_seconds"] == 120
    )


def test_runtime_configs_from_newer_code_validate_under_older_models():
    from benchmarks.retrieval.runtime_candidate import known_fields

    data = DevstillConfig().model_dump(mode="json")
    data["editing"]["future_option"] = 1
    data["unknown_section"] = {}
    assert DevstillConfig.model_validate(known_fields(DevstillConfig, data))


def test_the_baseline_ref_is_materialized_read_only(tmp_path):
    from benchmarks.retrieval.pi_comparison import ROOT, materialize_baseline

    commit, manifest = materialize_baseline("HEAD", tmp_path / "baseline")
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True)
    assert commit == head.strip()
    assert "src/devstill/runtime/agent_loop.py" in manifest
    path = tmp_path / "baseline" / "src/devstill/runtime/agent_loop.py"
    assert not path.stat().st_mode & 0o222


def test_baseline_variant_with_a_ref_routes_to_the_runtime_worker(monkeypatch):
    from benchmarks.retrieval import pi_comparison, runtime_candidate

    seen = []
    monkeypatch.setattr(runtime_candidate, "runtime_worker", seen.append)
    args = SimpleNamespace(variant="baseline", baseline_ref="abc")
    pi_comparison.worker(args)
    assert seen == [args]


def test_versus_baseline_pairs_the_devstill_arms():
    from benchmarks.retrieval.pi_comparison import versus_baseline

    def row(variant, repeat, passed, seconds):
        return {
            "id": "c",
            "repeat": repeat,
            "variant": variant,
            "acceptance_pass": passed,
            "worker_wall_seconds": seconds,
        }

    found = versus_baseline(
        [
            row("baseline", 0, True, 100),
            row("runtime", 0, True, 50),
            row("baseline", 1, False, 120),
            row("runtime", 1, True, 60),
        ],
        True,
    )
    assert found["paired_trials"] == 2 and found["shared_passes"] == 1
    assert found["runtime_only_passes"] == 1 and found["baseline_only_passes"] == 0
    assert found["median_runtime_over_baseline_wall_ratio"] == 0.5


def test_bench_suite_passes_the_baseline_ref():
    from benchmarks.bench_suite import suite_command

    settings = {
        "variants": "baseline,runtime",
        "repeats": 1,
        "timeout": 120,
        "baseline_ref": "5c58680",
    }
    record = {"settings": {"sampling": "default", "endpoint_wait": 60}}
    command = suite_command(
        settings, Path("d.json"), Path("v"), Path("o"), record, resume=False
    )
    assert command[command.index("--baseline-ref") + 1] == "5c58680"


# --- review 3 regressions ---------------------------------------------------------


def hanging_original(monkeypatch, started):
    """The original-code run in the pristine copy never ends by itself (a
    real process registered like run_test_module_in's)."""
    from devstill.runtime.tools import ToolResult

    def run_in(root, env, toolchain, code, timeout, register=None):
        process = subprocess.Popen(["sleep", "60"], start_new_session=True)
        started.append(process)
        if register is not None and not register(process):
            process.kill()
            return None
        process.wait()
        process.ended = time.perf_counter()
        return ToolResult("[exit code -9]", ok=False)

    monkeypatch.setattr(loop_module, "run_test_module_in", run_in)


def failing_everywhere(code, source):
    failure = {"name": "adds", "message": "expected 4, got 5", "line": 1}
    return {"adds": "failed"}, [failure], None


def test_an_abandoned_original_run_is_stopped(jsrepo, tmp_path, monkeypatch):
    started = []
    hanging_original(monkeypatch, started)
    fake_runner(monkeypatch, failing_everywhere)
    original = copy_sources(jsrepo, tmp_path / "original")
    work = copy_sources(jsrepo, tmp_path / "work")
    chat = scripted(reply(EDIT, call("test", 1, code=WRONG_TEST)))
    began = time.perf_counter()
    result = loop_module.agent_loop(
        js_tools(work),
        "Make add add",
        [],
        chat,
        tmp_path,
        time.perf_counter() + 40,
        originals=originals_from(jsrepo),
        toolchain=toolchain_for(),
        guard=CopyGuard(original),
        max_turns=1,
    )
    time.sleep(0.2)
    assert started and all(p.poll() is not None for p in started), "still running"
    # The comparison waits max(5, 3 x the run) and then stops the run (the
    # final finish may try again with a longer limit).
    assert started[0].ended - began < 15
    assert result["js_stats"]["original_runs_stopped"] >= 1


def test_cancelling_during_the_original_comparison_is_prompt(
    jsrepo, tmp_path, monkeypatch
):
    started = []
    hanging_original(monkeypatch, started)
    fake_runner(monkeypatch, failing_everywhere)
    original = copy_sources(jsrepo, tmp_path / "original")
    work = copy_sources(jsrepo, tmp_path / "work")
    chat = scripted(reply(EDIT, call("test", 1, code=WRONG_TEST)))
    cancel_at = {}

    def checkpoint():
        if started and "at" not in cancel_at:
            cancel_at["at"] = time.perf_counter() + 1.0
        if "at" in cancel_at and time.perf_counter() > cancel_at["at"]:
            raise InterruptedError("cancelled")

    with pytest.raises(InterruptedError):
        loop_module.agent_loop(
            js_tools(work),
            "Make add add",
            [],
            chat,
            tmp_path,
            time.perf_counter() + 60,
            checkpoint=checkpoint,
            originals=originals_from(jsrepo),
            toolchain=toolchain_for(),
            guard=CopyGuard(original),
            max_turns=1,
        )
    assert time.perf_counter() - cancel_at["at"] < 3.0
    time.sleep(0.2)
    assert all(p.poll() is not None for p in started)


def test_the_concurrent_scratch_type_check_gets_its_time_after_the_test_run(
    tmp_path,
):
    from devstill.runtime.jstest import ScratchTypes

    name = "tests/__devstill_check_1.test.ts"
    (tmp_path / "tests").mkdir()
    (tmp_path / "tsconfig.json").write_text('{"compilerOptions": {}}')
    tsc = tmp_path / "tsc"
    # Slower than the limit from its start, quicker than the limit after the
    # (slow) test run ends.
    tsc.write_text(
        "#!/bin/sh\nsleep 1.5\n"
        f"echo \"{name}(1,7): error TS2322: Type 'string' is not assignable to "
        "type 'number'.\"\nexit 2\n"
    )
    tsc.chmod(0o755)
    workspace = SimpleNamespace(root=tmp_path, env={"PATH": "/usr/bin:/bin"})
    timeouts = []
    check = ScratchTypes(
        workspace,
        SimpleNamespace(tsc_path="tsc"),
        name,
        "const x: number = 'a'\n",
        timeout=1.0,
        on_timeout=lambda: timeouts.append(1),
    )
    time.sleep(1.2)  # the test run
    text = check.finish(True)
    assert not timeouts
    assert text and "TS2322" in text
    # One that does not finish within the limit after the run still times out.
    tsc.write_text("#!/bin/sh\nsleep 30\n")
    check = ScratchTypes(
        workspace,
        SimpleNamespace(tsc_path="tsc"),
        name,
        "x\n",
        timeout=0.5,
        on_timeout=lambda: timeouts.append(1),
    )
    assert check.finish(True) is None and timeouts == [1]


def test_an_added_call_is_not_traced_as_a_declaration(tmp_path):
    (tmp_path / "src").mkdir()
    before = (
        "type SetState = (partial: object) => void\n"
        "export function make(set: SetState) {\n"
        "  set({ n: 1 })\n"
        "}\n"
    )
    after = (
        "type SetState = (partial: string) => void\n"
        "export function make(set: SetState) {\n"
        "  set('a')\n"
        "  set({ n: 1 })\n"
        "}\n"
    )
    (tmp_path / "src" / "b.ts").write_text(after)
    error = {
        "file": "src/b.ts",
        "line": 4,
        "col": 7,
        "code": "TS2345",
        "message": "Argument of type '{ n: number; }' is not assignable to parameter "
        "of type 'string'.",
    }
    traced, _ = trace_type_errors(
        [error], tmp_path, {"src/b.ts": before.encode()}.get, ["src/b.ts"]
    )
    assert "set('a')" not in traced
    assert "'SetState'" in traced or traced == ""


def test_the_backward_repair_covers_blank_lines_before_old_text():
    text = "const a = 1\n\nfunction g() {\n  return 1\n}\n"
    old = "function g() {\n  return 1\n}\n"
    new = "const a = 1\nfunction g() {\n  return 2\n}\n"
    found = boundary_candidates(text, old, new)
    assert [item for item, _ in found] == [
        "const a = 1\n\nfunction g() {\n  return 1\n}\n"
    ]
    applied = text.replace(found[0][0], new)
    assert applied == "const a = 1\nfunction g() {\n  return 2\n}\n"


def test_setup_context_ends_an_import_without_from_at_its_line():
    text = (
        "import fs = require('fs')\nconst a = 1\n"
        + "x()\n" * 200
        + "import { b } from 'b'\nit('x', () => {})\n"
    )
    shown = js_test_setup(text)
    assert len(shown.split("\n")) < 10
    assert "x()" not in shown
    many = "".join(f"import {{ a{i} }} from 'm{i}'\n" for i in range(40))
    assert "[25 more import lines]" in js_test_setup(many + "it('x', () => {})\n")


def crash_on_snapshot_a(code, source):
    if "DIRTY" in source:
        failure = {"name": "adds", "message": "expected 4, got 5", "line": 2}
    elif "a + b" in source:
        failure = {
            "name": "adds",
            "message": "TypeError: items is undefined",
            "line": 1,
            "source": True,
        }
    else:
        failure = {"name": "adds", "message": "expected 4, got -1", "line": 2}
    return {"adds": "failed"}, [failure], None


def test_a_salvage_run_crashing_in_the_source_blocks_that_snapshot(
    jsrepo, tmp_path, monkeypatch
):
    fake_runner(monkeypatch, crash_on_snapshot_a)
    dirty = call("edit", 0, path="src/m.js", oldText="a + b", newText="a + b // DIRTY")
    chat = scripted(reply(EDIT), reply(dirty, call("test", 1, code=WRONG_TEST)))
    result, _ = recorded_run(jsrepo, tmp_path, chat)
    assert result["status"] == "failed", result
    salvaged = [c for c in result["commands"] if c.get("salvage") is not None]
    assert salvaged and all(c.get("state") for c in salvaged)
    assert [1, "a salvage run speaks against it"] in result["js_stats"][
        "tier_candidates"
    ]


def same_type_error(code, source):
    return (
        {"t": "failed"},
        [{"name": "t", "message": "TypeError: boom", "line": 1}],
        None,
    )


def test_shadow_names_the_final_state_when_the_named_guard_blocked_its_excuse(
    jsrepo, tmp_path, monkeypatch
):
    fake_runner(monkeypatch, same_type_error)
    second = call("edit", 0, path="src/m.js", oldText="a + b", newText="(a + b)")

    def run(query, folder):
        chat = scripted(reply(EDIT), reply(second, call("test", 1, code=WRONG_TEST)))
        return recorded_run(jsrepo, tmp_path / folder, chat, query=query)[0]

    unnamed = run("Make add add", "unnamed")
    assert unnamed["status"] == "applied_unvalidated" and "shadow" not in unnamed
    named = run("Make add add; it throws a TypeError today", "named")
    assert named["js_stats"]["named_error_blocks"] >= 1
    assert named["shadow"]["old_policy"] == "final_state"
    assert named["shadow"]["final_guard"] == "clean"


def test_the_current_runtime_keeps_strict_config_validation(tmp_path, monkeypatch):
    from benchmarks.retrieval import runtime_candidate

    data = DevstillConfig().model_dump(mode="json")
    data["editing"]["js_max_turn"] = 40  # a typo
    path = tmp_path / "runtime.json"
    path.write_text(json.dumps(data))
    args = SimpleNamespace(variant="runtime", baseline_ref=None, runtime=path)
    with pytest.raises(Exception, match="js_max_turn"):
        runtime_candidate.runtime_worker(args)
    dropped = []
    runtime_candidate.known_fields(DevstillConfig, data, dropped)
    assert dropped == ["editing.js_max_turn"]
    assert runtime_candidate.baseline_arm(
        SimpleNamespace(variant="baseline", baseline_ref="abc")
    )
    assert not runtime_candidate.baseline_arm(args)


def test_the_baseline_budget_is_passed_to_the_baseline_worker():
    from benchmarks.bench_suite import suite_command
    from benchmarks.retrieval.pi_comparison import build_parser

    args = build_parser().parse_args(
        ["worker", "--output", "o", "--variant", "baseline", "--baseline-ref", "x"]
    )
    assert args.baseline_budget == "configured"
    settings = {
        "variants": "baseline,runtime",
        "repeats": 1,
        "timeout": 240,
        "baseline_ref": "5c58680",
        "baseline_budget": "trial",
    }
    record = {"settings": {"sampling": "default", "endpoint_wait": 60}}
    command = suite_command(
        settings, Path("d.json"), Path("v"), Path("o"), record, resume=False
    )
    assert command[command.index("--baseline-budget") + 1] == "trial"
