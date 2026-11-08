import json
import os
import sys
import time
from pathlib import Path

import pytest

from benchmarks.retrieval.minimal_editor import (
    EditResponse,
    apply_edits,
    edit_loop,
    read_sources,
    run_checks,
    validate_checks,
)


@pytest.fixture(autouse=True)
def prepared_python(monkeypatch):
    # Match the benchmark's prepared interpreter, without relying on host shims.
    monkeypatch.setenv("PATH", str(Path(sys.executable).parent), prepend=os.pathsep)


def test_batch_rejects_ambiguous_match_without_partial_write(tmp_path):
    path = tmp_path / "a.py"
    path.write_text("x = 1\ny = 1\n")
    with pytest.raises(ValueError, match="exactly once"):
        apply_edits(
            tmp_path,
            [
                {"path": "a.py", "old": "x = 1", "new": "x = 2"},
                {"path": "a.py", "old": "missing", "new": "z"},
            ],
        )
    assert path.read_text() == "x = 1\ny = 1\n"


def test_rejects_syntax_error_before_write_and_escaping_path(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n")
    with pytest.raises(SyntaxError):
        apply_edits(tmp_path, [{"path": "a.py", "old": "x = 1", "new": "x = ("}])
    assert (tmp_path / "a.py").read_text() == "x = 1\n"
    (tmp_path / "escape").symlink_to(tmp_path.parent, target_is_directory=True)
    for name in ("../out.py", "escape/out.py", ".git/config"):
        with pytest.raises(ValueError):
            apply_edits(tmp_path, [{"path": name, "old": "", "new": "x = 1"}])


def test_large_file_read_starts_at_requested_line(tmp_path):
    (tmp_path / "a.py").write_text("\n".join(str(i) for i in range(1, 1100)))
    assert (
        read_sources(tmp_path, [{"path": "a.py", "start": 1067, "end": 1068}])[0][
            "code"
        ]
        == "1067\n1068"
    )


def test_failed_check_can_repair_current_files_once(tmp_path, monkeypatch):
    (tmp_path / "a.py").write_text("x = 1\n")
    actions = iter(
        [
            {
                "edits": [{"path": "a.py", "old": "x = 1", "new": "x = 2"}],
                "reads": [],
                "checks": [["python", "-c", "assert False"]],
            },
            {
                "edits": [{"path": "a.py", "old": "x = 2", "new": "x = 3"}],
                "reads": [],
                "checks": [["python", "-c", "from a import x; assert x == 3"]],
            },
        ]
    )
    checks = iter([[{"returncode": 1}], [{"returncode": 0}]])
    monkeypatch.setattr(
        "benchmarks.retrieval.minimal_editor.run_checks", lambda *a: next(checks)
    )

    def generate(messages, deadline):
        if len(messages) > 2:
            assert '"returncode": 1' in messages[-1]["content"]
            assert (tmp_path / "a.py").read_text() == "x = 2\n"
        return json.dumps(next(actions))

    result = edit_loop(
        tmp_path, "request", [], generate, tmp_path, time.perf_counter() + 10
    )
    assert result["status"] == "applied_unvalidated" and result["model_calls"] == 2
    assert (tmp_path / "a.py").read_text() == "x = 3\n"


def test_empty_or_invalid_edits_cannot_complete_and_retry_is_bounded(tmp_path):
    calls = []

    def generate(*args):
        calls.append(1)
        return '{"edits": [], "checks": []}'

    result = edit_loop(
        tmp_path, "request", [], generate, tmp_path, time.perf_counter() + 10
    )
    assert result["status"] == "failed" and len(calls) == 2


def test_checks_only_repair_keeps_applied_change(tmp_path, monkeypatch):
    (tmp_path / "a.py").write_text("x = 1\n")
    actions = iter(
        [
            {
                "edits": [{"path": "a.py", "old": "x = 1", "new": "x = 2"}],
                "reads": [{"path": "a.py", "start": 1, "end": 1}],
                "checks": [["python", "-c", "assert False"]],
            },
            {
                "edits": [],
                "reads": [],
                "checks": [["python", "-c", "from a import x; assert x == 2"]],
            },
        ]
    )
    results = iter([[{"returncode": 1}], [{"returncode": 0}]])
    monkeypatch.setattr(
        "benchmarks.retrieval.minimal_editor.run_checks", lambda *a: next(results)
    )
    result = edit_loop(
        tmp_path,
        "request",
        [],
        lambda *a: json.dumps(next(actions)),
        tmp_path,
        time.perf_counter() + 10,
    )
    assert result["status"] == "applied_unvalidated"
    assert result["model_calls"] == 2
    assert (tmp_path / "a.py").read_text() == "x = 2\n"


def test_missing_checks_rejected_before_write_then_explicitly_repaired(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n")
    calls = []

    def generate(messages, deadline):
        calls.append(1)
        action = {
            "edits": [{"path": "a.py", "old": "x = 1", "new": "x = 2"}],
            "reads": [],
            "checks": [],
        }
        if len(calls) == 2:
            feedback = json.loads(messages[-1]["content"].split("\n")[0])
            assert "Missing behavior checks" in feedback["error"]
            assert not feedback["edits_applied"]
            assert (tmp_path / "a.py").read_text() == "x = 1\n"
            action["checks"] = [["python", "-c", "from a import x; assert x == 2"]]
        return json.dumps(action)

    result = edit_loop(
        tmp_path, "set x to two", [], generate, tmp_path, time.perf_counter() + 10
    )
    assert result["status"] == "applied_unvalidated"
    assert result["model_calls"] == 2
    assert (tmp_path / "a.py").read_text() == "x = 2\n"


def test_bad_check_syntax_does_not_apply_edits_and_can_be_repaired(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n")
    calls = []

    def generate(messages, deadline):
        calls.append(1)
        if len(calls) == 2:
            assert '"error_type": "SyntaxError"' in messages[-1]["content"]
            assert (tmp_path / "a.py").read_text() == "x = 1\n"
        code = (
            "from a import x; assert x == 2"
            if len(calls) == 2
            else "from a import x\\nassert x == 2"
        )
        return json.dumps(
            {
                "edits": [{"path": "a.py", "old": "x = 1", "new": "x = 2"}],
                "reads": [],
                "checks": [["python", "-c", code]],
            }
        )

    result = edit_loop(
        tmp_path, "request", [], generate, tmp_path, time.perf_counter() + 10
    )
    assert result["status"] == "applied_unvalidated"


def test_real_name_error_gets_checks_only_repair_without_reapplying_patch(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n")
    calls = []

    def generate(messages, deadline):
        calls.append(1)
        if len(calls) == 1:
            return json.dumps(
                {
                    "edits": [{"path": "a.py", "old": "x = 1", "new": "x = 2"}],
                    "reads": [],
                    "checks": [
                        [
                            "python",
                            "-c",
                            "from a import x; assert x == 2; missing_name()",
                        ]
                    ],
                }
            )
        feedback = json.loads(messages[-1]["content"].split("\n")[0])
        assert "NameError" in feedback["checks"][0]["output"]
        assert feedback["edits_applied"]
        assert "checks-only" in feedback["next_action"]
        assert (tmp_path / "a.py").read_text() == "x = 2\n"
        return json.dumps(
            {
                "edits": [],
                "reads": [],
                "checks": [["python", "-c", "from a import x; assert x == 2"]],
            }
        )

    result = edit_loop(
        tmp_path, "request", [], generate, tmp_path, time.perf_counter() + 10
    )
    assert result["status"] == "applied_unvalidated" and result["model_calls"] == 2


def test_read_only_response_defers_checks_until_source_is_edited(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n")
    calls = []
    check = [["python", "-c", "from a import x; assert x == 2"]]

    def generate(messages, deadline):
        calls.append(1)
        if len(calls) == 1:
            return json.dumps(
                {
                    "edits": [],
                    "reads": [{"path": "a.py", "start": 1, "end": 1}],
                    "checks": check,
                }
            )
        feedback = json.loads(messages[-1]["content"].split("\n")[0])
        assert feedback["reads"][0]["code"] == "x = 1"
        assert "checks" not in feedback
        assert not feedback["edits_applied"]
        return json.dumps(
            {
                "edits": [{"path": "a.py", "old": "x = 1", "new": "x = 2"}],
                "reads": [],
                "checks": check,
            }
        )

    result = edit_loop(
        tmp_path, "request", [], generate, tmp_path, time.perf_counter() + 10
    )
    assert result["status"] == "applied_unvalidated" and result["model_calls"] == 2


def test_generated_schema_and_local_validation_require_checks():
    schema = EditResponse.model_json_schema()
    assert schema["properties"]["checks"]["minItems"] == 1
    assert schema["properties"]["checks"]["items"]["minItems"] == 3
    with pytest.raises(ValueError):
        EditResponse.model_validate({"edits": [], "reads": [], "checks": []})
    with pytest.raises(ValueError):
        EditResponse.model_validate({"edits": [], "reads": [], "checks": [[]]})


def test_checks_are_all_validated_before_any_subprocess(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(
        "benchmarks.retrieval.minimal_editor.subprocess.run",
        lambda *a, **kw: seen.append(a),
    )
    with pytest.raises(SyntaxError):
        run_checks(
            tmp_path,
            [
                ["python", "-c", "assert __name__ == '__main__'"],
                ["python", "-c", "assert ("],
            ],
            time.perf_counter() + 10,
        )
    assert seen == []


def test_empty_final_response_cannot_complete_an_already_applied_patch(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n")
    actions = iter(
        [
            {
                "edits": [{"path": "a.py", "old": "x = 1", "new": "x = 2"}],
                "reads": [],
                "checks": [["python", "-c", "from a import x; assert x == 3"]],
            },
            {"edits": [], "reads": [], "checks": []},
        ]
    )
    result = edit_loop(
        tmp_path,
        "request",
        [],
        lambda *a: json.dumps(next(actions)),
        tmp_path,
        time.perf_counter() + 10,
    )
    assert result["status"] == "failed" and result["model_calls"] == 2
    assert (tmp_path / "a.py").read_text() == "x = 2\n"
    trace = (tmp_path / "editor-trace.json").read_text()
    assert "Missing behavior checks" in trace


@pytest.mark.parametrize(
    "code",
    [
        "assert True",
        "assert 1",
        "assert x == 2 or True",
        "assert (x == 2 and y == 3) or 'always'",
    ],
)
def test_always_true_assertions_are_rejected(code):
    with pytest.raises(ValueError, match="always-true assertion"):
        validate_checks([["python", "-c", code]])


def test_meaningful_disjunction_is_not_rejected():
    validate_checks([["python", "-c", "assert x == 2 or x == 3"]])


def test_tautological_check_is_rejected_before_write_then_repaired(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n")
    calls = []

    def generate(messages, deadline):
        calls.append(1)
        if len(calls) == 2:
            assert "always-true assertion" in messages[-1]["content"]
            assert (tmp_path / "a.py").read_text() == "x = 1\n"
        code = "from a import x; assert x == 2" + (
            " or True" if len(calls) == 1 else ""
        )
        return json.dumps(
            {
                "edits": [{"path": "a.py", "old": "x = 1", "new": "x = 2"}],
                "reads": [],
                "checks": [["python", "-c", code]],
            }
        )

    result = edit_loop(
        tmp_path, "request", [], generate, tmp_path, time.perf_counter() + 10
    )
    assert result["status"] == "applied_unvalidated" and result["model_calls"] == 2
