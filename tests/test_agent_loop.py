"""Quick agent loop: tools, control flow and the production service path."""

import json
import sys
import time
from pathlib import Path

import pytest

from devstill.config import DevstillConfig
from devstill.runtime.agent_loop import agent_loop, normalize_edits, repair_json
from devstill.runtime.loop_context import build_sections, module_name, outline
from devstill.runtime.service import RuntimeSession
from devstill.runtime.tools import ToolError, Workspace, apply_text_edits

FAR = 1e18


def call(name, index=0, **arguments):
    return {
        "id": f"c{index}",
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments)},
    }


def reply(*calls, content="", finish="tool_calls"):
    message = {"role": "assistant", "content": content}
    if calls:
        message["tool_calls"] = list(calls)
    return {
        "message": message,
        "finish_reason": finish if calls else "stop",
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    }


def scripted(*replies):
    queue = list(replies)
    seen = []

    def chat(messages, tools, deadline):
        seen.append(json.loads(json.dumps(messages)))
        return queue.pop(0)

    chat.seen = seen
    return chat


@pytest.fixture
def work(tmp_path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("")
    (tmp_path / "pkg" / "m.py").write_text("def add(a, b):\n    return a - b\n")
    return tmp_path


def tools_for(root):
    return Workspace(
        root,
        env={
            "PATH": str(Path(sys.executable).parent) + ":/usr/bin:/bin",
            "PYTHONPATH": str(root),
            "PYTHONDONTWRITEBYTECODE": "1",
        },
    )


# --- edit matching -----------------------------------------------------------


def test_exact_edits_apply_against_original_in_any_order():
    text = "a = 1\nb = 2\nc = 3\n"
    edits = [
        {"oldText": "c = 3", "newText": "c = 30"},
        {"oldText": "a = 1", "newText": "a = 10"},
    ]
    assert apply_text_edits(text, edits, "f.py") == "a = 10\nb = 2\nc = 30\n"


def test_ambiguous_missing_and_overlapping_edits_are_explained():
    text = "x = 1\nx = 1\ny = 2\n"
    with pytest.raises(ToolError, match=r"matches 2 places .*lines 1, 2"):
        apply_text_edits(text, [{"oldText": "x = 1", "newText": "x = 3"}], "f.py")
    with pytest.raises(ToolError, match="Closest current text"):
        apply_text_edits(text, [{"oldText": "y = 3\n", "newText": "y = 4\n"}], "f.py")
    with pytest.raises(ToolError, match="overlap"):
        apply_text_edits(
            "abc\n",
            [{"oldText": "ab", "newText": "x"}, {"oldText": "bc", "newText": "y"}],
            "f.py",
        )
    with pytest.raises(ToolError, match="identical"):
        apply_text_edits(text, [{"oldText": "y = 2", "newText": "y = 2"}], "f.py")


def test_fuzzy_match_ignores_trailing_space_and_smart_quotes_not_indentation():
    text = "def f():   \n    return 'x'\n"
    edits = [
        {
            "oldText": "def f():\n    return ‘x’\n",
            "newText": "def f():\n    return 'y'\n",
        }
    ]
    assert apply_text_edits(text, edits, "f.py") == "def f():\n    return 'y'\n"
    with pytest.raises(ToolError, match="not found"):
        apply_text_edits(text, [{"oldText": "return 'z'", "newText": "x"}], "f.py")


def test_edit_preserves_crlf_and_bom_and_rejects_syntax_errors(work):
    path = work / "pkg" / "crlf.py"
    path.write_bytes(b"\xef\xbb\xbfa = 1\r\nb = 2\r\n")
    tools = tools_for(work)
    result = tools.edit("pkg/crlf.py", [{"oldText": "b = 2", "newText": "b = 3"}])
    assert result.ok and "b = 3" in result.text
    assert path.read_bytes() == b"\xef\xbb\xbfa = 1\r\nb = 3\r\n"
    with pytest.raises(ToolError, match="would not compile"):
        tools.edit("pkg/crlf.py", [{"oldText": "b = 3", "newText": "b = ("}])
    assert path.read_bytes() == b"\xef\xbb\xbfa = 1\r\nb = 3\r\n"


def test_read_write_and_bash(work):
    tools = tools_for(work)
    assert "return a - b" in tools.read("pkg/m.py").text
    assert "[pkg/m.py lines 2-2 of 2]" in tools.read("pkg/m.py", 2, 1).text
    with pytest.raises(ToolError, match="beyond end"):
        tools.read("pkg/m.py", 9)
    assert tools.write("pkg/new.py", "VALUE = 1\n").ok
    with pytest.raises(ToolError, match="would not compile"):
        tools.write("pkg/bad.py", "def (\n")
    assert not (work / "pkg" / "bad.py").exists()
    ok = tools.bash("python -c 'import pkg.new; print(pkg.new.VALUE)'", 10)
    assert ok.ok and "1" in ok.text and "[exit code 0]" in ok.text
    failed = tools.bash("exit 3", 10)
    assert not failed.ok and "[exit code 3]" in failed.text
    started = time.perf_counter()
    slow = tools.bash("sleep 5", 0.3)
    assert not slow.ok and "timed out" in slow.text
    assert time.perf_counter() - started < 3
    assert tools.changed == ["pkg/new.py"]


def test_argument_repair_and_edit_shapes():
    assert repair_json('{"a": "x\ny"}') == {"a": "x\ny"}
    assert normalize_edits({"edits": '[{"oldText": "a", "newText": "b"}]'}) == [
        {"oldText": "a", "newText": "b"}
    ]
    assert normalize_edits({"old_text": "a", "new_text": "b"}) == [
        {"oldText": "a", "newText": "b"}
    ]
    with pytest.raises(ToolError):
        normalize_edits({"edits": []})


# --- loop control flow ---------------------------------------------------------


def test_edit_check_and_done_in_one_response_finishes_with_one_call(work):
    chat = scripted(
        reply(
            call("edit", 0, path="pkg/m.py", oldText="a - b", newText="a + b"),
            call(
                "bash",
                1,
                command="python -c 'from pkg.m import add; assert add(2, 3) == 5'",
            ),
            call("done", 2, summary="fixed add"),
        )
    )
    result = agent_loop(tools_for(work), "Fix add", ["ctx"], chat, work, FAR)
    assert result["status"] == "applied_unvalidated"
    assert result["model_calls"] == 1
    assert result["changed_files"] == ["pkg/m.py"]
    assert result["commands"][0]["ok"]
    assert "New task:\n\nRequest:\nFix add" in chat.seen[0][-1]["content"]


def test_failed_edit_skips_rest_of_batch_and_can_be_repaired(work):
    chat = scripted(
        reply(
            call("edit", 0, path="pkg/m.py", oldText="a-b", newText="a+b"),
            call("done", 1, summary="x"),
        ),
        reply(
            call("edit", 0, path="pkg/m.py", oldText="a - b", newText="a + b"),
            call("done", 1, summary="fixed"),
        ),
    )
    result = agent_loop(tools_for(work), "Fix add", [], chat, work, FAR)
    assert result["status"] == "applied_unvalidated" and result["model_calls"] == 2
    second = chat.seen[1]
    assert "not found" in second[-2]["content"]
    assert "Skipped" in second[-1]["content"]


def test_done_is_ignored_when_a_command_in_the_same_response_fails(work):
    chat = scripted(
        reply(
            call("edit", 0, path="pkg/m.py", oldText="a - b", newText="a * b"),
            call(
                "bash",
                1,
                command="python -c 'from pkg.m import add; assert add(2, 3) == 5'",
            ),
            call("done", 2, summary="x"),
        ),
        reply(
            call("edit", 0, path="pkg/m.py", oldText="a * b", newText="a + b"),
            call(
                "bash",
                1,
                command="python -c 'from pkg.m import add; assert add(2, 3) == 5'",
            ),
            call("done", 2, summary="fixed"),
        ),
    )
    result = agent_loop(tools_for(work), "Fix add", [], chat, work, FAR)
    assert result["status"] == "applied_unvalidated" and result["model_calls"] == 2
    assert [c["ok"] for c in result["commands"]] == [False, True]
    assert (work / "pkg" / "m.py").read_text().endswith("return a + b\n")


def test_text_reply_without_changes_is_nudged_once_then_fails(work):
    chat = scripted(reply(content="Looks fine."), reply(content="Still fine."))
    result = agent_loop(tools_for(work), "Fix add", [], chat, work, FAR)
    assert result["status"] == "failed" and result["failure_stage"] == "no_changes"
    assert "No file has been changed" in chat.seen[1][-1]["content"]


def test_plain_text_reply_after_edit_finishes(work):
    chat = scripted(
        reply(call("edit", 0, path="pkg/m.py", oldText="a - b", newText="a + b")),
        reply(content="Done: add now adds."),
    )
    result = agent_loop(tools_for(work), "Fix add", [], chat, work, FAR)
    assert result["status"] == "applied_unvalidated" and result["model_calls"] == 2


def test_length_cutoff_does_not_execute_truncated_calls(work):
    chat = scripted(
        reply(
            call("edit", 0, path="pkg/m.py", oldText="a - b", newText="a + b"),
            finish="length",
        ),
        reply(
            call("edit", 0, path="pkg/m.py", oldText="a - b", newText="a + b"),
            call("done", 1, summary="ok"),
        ),
    )
    result = agent_loop(tools_for(work), "Fix add", [], chat, work, FAR)
    assert result["status"] == "applied_unvalidated"
    assert "output token limit" in chat.seen[1][-1]["content"]


def test_turn_limit_accepts_only_unfailed_changes(work):
    edit = call("edit", 0, path="pkg/m.py", oldText="a - b", newText="a + b")
    ok = agent_loop(
        tools_for(work), "Fix", [], scripted(reply(edit)), work, FAR, max_turns=1
    )
    assert ok["status"] == "applied_unvalidated" and ok["finished"] == "turn_limit"
    (work / "pkg" / "m.py").write_text("def add(a, b):\n    return a - b\n")
    failing = reply(
        call("edit", 0, path="pkg/m.py", oldText="a - b", newText="a * b"),
        call("bash", 1, command="exit 1"),
    )
    bad = agent_loop(
        tools_for(work), "Fix", [], scripted(failing), work, FAR, max_turns=1
    )
    assert bad["status"] == "failed" and bad["failure_stage"] == "turn_limit"


def test_malformed_arguments_and_unknown_tools_are_reported(work):
    broken = {
        "id": "c0",
        "type": "function",
        "function": {"name": "edit", "arguments": "{oops"},
    }
    chat = scripted(
        reply(broken, call("frobnicate", 1)),
        reply(
            call("edit", 0, path="pkg/m.py", oldText="a - b", newText="a + b"),
            call("done", 1, summary="ok"),
        ),
    )
    result = agent_loop(tools_for(work), "Fix", [], chat, work, FAR)
    assert result["status"] == "applied_unvalidated"
    contents = [m["content"] for m in chat.seen[1] if m["role"] == "tool"]
    assert any("Could not parse edit arguments" in c for c in contents)
    assert any("Skipped" in c for c in contents)


def test_runtime_delivery_checks_must_pass_before_done(work):
    calls = []

    def runner(root, commands, deadline):
        calls.append(commands)
        ok = (root / "pkg" / "extra.py").exists()
        return [{"returncode": 0 if ok else 1, "output": "missing pkg/extra.py"}]

    chat = scripted(
        reply(
            call("edit", 0, path="pkg/m.py", oldText="a - b", newText="a + b"),
            call("done", 1, summary="x"),
        ),
        reply(
            call("write", 0, path="pkg/extra.py", content="X = 1\n"),
            call("done", 1, summary="y"),
        ),
    )
    result = agent_loop(
        tools_for(work),
        "Fix",
        [],
        chat,
        work,
        FAR,
        required_checks=[["python", "-c", "import pkg.extra"]],
        check_runner=runner,
    )
    assert result["status"] == "applied_unvalidated" and len(calls) == 2
    assert "delivery checks failed" in chat.seen[1][-1]["content"]


# --- context -----------------------------------------------------------------


def test_sections_merge_whole_lines_with_outline_and_related_tests(tmp_path):
    src = tmp_path / "src" / "pkg"
    src.mkdir(parents=True)
    body = "import os\n\n\nclass Box:\n    def get(self):\n        return 1\n\n    def put(self, v):\n        self.v = v\n\n\ndef helper():\n    return Box()\n"
    (src / "box.py").write_text(body)
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_box.py").write_text(
        "from pkg.box import Box\nBox().get()\n"
    )
    records = [
        {
            "path": "src/pkg/box.py",
            "lines": [5, 6],
            "symbol": "Box.get",
            "kind": "method",
            "code": "def get(self):",
        },
        {
            "path": "src/pkg/box.py",
            "lines": [8, 9],
            "symbol": "Box.put",
            "kind": "method",
            "code": "",
        },
        {
            "path": "src/pkg/box.py",
            "lines": [1, 1],
            "symbol": "imports",
            "kind": "imports",
            "code": "",
        },
    ]
    sections = build_sections(tmp_path, records, 10000)
    text = "\n".join(sections)
    assert "(import as pkg.box)" in text
    assert "L4 class Box: get, put" in text
    assert "lines 1-9:\n```python\nimport os\n" in text  # whole lines, nearby merged
    assert "    def get(self):\n        return 1" in text
    assert "Related tests: tests/test_box.py (2 mentions)" in text
    assert module_name("src/pkg/__init__.py") == "pkg"
    assert outline("x = (") == ""


