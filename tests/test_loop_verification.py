"""How the Quick loop judges the agent's own checks (no model needed).

Covers the check runner's per-assert records, comparing a failing check with
the original code, which commands count as checks, finishing automatically
after a proving check, and when a failing check may not block a finish.
"""

import json

import pytest

from devstill.runtime.agent_loop import (
    EXAMPLE,
    TOOLS,
    agent_loop,
    normalize_edits,
    weakening_note,
)
from devstill.runtime.tools import check_failure_line

from .test_agent_loop import FAR, call, reply, scripted, tools_for


@pytest.fixture
def work(tmp_path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("")
    (tmp_path / "pkg" / "m.py").write_text("def add(a, b):\n    return a - b\n")
    return tmp_path


def originals_of(root):
    saved = {"pkg/m.py": (root / "pkg" / "m.py").read_bytes()}
    return saved.get


def edit(old, new, index=0):
    return call(
        "edit", index, path="pkg/m.py", edits=[{"oldText": old, "newText": new}]
    )


def tool_results(chat, turn):
    return [
        m["content"]
        for m in chat.seen[turn]
        if m["role"] == "tool" and not m["tool_call_id"].startswith("example")
    ]


# --- the check runner ---------------------------------------------------------


def test_runner_reports_every_failing_assert_with_values(work):
    result = tools_for(work).python(
        "from pkg.m import add\n"
        "assert add(2, 3) == 5\n"
        "assert add(1, 1) == 0\n"
        "assert add(4, 1) == 5, 'four'\n",
        10,
    )
    assert not result.ok
    text = result.text
    assert "check line 2: assert add(2, 3) == 5\n  left:  -1\n  right: 5" in text
    assert "check line 4" in text and "message: four" in text and "left:  3" in text
    assert "[check failed at line 2: 2 failed assert(s)]" in text
    assert check_failure_line(text) == 2
    record = result.meta["record"]
    assert record["asserts"] == {"2": [1, 1], "3": [1, 0], "4": [1, 1]}
    assert record["first_line"] == 2 and record["stop"] is None
    assert result.meta["failed_assert"]["line"] == 2


def test_runner_keeps_names_bound_in_except_and_the_true_failing_line(work):
    result = tools_for(work).python(
        "import json\n"
        "try:\n"
        "    json.loads('{')\n"
        "except ValueError as e:\n"
        "    assert 'Expecting' not in str(e)\n",
        10,
    )
    assert check_failure_line(result.text) == 5  # not the try-body line 3
    assert "NameError" not in result.text  # operands captured while `e` existed
    assert "right: 'Expecting property name" in result.text


def test_runner_names_the_library_signature_a_check_misused(work):
    result = tools_for(work).python("import json\njson.loads(123)\n", 10)
    assert "[called on check line 2: json.loads(s, *, cls=None" in result.text
    stop = result.meta["record"]["stop"]
    assert stop == {
        "line": 2,
        "inner_line": 2,
        "type": "TypeError",
        "message": stop["message"],
        "in_check": False,
    }
    own = tools_for(work).python("import pkg.m\npkg.m.missing()\n", 10)
    assert "raised by the check script itself (line 2)" in own.text
    assert own.meta["record"]["stop"]["in_check"] is True


def test_asserts_inside_functions_still_stop_the_check(work):
    result = tools_for(work).python(
        "from pkg.m import add\n"
        "def check(a, b, want):\n"
        "    assert add(a, b) == want\n"
        "check(2, 3, 5)\n"
        "print('not reached')\n",
        10,
    )
    assert "not reached" not in result.text and "add(a, b) = -1" in result.text
    assert result.meta["record"]["stop"]["line"] == 4


# --- comparing a failing check with the original code ---------------------------


def test_a_failing_check_is_compared_with_the_original_code(work):
    chat = scripted(
        reply(
            edit("a - b", "a + b + 1"),
            call(
                "python",
                1,
                code="from pkg.m import add\nassert add(0, 0) == 0\nassert add(2, 3) == 5\n",
            ),
        ),
        reply(edit("a + b + 1", "a + b"), call("done", 1, summary="x")),
    )
    agent_loop(
        tools_for(work), "Fix add", [], chat, work, FAR, originals=originals_of(work)
    )
    verdict = tool_results(chat, 1)[-1]
    assert "Compared with the original code" in verdict
    assert "line 2 held on the original code and fail now" in verdict
    # add(2, 3) was -1 originally and is 6 now: reached, still wrong.
    assert "line 3 also failed on the original code, with other values" in verdict


def test_progress_does_not_count_when_the_change_breaks_what_held(work):
    check = (
        "from pkg.m import add\n"
        "assert add(2, 3) == 5\n"  # requested: fails originally, holds now
        "assert add(0, 0) == 0\n"  # held originally, broken by the change
        "assert add(1, 1) == 3\n"  # a wrong expectation on both versions
    )
    result = agent_loop(
        tools_for(work),
        "Fix add",
        [],
        scripted(
            reply(edit("a - b", "a + b if a else 7"), call("python", 1, code=check))
        ),
        work,
        FAR,
        max_turns=1,
        originals=originals_of(work),
    )
    # The first failure moved from line 2 to line 3, but line 3 held before.
    assert result["status"] == "failed" and result["failure_stage"] == "turn_limit"


def test_progress_ignores_an_original_that_only_lacked_the_new_api(work):
    check = "from pkg.m import add\nr = add(2, 3, scale=2)\nassert r == 10\n"
    result = agent_loop(
        tools_for(work),
        "Add a scale argument",
        [],
        scripted(
            reply(
                edit(
                    "def add(a, b):\n    return a - b",
                    "def add(a, b, scale=1):\n    return (a - b) * scale",
                ),
                call("python", 1, code=check),
            )
        ),
        work,
        FAR,
        max_turns=1,
        originals=originals_of(work),
    )
    # Originally the check stopped at line 2 (unexpected keyword); now it
    # fails at line 3. That is not progress: the original never ran it.
    assert result["status"] == "failed"


def test_progress_still_counts_when_only_a_wrong_expectation_remains(work):
    check = "from pkg.m import add\nassert add(2, 3) == 5\nassert add(1, 1) == 3\n"
    result = agent_loop(
        tools_for(work),
        "Fix add",
        [],
        scripted(reply(edit("a - b", "a + b"), call("python", 1, code=check))),
        work,
        FAR,
        max_turns=1,
        originals=originals_of(work),
    )
    assert result["status"] == "applied_unvalidated"


# --- which commands count as checks ----------------------------------------------


def test_print_only_probes_do_not_verify_a_change(work):
    probe = "python - <<'EOF'\nfrom pkg.m import add\nprint(add(2, 3))\nEOF"
    result = agent_loop(
        tools_for(work),
        "Fix add",
        [],
        scripted(
            reply(
                edit("a - b", "a * b"),
                call(
                    "python", 1, code="from pkg.m import add\nassert add(2, 3) == 5\n"
                ),
            ),
            reply(call("bash", 0, command=probe)),
        ),
        work,
        FAR,
        max_turns=2,
        originals=originals_of(work),
    )
    # The probe printed 6 and passed, but the latest real check still fails.
    assert result["status"] == "failed"
    assert [c.get("check") for c in result["commands"]] == [True, False]


def test_a_timed_out_test_run_is_refused_once_but_cannot_veto_at_the_limit(work):
    (work / "tests").mkdir()
    (work / "tests" / "test_slow.py").write_text(
        "import time\n\n\ndef test_slow():\n    time.sleep(30)\n"
    )
    hang = call("bash", 1, command="python -m pytest -q tests/test_slow.py")
    result = agent_loop(
        tools_for(work),
        "Fix add",
        [],
        scripted(reply(edit("a - b", "a + b"), hang)),
        work,
        FAR,
        max_turns=1,
        bash_timeout=1.5,
    )
    # At the turn limit a test run that never finished says nothing.
    assert result["status"] == "applied_unvalidated" and not result["verified"]
    last = result["commands"][-1]
    assert last["timed_out"] and "did not finish" in last["output"]
    (work / "pkg" / "m.py").write_text("def add(a, b):\n    return a - b\n")
    chat = scripted(
        reply(edit("a - b", "a + b"), hang),
        reply(call("done", 0, summary="x")),
        reply(call("done", 0, summary="x")),
    )
    agent_loop(tools_for(work), "Fix add", [], chat, work, FAR, bash_timeout=1.5)
    assert "did not finish" in tool_results(chat, 2)[-1]  # refused once at done


# --- finishing ------------------------------------------------------------------


def test_a_proving_check_finishes_without_calling_done(work):
    chat = scripted(
        reply(
            edit("a - b", "a + b"),
            call("python", 1, code="from pkg.m import add\nassert add(2, 3) == 5\n"),
            content="Fixed the operator.",
        )
    )
    result = agent_loop(
        tools_for(work), "Fix add", [], chat, work, FAR, originals=originals_of(work)
    )
    assert result["status"] == "applied_unvalidated" and result["model_calls"] == 1
    assert result["finished"] == "verified_check" and result["verified"]
    assert result["summary"] == "Fixed the operator."


def test_no_automatic_finish_for_a_check_that_passes_on_the_original(work):
    chat = scripted(
        reply(
            edit("a - b", "a + b"),
            call("python", 1, code="from pkg.m import add\nassert add(0, 0) == 0\n"),
        ),
        reply(call("done", 0, summary="x")),
        reply(
            call("python", 0, code="from pkg.m import add\nassert add(2, 3) == 5\n"),
            call("done", 1, summary="fixed"),
        ),
    )
    result = agent_loop(
        tools_for(work), "Fix add", [], chat, work, FAR, originals=originals_of(work)
    )
    assert result["model_calls"] == 3
    assert "also passes on the original code" in tool_results(chat, 2)[-1]


def test_no_automatic_finish_after_bending_an_assertion_that_held(work):
    chat = scripted(
        reply(
            edit("a - b", "a + b + 1"),
            call("python", 1, code="from pkg.m import add\nassert add(0, 0) == 0\n"),
        ),
        reply(call("python", 0, code="from pkg.m import add\nassert add(0, 0) == 1\n")),
        reply(call("done", 0, summary="x")),
    )
    result = agent_loop(
        tools_for(work), "Fix add", [], chat, work, FAR, originals=originals_of(work)
    )
    warned = tool_results(chat, 2)[-1]
    assert "now expects that actual value" in warned
    assert result["model_calls"] == 3  # the bent check did not end the task


def test_weakening_warning_is_kept_when_the_change_reached_the_assertion(work):
    chat = scripted(
        reply(
            edit("a - b", "a + b"),
            # add(1, 1) was 0 originally and is 2 now: the change reached it.
            call(
                "python",
                1,
                code="from pkg.m import add\nassert add(2, 3) == 5\nassert add(1, 1) == 3\n",
            ),
        ),
        reply(call("python", 0, code="from pkg.m import add\nassert add(2, 3) == 5\n")),
        reply(call("done", 0, summary="x")),
    )
    result = agent_loop(
        tools_for(work), "Fix add", [], chat, work, FAR, originals=originals_of(work)
    )
    assert "no longer asserts it" in tool_results(chat, 2)[-1]
    assert (
        result["model_calls"] == 3
    )  # a check that dropped an assertion: no auto-finish


def test_weakening_warning_text_is_dropped_for_an_assertion_the_change_never_reached(
    work,
):
    chat = scripted(
        reply(
            edit("a - b", "a + b"),
            # add(5, 0) is 5 on both versions: a wrong expectation.
            call(
                "python",
                1,
                code="from pkg.m import add\nassert add(2, 3) == 5\nassert add(5, 0) == 6\n",
            ),
        ),
        reply(call("python", 0, code="from pkg.m import add\nassert add(2, 3) == 5\n")),
        reply(call("done", 0, summary="x")),
    )
    result = agent_loop(
        tools_for(work), "Fix add", [], chat, work, FAR, originals=originals_of(work)
    )
    assert "Warning" not in tool_results(chat, 2)[-1]
    # It still dropped a failing assertion, so the model has to call done.
    assert result["model_calls"] == 3 and result["status"] == "applied_unvalidated"


def test_discrimination_judges_the_agent_check_even_after_a_test_run(work):
    (work / "tests").mkdir()
    (work / "tests" / "test_ok.py").write_text("def test_ok():\n    assert True\n")
    chat = scripted(
        reply(
            edit("a - b", "a + b"),
            call("python", 1, code="from pkg.m import add\nassert add(0, 0) == 0\n"),
            call("bash", 2, command="python -m pytest -q tests/test_ok.py"),
            call("done", 3, summary="x"),
        ),
        reply(call("done", 0, summary="x")),
    )
    result = agent_loop(
        tools_for(work), "Fix add", [], chat, work, FAR, originals=originals_of(work)
    )
    assert "also passes on the original code" in tool_results(chat, 1)[-1]
    assert result["model_calls"] == 2


@pytest.fixture
def tested(tmp_path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("")
    (tmp_path / "pkg" / "m.py").write_text("def add(a, b):\n    return a - b\n")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_m.py").write_text(
        "from pkg.m import add\n\n\ndef test_zero():\n    assert add(0, 0) == 0\n\n\n"
        "def test_known_bad():\n    assert add(1, 1) == 3\n"
    )
    return tmp_path


def test_failing_tests_that_also_fail_originally_are_named_when_done_is_refused(
    tested,
):
    from devstill.runtime.tools import TestGuard

    tools = tools_for(tested)
    original = originals_of(tested)
    guard = TestGuard(tools, ["tests/test_m.py"], original)
    chat = scripted(
        reply(
            edit("a - b", "a + b"),
            call("bash", 1, command="python -m pytest -q tests/test_m.py"),
        ),
        reply(call("done", 0, summary="fixed")),
        reply(call("done", 0, summary="fixed")),
    )
    result = agent_loop(
        tools, "Fix add", [], chat, tested, FAR, guard=guard, originals=original
    )
    # test_known_bad fails before and after the change; the request might
    # still be about it, so done is refused once with that information.
    refusal = tool_results(chat, 2)[-1]
    assert "also fail on the original code: tests/test_m.py::test_known_bad" in refusal
    assert result["status"] == "applied_unvalidated" and result["model_calls"] == 3


def test_done_is_refused_once_when_a_test_run_shows_a_new_failure(tested):
    from devstill.runtime.tools import TestGuard

    tools = tools_for(tested)
    original = originals_of(tested)
    guard = TestGuard(tools, ["tests/test_m.py"], original)
    chat = scripted(
        reply(
            edit("a - b", "a + b + 1"),
            call("bash", 1, command="python -m pytest -q tests/test_m.py"),
        ),
        reply(call("done", 0, summary="x")),
        reply(edit("a + b + 1", "a + b"), call("done", 1, summary="fixed")),
    )
    result = agent_loop(
        tools, "Fix add", [], chat, tested, FAR, guard=guard, originals=original
    )
    # test_zero passed on the original and fails now: a real regression.
    assert "your latest check failed" in tool_results(chat, 2)[-1]
    assert result["status"] == "applied_unvalidated" and result["model_calls"] == 3


# --- small fixes ------------------------------------------------------------------


def test_weakening_note_shows_the_container_for_membership_asserts():
    failed = {
        "left_src": "'hidden'",
        "right_src": "msg",
        "op": "NotIn",
        "left": "'hidden'",
        "right": "'Did you mean --hidden?'",
        "line": 3,
    }
    note = weakening_note(failed, "assert 'x' in msg\n")
    assert "msg was 'Did you mean --hidden?'" in note
    assert "actual value 'hidden'" not in note


def test_edit_tool_advertises_multi_block_edits():
    edit_tool = next(t for t in TOOLS if t["function"]["name"] == "edit")
    parameters = edit_tool["function"]["parameters"]
    assert parameters["required"] == ["path", "edits"]
    assert parameters["properties"]["edits"]["items"]["required"] == [
        "oldText",
        "newText",
    ]
    example = json.loads(EXAMPLE[1]["tool_calls"][0]["function"]["arguments"])
    assert normalize_edits(example) == example["edits"]


# --- review fixes: robustness of the runner and of finishing early ------------------


def test_a_failing_assert_still_guards_the_statements_after_its_run(work):
    marker = work / "deleted"
    result = tools_for(work).python(
        "from pkg.m import add\n"
        "assert add(2, 3) == 5\n"  # fails, but the next statement is an assert
        "assert add(0, 0) == 0\n"  # passes; the run of asserts ends here
        f"open({str(marker)!r}, 'w')\n"  # guarded by the run above
        "assert add(1, 1) == 2\n",
        10,
    )
    assert not result.ok and not marker.exists()
    assert result.meta["record"]["asserts"] == {"2": [1, 1], "3": [1, 0]}


def test_guard_asserts_in_loops_stop_at_the_first_failure(work):
    result = tools_for(work).python(
        "from pkg.m import add\n"
        "total = i = 0\n"
        "while total != 10:\n"
        "    i += 1\n"
        "    assert i < 50, i\n"
        "    total = add(total, 1)\n",
        10,
    )
    assert not result.ok and "[check failed at line 5" in result.text
    assert result.meta["record"]["asserts"] == {"5": [50, 1]}


@pytest.mark.parametrize(
    "ending",
    ["import os\nos._exit(0)\n", "import sys\nsys.exit = print\n"],
)
def test_failed_asserts_cannot_be_hidden_by_how_the_script_exits(work, ending):
    result = tools_for(work).python(
        "from pkg.m import add\nassert add(2, 3) == 5\nassert True\n" + ending, 10
    )
    assert not result.ok


def test_code_under_test_exiting_early_fails_the_check(work):
    (work / "pkg" / "cli.py").write_text(
        "import sys\n\n\ndef main():\n    print('tool 1.0')\n    sys.exit(0)\n"
    )
    early = tools_for(work).python(
        "from pkg.cli import main\nmain()\nassert False, 'never ran'\n", 10
    )
    assert not early.ok and "the rest of the check never ran" in early.text
    last = tools_for(work).python("from pkg.cli import main\nmain()\n", 10)
    assert last.ok  # exiting at the last statement is a normal end


def test_checks_run_as_a_real_main_module(work):
    result = tools_for(work).python(
        "import unittest\n"
        "class T(unittest.TestCase):\n"
        "    def test_x(self):\n"
        "        self.assertEqual(1, 1)\n"
        "if __name__ == '__main__':\n"
        "    unittest.main()\n",
        10,
    )
    assert result.ok and "Ran 1 test" in result.text


def test_plain_asserts_where_the_script_expects_assertion_errors(work):
    result = tools_for(work).python(
        "import contextlib\n"
        "with contextlib.suppress(AssertionError):\n"
        "    assert 1 == 2\n"
        "n = 0\n"
        "for _ in range(5):\n"
        "    try:\n"
        "        n += 1\n"
        "        assert n >= 3\n"
        "        break\n"
        "    except AssertionError:\n"
        "        pass\n"
        "assert n == 3, n\n",
        10,
    )
    assert result.ok


def test_no_automatic_finish_unless_the_proving_check_ends_the_response(work):
    chat = scripted(
        reply(
            edit("a - b", "a + b"),
            call("python", 1, code="from pkg.m import add\nassert add(2, 3) == 5\n"),
            call("bash", 2, command="grep -n return pkg/m.py"),  # still exploring
        ),
        reply(call("done", 0, summary="fixed")),
    )
    result = agent_loop(
        tools_for(work), "Fix add", [], chat, work, FAR, originals=originals_of(work)
    )
    assert result["model_calls"] == 2


def test_no_automatic_finish_after_a_failed_check_since_the_edit(work):
    chat = scripted(
        reply(
            edit("a - b", "a + b"),
            call("python", 1, code="from pkg.m import add\nassert add(2, 3) == 6\n"),
        ),
        reply(call("python", 0, code="from pkg.m import add\nassert add(2, 3) == 5\n")),
        reply(call("done", 0, summary="fixed")),
    )
    result = agent_loop(
        tools_for(work), "Fix add", [], chat, work, FAR, originals=originals_of(work)
    )
    assert result["model_calls"] == 3


def test_failing_python_commands_still_block_done_even_if_they_only_print(work):
    chat = scripted(
        reply(
            edit("a - b", "a + b"),
            call("bash", 1, command="python -c 'from pkg.m import missing; print(1)'"),
            call("done", 2, summary="x"),
        ),
        reply(call("done", 0, summary="x")),
        reply(call("done", 0, summary="x")),
    )
    result = agent_loop(tools_for(work), "Fix add", [], chat, work, FAR)
    assert "your latest check failed" in tool_results(chat, 2)[-1]
    assert not result["verified"]


def test_digest_is_the_same_for_a_value_and_its_json_copy():
    from devstill.runtime.artifacts import digest

    value = {"asserts": {9: [1, 0], 10: [1, 1]}, "pair": (1, 2)}
    assert digest(value) == digest(json.loads(json.dumps(value)))
