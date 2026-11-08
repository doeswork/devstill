"""Replay failed response shapes without relying on a live model or its evaluator."""

import json

import pytest
from jsonschema import Draft202012Validator

from devstill.runtime.editor import decode_response, response_schema, validate_checks


def action(*, code="import a\nassert a.x == 2", edits=None, reads=None, repairs=None):
    return {
        "edits": edits
        if edits is not None
        else [{"path": "a.py", "old": "x = 1", "new": "x = 2"}],
        "reads": reads or [],
        "checks": [{"python_lines": [code]}],
        "check_repairs": repairs or [],
    }


def trace_feedback(session, proposal):
    trace = json.loads(
        (session.root / proposal["id"] / "editor-trace.json").read_text()
    )
    return [json.loads(m["content"]) for m in trace[3:] if m.get("role") == "user"]


def test_rejected_checks_have_no_repair_indices_and_can_recover(session, monkeypatch):
    calls = []

    def generate(messages, deadline):
        calls.append(1)
        if len(calls) == 1:
            assert "check_repairs" not in response_schema(messages)["properties"]
            return json.dumps(action(code="assert False or True"))
        feedback = json.loads(messages[-1]["content"])
        assert feedback["response_checks"] == "rejected"
        assert feedback["retained_checks"] == []
        assert feedback["check_state"]["repairable_indices"] == []
        assert "No checks or edits were accepted" in feedback["next_action"]
        assert response_schema(messages)["properties"]["check_repairs"]["maxItems"] == 0
        return json.dumps(action())

    monkeypatch.setattr(session, "generate", generate)
    proposal = session.prepare("Change x to 2")
    assert proposal["status"] == "ready", proposal
    assert proposal["model_calls"] == 2


def test_nonexistent_repair_index_is_rejected_even_without_schema_enforcement(
    session, monkeypatch
):
    actions = iter(
        [
            action(code="assert True"),
            action(
                repairs=[
                    {
                        "index": 0,
                        "reason": "First assertion was invalid and never retained",
                    }
                ]
            ),
        ]
    )
    monkeypatch.setattr(session, "generate", lambda *a: json.dumps(next(actions)))
    proposal = session.prepare("Change x to 2")
    assert proposal["status"] == "failed"
    assert proposal["execution"]["failure_stage"] == "check_validation"
    assert proposal["execution"]["changed_files"] == []
    assert "retained, nonpassing" in proposal["execution"]["last_error"]


@pytest.mark.parametrize("repair", [False, True])
def test_generation_schema_requires_checks_for_edits_and_completion(repair):
    messages = [{"role": "user", "content": "Change x"}]
    if repair:
        messages += [
            {"role": "assistant", "content": "{}"},
            {
                "role": "user",
                "content": json.dumps({"check_state": {"repairable_indices": []}}),
            },
        ]
    schema = response_schema(messages)
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    response = action()
    if not repair:
        del response["check_repairs"]
    assert validator.is_valid(response)
    response["checks"] = []
    assert not validator.is_valid(response)  # Observed v6 unchecked edit.
    response["reads"] = [{"path": "a.py", "start": 1, "end": 1}]
    assert not validator.is_valid(response)  # Reads do not exempt simultaneous edits.
    response["edits"] = []
    assert validator.is_valid(response)
    response["reads"] = []
    assert not validator.is_valid(response)  # An empty completion cannot pass.

    # Reproduce providers that compile alternatives without sibling constraints.
    # Each alternative must still reject the observed malformed string checks.
    malformed = action()
    malformed["checks"] = ["import a\nassert a.x == 2"]
    if not repair:
        del malformed["check_repairs"]
    for branch in schema["anyOf"]:
        standalone = Draft202012Validator({**branch, "$defs": schema["$defs"]})
        assert not standalone.is_valid(malformed)
        assert not standalone.is_valid({})
        assert not standalone.is_valid({"checks": ["anything"]})