# --- production service path ---------------------------------------------------


@pytest.fixture
def loop_session(tmp_path, monkeypatch):
    (tmp_path / "a.py").write_text("def f():\n    return 1\n")
    (tmp_path / "data.bin").write_bytes(b"\x00\x01binary")
    config = DevstillConfig()
    config.llm.enabled = True
    config.llm.model = "test"
    config.repo.root = str(tmp_path)
    config.index.path = str(tmp_path / ".devstill/chroma")
    config.editing.python = sys.executable
    runtime = RuntimeSession(tmp_path / "devstill.json", config)
    monkeypatch.setattr(
        runtime,
        "evidence",
        lambda query, budget=None, lexical_first=False: [
            {
                "path": "a.py",
                "code": "def f():\n    return 1",
                "lines": [1, 2],
                "symbol": "f",
                "kind": "function",
            }
        ],
    )
    yield runtime
    runtime.close()


def test_loop_engine_reviews_then_applies_and_ignores_scratch_files(
    loop_session, monkeypatch
):
    chat = scripted(
        reply(
            call(
                "edit",
                0,
                path="a.py",
                edits=[{"oldText": "return 1", "newText": "return 2"}],
            ),
            call(
                "bash",
                1,
                command="echo tmp > scratch.txt && test -f data.bin && python -c 'import a; assert a.f() == 2'",
            ),
            call("done", 2, summary="f returns 2"),
        )
    )
    monkeypatch.setattr(loop_session, "chat", chat)
    proposal = loop_session.prepare("Make f return 2")
    assert proposal["status"] == "ready", proposal
    assert proposal["model_calls"] == 1
    assert list(proposal["files"]) == ["a.py"]
    assert "+    return 2" in proposal["patch"]
    assert "### a.py" in chat.seen[0][-1]["content"]
    repo = loop_session.repo
    assert (repo / "a.py").read_text() == "def f():\n    return 1\n"  # review only
    loop_session.apply(proposal["id"], proposal["digest"])
    assert (repo / "a.py").read_text() == "def f():\n    return 2\n"
    assert not (repo / "scratch.txt").exists()


