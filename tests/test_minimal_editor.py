import json
import time

import pytest

from benchmarks.retrieval.minimal_editor import apply_edits, edit_loop, read_sources


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
                "checks": [["python", "-c", "assert False"]],
            },
            {
                "edits": [{"path": "a.py", "old": "x = 2", "new": "x = 3"}],
                "checks": [["python", "-c", "assert True"]],
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
            {"edits": [], "reads": [], "checks": [["python", "-c", "assert True"]]},
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