def test_schema_only_allows_retained_nonpassing_checks(session, monkeypatch):
    calls = []

    def generate(messages, deadline):
        calls.append(1)
        if len(calls) == 1:
            response = action()
            response["checks"].append({"python_lines": ["import a; assert a.x == 3"]})
            return json.dumps(response)
        feedback = json.loads(messages[-1]["content"])
        assert feedback["check_state"] == {
            "repairable_indices": [1],
            "passing_indices": [0],
        }
        assert response_schema(messages)["$defs"]["CheckRepair"]["properties"]["index"][
            "enum"
        ] == [1]
        return json.dumps(
            action(
                edits=[],
                repairs=[{"index": 1, "reason": "The requested value is 2, not 3"}],
            )
        )

    monkeypatch.setattr(session, "generate", generate)
    assert session.prepare("Change x to 2")["status"] == "ready"


@pytest.mark.parametrize(
    "draft",
    [
        [],
        [{"python_lines": ["assert False or True"]}],
        [{"python_lines": ["if syntax error"]}],
    ],
)
def test_read_only_ignores_draft_checks_and_preserves_response_budget(
    session, monkeypatch, draft
):
    calls = []

    def generate(messages, deadline):
        calls.append(1)
        if len(calls) == 1:
            response = action(edits=[], reads=[{"path": "a.py", "start": 1, "end": 1}])
            response["checks"] = draft
            return json.dumps(response)
        feedback = json.loads(messages[-1]["content"])
        assert feedback["reads"][0]["code"] == "x = 1"
        assert feedback["response_checks"] == "ignored_draft"
        assert feedback["retained_checks"] == []
        assert feedback["remaining_responses"] == 1
        return json.dumps(action())

    monkeypatch.setattr(session, "generate", generate)
    result = session.prepare("Change x to 2")
    assert result["status"] == "ready", result
    assert result["model_calls"] == 2


def test_final_read_cannot_complete_earlier_edits(session, monkeypatch):
    actions = iter(
        [
            action(code="import a; assert a.x == 3"),
            action(edits=[], reads=[{"path": "a.py", "start": 1, "end": 1}]),
        ]
    )
    monkeypatch.setattr(session, "generate", lambda *a: json.dumps(next(actions)))
    proposal = session.prepare("Change x to 3")
    assert proposal["status"] == "failed"
    assert proposal["execution"]["failure_stage"] == "source_reads"
    feedback = trace_feedback(session, proposal)[-1]
    assert feedback["reads"][0]["code"] == "x = 2"
    assert len(feedback["retained_checks"]) == 1
    assert (
        "checks" not in feedback
    )  # A read does not re-run checks or claim completion.


def test_read_only_still_obeys_path_guard(session, monkeypatch):
    monkeypatch.setattr(
        session,
        "generate",
        lambda *a: json.dumps(
            action(edits=[], reads=[{"path": "../secret.py", "start": 1, "end": 1}])
        ),
    )
    result = session.prepare("Read missing context")
    assert result["status"] == "failed"
    assert all("reads" not in f for f in trace_feedback(session, result))


def test_multiline_checks_preserve_literal_escapes():
    response = decode_response(
        action(code="value = 'a\\nb'\nassert value.splitlines() == ['a', 'b']")
    )
    validate_checks(response["checks"])
    assert "'a\\nb'" in response["checks"][0][2]
    exec(response["checks"][0][2], {})  # noqa: S102 - fixed test code
    with pytest.raises(SyntaxError):
        validate_checks(
            decode_response(action(code="value = 2\\nassert value == 2"))["checks"]
        )


@pytest.mark.parametrize(
    "code",
    ["# padding\n" * 120 + "assert 1 == 1", "#" + "x" * 24000 + "\nassert 1 == 1"],
)
def test_multiline_limits_apply_to_resulting_source_before_writes(
    session, monkeypatch, code
):
    monkeypatch.setattr(session, "generate", lambda *a: json.dumps(action(code=code)))
    result = session.prepare("Change x to 2")
    assert result["status"] == "failed"
    assert result["execution"]["changed_files"] == []
    assert "120 source lines" in result["execution"]["last_error"]