def test_loop_engine_proposes_tracked_files_changed_by_commands(
    loop_session, monkeypatch
):
    chat = scripted(
        reply(
            call("bash", 0, command="sed -i 's/return 1/return 3/' a.py"),
            call("done", 1, summary="via sed"),
        )
    )
    monkeypatch.setattr(loop_session, "chat", chat)
    proposal = loop_session.prepare("Make f return 3")
    assert proposal["status"] == "ready", proposal
    assert list(proposal["files"]) == ["a.py"]
    assert "+    return 3" in proposal["patch"]
    tool_messages = [
        m
        for m in chat.seen[0]
        if m["role"] == "tool" and not m["tool_call_id"].startswith("example")
    ]
    assert not tool_messages  # one call; the change was detected from the command


def test_python_tool_runs_scripts_without_shell_quoting_and_explains_asserts(work):
    tools = tools_for(work)
    ok = tools.python("from pkg.m import add\nprint('it''s', add(5, 3))\n", 10)
    assert ok.ok and "its 2" in ok.text
    failed = tools.python("from pkg.m import add\nassert add(2, 3) == 5, 'sum'\n", 10)
    assert not failed.ok
    assert "assert add(2, 3) == 5" in failed.text  # source line shown
    assert "left:  -1" in failed.text and "right: 5" in failed.text
    crashed = tools.python("import pkg.missing\n", 10)
    assert not crashed.ok and "ModuleNotFoundError" in crashed.text


def test_fast_binary_detection_matches_reference_rule():
    from devstill.parsers.detection import is_binary

    def reference(raw):
        sample = raw[:8192]
        if b"\0" in sample:
            return True
        if not sample:
            return False
        control = sum(v < 9 or (13 < v < 32) or v == 127 for v in sample)
        return control / len(sample) > 0.08

    samples = [
        b"",
        b"plain text\n",
        bytes(range(1, 256)),
        b"\x01" * 9 + b"a" * 91,
        b"\x01" * 8 + b"a" * 92,
        b"x\x00y",
        "café".encode() * 50,
    ]
    for raw in samples:
        assert is_binary(raw) == reference(raw)


def test_review_render_shows_agent_checks_and_summary(loop_session, monkeypatch):
    from devstill.runtime.service import render

    chat = scripted(
        reply(
            call("edit", 0, path="a.py", oldText="return 1", newText="return 2"),
            call("python", 1, code="import a\nassert a.f() == 2\n"),
            call("done", 2, summary="f returns 2"),
        )
    )
    monkeypatch.setattr(loop_session, "chat", chat)
    proposal = loop_session.prepare("Make f return 2")
    assert proposal["execution"]["verified"]
    text = render(proposal)
    assert "- passed: python check (2 lines)" in text
    assert "Latest passing check:\n```python\nimport a\nassert a.f() == 2" in text
    assert "Summary: f returns 2" in text
    state = loop_session.apply(proposal["id"], proposal["digest"])
    assert state["validation"] == "agent_check_passed"