def test_protocol_repair_does_not_make_wrong_url_encoding_pass(session, monkeypatch):
    source = """from urllib.parse import quote
def encode(value):
    return quote(str(value), safe="!$&'()*+,/:;=@")
"""
    (session.repo / "a.py").write_text(source)
    monkeypatch.setattr(
        session,
        "evidence",
        lambda query: [{"path": "a.py", "code": source, "lines": [1, 3]}],
    )
    code = "import a\nassert a.encode('x+y/z') == 'x%2By/z'"
    wrong = action(
        code=code,
        edits=[
            {
                "path": "a.py",
                "old": 'safe="!$&\'()*+,/:;=@"',
                "new": 'safe="!$&\'()*+,/:;=@", encoding=None',
            }
        ],
    )
    actions = iter([wrong, action(code=code, edits=[])])
    monkeypatch.setattr(session, "generate", lambda *a: json.dumps(next(actions)))
    result = session.prepare("Encode plus as %2B while preserving slash")
    assert result["status"] == "failed"
    assert result["execution"]["failure_stage"] == "check_execution"
    assert all(
        f["response_checks"] == "retained" for f in trace_feedback(session, result)
    )
    assert (session.repo / "a.py").read_text() == source


def test_runtime_delivery_check_has_no_model_repair_index(session, monkeypatch):
    calls = []

    def generate(messages, deadline):
        calls.append(1)
        if len(calls) == 1:
            return json.dumps(action())
        feedback = json.loads(messages[-1]["content"])
        assert feedback["failure_stage"] == "delivery_verification"
        assert feedback["check_state"] == {
            "repairable_indices": [],
            "passing_indices": [0],
        }
        assert response_schema(messages)["properties"]["check_repairs"]["maxItems"] == 0
        return json.dumps(action(edits=[]))

    monkeypatch.setattr(session, "generate", generate)
    result = session.prepare("Change x to 2 and create b.py")
    assert result["status"] == "failed"
    assert result["execution"]["failure_stage"] == "delivery_verification"


def checks_action(*codes, edits=None, repairs=None):
    response = action(edits=edits, repairs=repairs)
    response["checks"] = [{"python_lines": [code]} for code in codes]
    return response


def test_corrected_check_replaces_failing_check_without_repair_index(
    session, monkeypatch
):
    # Correct patch, wrong expectation; the fix resubmits the check without an
    # index. Previously the wrong check was retained and failed the run.
    actions = iter(
        [
            action(code="import a; assert a.x == 3"),
            action(code="import a; assert a.x == 2", edits=[]),
        ]
    )
    monkeypatch.setattr(session, "generate", lambda *a: json.dumps(next(actions)))
    proposal = session.prepare("Change x to 2")
    assert proposal["status"] == "ready", proposal
    execution = proposal["execution"]
    assert execution["replaced_checks"] == [
        ["python", "-c", "import a; assert a.x == 3"]
    ]
    feedback = trace_feedback(session, proposal)[0]
    assert feedback["check_state"]["repairable_indices"] == [0]


def test_implementation_only_repair_reruns_retained_checks(session, monkeypatch):
    calls = []

    def generate(messages, deadline):
        calls.append(1)
        if len(calls) == 1:
            return json.dumps(
                action(edits=[{"path": "a.py", "old": "x = 1", "new": "x = 3"}])
            )
        repair = {
            "edits": [{"path": "a.py", "old": "x = 3", "new": "x = 2"}],
            "reads": [],
            "checks": [],
            "check_repairs": [],
        }
        validator = Draft202012Validator(response_schema(messages))
        assert validator.is_valid(repair)
        assert not validator.is_valid({**repair, "edits": []})  # No empty no-op.
        assert not validator.is_valid(
            {
                **repair,
                "check_repairs": [
                    {"index": 0, "reason": "Drop it without a replacement"}
                ],
            }
        )
        return json.dumps(repair)

    monkeypatch.setattr(session, "generate", generate)
    proposal = session.prepare("Change x to 2")
    assert proposal["status"] == "ready", proposal
    assert proposal["model_calls"] == 2
    assert [c["command"][-1] for c in proposal["execution"]["checks"]] == [
        "import a\nassert a.x == 2"
    ]
    assert proposal["execution"]["replaced_checks"] == []