# --- regression guard -----------------------------------------------------------


@pytest.fixture
def tested(tmp_path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("")
    (tmp_path / "pkg" / "m.py").write_text(
        "def add(a, b):\n    return a + b\n\n\ndef neg(a):\n    return -a\n"
    )
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_m.py").write_text(
        "from pkg.m import add, neg\n\n\ndef test_add():\n    assert add(2, 3) == 5\n\n\n"
        "def test_neg():\n    assert neg(2) == -2\n\n\ndef test_known_bad():\n    assert add(1, 1) == 3\n"
    )
    return tmp_path


def test_guard_reports_only_tests_broken_by_the_change(tested):
    from devstill.runtime.tools import TestGuard

    original = {"pkg/m.py": (tested / "pkg" / "m.py").read_bytes()}
    tools = tools_for(tested)
    guard = TestGuard(tools, ["tests/test_m.py"], original.get)
    tools.edit("pkg/m.py", [{"oldText": "return -a", "newText": "return a"}])
    guard.changed()
    new, output = guard.regressions(30)
    assert new == ["tests/test_m.py::test_neg"]  # test_known_bad failed before too
    assert "test_neg" in output
    assert (tested / "pkg" / "m.py").read_text().endswith("return a\n")  # restored
    tools.edit("pkg/m.py", [{"oldText": "return a\n", "newText": "return -a\n"}])
    guard.changed()
    assert guard.regressions(30) is None
    guard.close()


def test_loop_feeds_regressions_back_before_accepting_done(tested):
    from devstill.runtime.tools import TestGuard

    original = {"pkg/m.py": (tested / "pkg" / "m.py").read_bytes()}
    tools = tools_for(tested)
    guard = TestGuard(tools, ["tests/test_m.py"], original.get)
    chat = scripted(
        reply(
            call(
                "edit",
                0,
                path="pkg/m.py",
                oldText="    return a + b",
                newText="    return abs(a + b)",
            ),
            call("edit", 1, path="pkg/m.py", oldText="return -a", newText="return a"),
            call("done", 2, summary="x"),
        ),
        reply(
            call(
                "edit", 0, path="pkg/m.py", oldText="return a\n", newText="return -a\n"
            ),
            call("done", 1, summary="fixed"),
        ),
    )
    result = agent_loop(
        tools, "Make add return absolute sums", [], chat, tested, FAR, guard=guard
    )
    assert result["status"] == "applied_unvalidated" and result["model_calls"] == 2
    feedback = chat.seen[1][-1]["content"]
    assert (
        "now fail" in feedback
        and "test_neg" in feedback
        and "test_known_bad" not in feedback
    )
    assert "Your changes so far:\n```diff\n--- a/pkg/m.py" in feedback
    assert "-    return -a\n+    return a" in feedback


def test_files_named_in_the_request_are_shown_head_and_tail(tmp_path):
    from devstill.runtime.service import code_first, named_file_records

    (tmp_path / "tests").mkdir()
    body = "import pytest\n" + "".join(
        f"\n\ndef test_case_number_{i}():\n    assert {i} == {i}, 'case {i}'\n" for i in range(200)
    )
    (tmp_path / "tests" / "test_big.py").write_text(body)
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'x'\n")
    config = DevstillConfig()
    records = named_file_records(
        tmp_path, config, "Add test_new to tests/test_big.py and bump pyproject.toml; os.path.join stays"
    )
    paths = [(r["path"], r["lines"]) for r in records]
    total = len(body.split("\n")) - 1
    assert paths[0] == ("tests/test_big.py", [1, 40])
    assert paths[1] == ("tests/test_big.py", [total - 39, total])
    assert paths[2] == ("pyproject.toml", [1, 2])
    assert records[1]["code"].endswith("assert 199 == 199, 'case 199'")
    assert code_first([], "Add it to the Sphinx API reference") == []


def test_write_refuses_to_replace_large_files_but_can_append(work):
    tools = tools_for(work)
    big = work / "pkg" / "big.py"
    big.write_text("".join(f"X{i} = {i}\n" for i in range(60)))
    with pytest.raises(ToolError, match="append=true"):
        tools.write("pkg/big.py", "Y = 1\n")
    assert big.read_text().count("\n") == 60
    result = tools.write("pkg/big.py", "Y = 1\n", append=True)
    assert "Appended to pkg/big.py (now 61 lines)" in result.text
    assert big.read_text().endswith("X59 = 59\nY = 1\n")
    small = work / "pkg" / "small.py"
    small.write_text("A = 1")  # no trailing newline; appends on a new line
    tools.write("pkg/small.py", "B = 2\n", append=True)
    assert small.read_text() == "A = 1\nB = 2\n"
    assert tools.write("pkg/small.py", "C = 3\n").ok  # small files may be replaced