def test_first_edit_without_checks_is_rejected_at_runtime(session, monkeypatch):
    first = action()
    first["checks"] = []
    actions = iter([first, first])
    monkeypatch.setattr(session, "generate", lambda *a: json.dumps(next(actions)))
    proposal = session.prepare("Change x to 2")
    assert proposal["status"] == "failed"
    assert proposal["execution"]["changed_files"] == []
    assert "1..3 behavior checks" in proposal["execution"]["last_error"]


def test_retiring_a_check_without_replacement_is_rejected(session, monkeypatch):
    actions = iter(
        [
            action(code="import a; assert a.x == 3"),
            {
                "edits": [{"path": "a.py", "old": "x = 2", "new": "x = 4"}],
                "reads": [],
                "checks": [],
                "check_repairs": [{"index": 0, "reason": "Expectation was not wanted"}],
            },
        ]
    )
    monkeypatch.setattr(session, "generate", lambda *a: json.dumps(next(actions)))
    proposal = session.prepare("Change x")
    assert proposal["status"] == "failed"
    assert "requires replacement checks" in proposal["execution"]["last_error"]


def test_check_for_missing_api_is_reported_not_gating(session, monkeypatch):
    actions = iter(
        [checks_action("import a\nassert a.x == 2", "import a\nassert a.strong(1)")]
    )
    monkeypatch.setattr(session, "generate", lambda *a: json.dumps(next(actions)))
    proposal = session.prepare("Change x to 2")
    assert proposal["status"] == "ready", proposal
    assert proposal["model_calls"] == 1
    (invalid,) = proposal["execution"]["invalid_checks"]
    assert invalid["error"] == "AttributeError: module 'a' has no attribute 'strong'"
    assert "x = 2" in proposal["patch"]  # Baseline swap restored the patched source.


def test_only_invalid_checks_cannot_complete(session, monkeypatch):
    invalid = checks_action("from a import missing\nassert missing() == 2")
    actions = iter([invalid, checks_action("import a\nassert a.nope == 2", edits=[])])
    monkeypatch.setattr(session, "generate", lambda *a: json.dumps(next(actions)))
    proposal = session.prepare("Change x to 2")
    assert proposal["status"] == "failed"
    assert proposal["execution"]["failure_stage"] == "check_execution"
    feedback = trace_feedback(session, proposal)[0]
    assert "none tests this patch" in feedback["error"]
    assert feedback["retained_checks"] == []


def test_missing_api_caused_by_the_patch_still_gates(session, monkeypatch):
    # The original source has x; only the patch removes it. Not an invalid check.
    patch = [{"path": "a.py", "old": "x = 1", "new": "y = 2"}]
    actions = iter(
        [
            checks_action(
                "import a\nassert a.y == 2", "import a\nassert a.x == 1", edits=patch
            ),
            checks_action("import a\nassert a.y == 2", edits=[]),
        ]
    )
    monkeypatch.setattr(session, "generate", lambda *a: json.dumps(next(actions)))
    proposal = session.prepare("Add y without removing x")
    assert proposal["status"] == "failed"
    assert proposal["execution"]["failure_stage"] == "check_execution"
    assert all("invalid_checks" not in f for f in trace_feedback(session, proposal))


def test_baseline_run_restores_created_files(session, monkeypatch):
    edits = [{"path": "b.py", "old": "", "new": "y = 2\n"}]
    actions = iter(
        [
            checks_action(
                "import b\nassert b.y == 2", "import a\nassert a.gone", edits=edits
            )
        ]
    )
    monkeypatch.setattr(session, "generate", lambda *a: json.dumps(next(actions)))
    proposal = session.prepare("Add module with y")
    assert proposal["status"] == "ready", proposal
    assert len(proposal["execution"]["invalid_checks"]) == 1
    assert "+y = 2" in proposal["patch"]  # Created file survived the baseline run.


def test_else_raise_assertion_error_counts_as_behavior_check():
    code = "try:\n    int('a')\nexcept ValueError:\n    pass\nelse:\n    raise AssertionError('accepted')"
    validate_checks([["python", "-c", code]])
    validate_checks([["python", "-c", "x = 1\nif x != 1:\n    raise AssertionError"]])
    with pytest.raises(ValueError, match="behavioral assertion"):
        validate_checks([["python", "-c", "raise ValueError('not a check')"]])