def test_guard_selects_tests_for_changed_functions_only(tested):
    from devstill.runtime.loop_context import changed_symbols, guard_test_files

    original = {"pkg/m.py": (tested / "pkg" / "m.py").read_bytes()}
    (tested / "pkg" / "m.py").write_text(
        "def add(a, b):\n    return a + b\n\n\ndef neg(a):\n    return 0 - a\n"
    )
    assert changed_symbols((tested / "pkg" / "m.py").read_text(), original["pkg/m.py"].decode()) == {"neg"}
    assert guard_test_files(tested, ["pkg/m.py"], original.get) == ["tests/test_m.py"]
    (tested / "notes.toml").write_text("x = 1\n")
    assert guard_test_files(tested, ["notes.toml", "tests/test_m.py"], original.get) == []


def test_turn_limit_judges_the_latest_check_after_the_edit(work):
    edit = call("edit", 0, path="pkg/m.py", oldText="a - b", newText="a + b")
    wrong = call("python", 1, code="from pkg.m import add\nassert add(2, 3) == 6\n")
    right = call("python", 0, code="from pkg.m import add\nassert add(2, 3) == 5\n")
    chat = scripted(reply(edit, wrong), reply(right))
    result = agent_loop(tools_for(work), "Fix add", [], chat, work, FAR, max_turns=2)
    assert result["status"] == "applied_unvalidated" and result["finished"] == "turn_limit"
    assert result["verified"]


def test_weakening_note_flags_dropped_or_flipped_failed_assertions():
    from devstill.runtime.agent_loop import weakening_note

    failed = {"left_src": "f('a/b')", "right_src": "'a/b'", "op": "Eq", "left": "'a%2Fb'", "right": "'a/b'"}
    flipped = weakening_note(failed, "assert f('a/b') == 'a%2Fb'\n")
    assert "now expects that actual value" in flipped and "fix the code" in flipped
    dropped = weakening_note(failed, "assert f('x') == 'x'\n")
    assert "no longer asserts it" in dropped
    assert weakening_note(failed, "assert f('a/b') == 'a/b'\n") == ""  # kept
    assert weakening_note(None, "assert True\n") == ""
    assert weakening_note(failed, "def (:\n") == ""


def test_loop_warns_when_a_check_is_bent_to_match_wrong_code(work):
    chat = scripted(
        reply(call("python", 0, code="from pkg.m import add\nassert add(2, 3) == 5\n")),
        reply(
            call("python", 0, code="from pkg.m import add\nassert add(2, 3) == -1\n"),
        ),
        reply(
            call("edit", 0, path="pkg/m.py", oldText="a - b", newText="a + b"),
            call("python", 1, code="from pkg.m import add\nassert add(2, 3) == 5\n"),
            call("done", 2, summary="fixed"),
        ),
    )
    result = agent_loop(tools_for(work), "Fix add", [], chat, work, FAR)
    assert result["status"] == "applied_unvalidated"
    first = [m for m in chat.seen[1] if m["role"] == "tool"][-1]["content"]
    assert "left:  -1" in first and "right: 5" in first
    second = [m for m in chat.seen[2] if m["role"] == "tool"][-1]["content"]
    assert "now expects that actual value" in second


def test_guard_whole_suite_catches_unrelated_meta_tests(tested):
    from devstill.runtime.tools import TestGuard

    (tested / "tests" / "test_meta.py").write_text(
        "import pkg.m\n\n\ndef test_module_stays_small():\n"
        "    assert len(open(pkg.m.__file__).read().splitlines()) < 7\n"
    )
    original = {"pkg/m.py": (tested / "pkg" / "m.py").read_bytes()}
    tools = tools_for(tested)
    guard = TestGuard(tools, [], original.get, suite=["tests"])  # no related files
    tools.edit("pkg/m.py", [{"oldText": "return -a", "newText": "# negate\n    return -a"}])
    guard.changed()
    time.sleep(0.1)
    new, output = guard.regressions(30)
    assert new == ["tests/test_meta.py::test_module_stays_small"]
    assert "test_known_bad" not in output  # pre-existing failure filtered out
    guard.close()
