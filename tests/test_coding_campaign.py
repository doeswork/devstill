import json

from benchmarks.retrieval.coding_campaign import summarize
from benchmarks.retrieval.coding_cases import CASES
from benchmarks.retrieval.coding_report import audit_quick, completed, summarize_quick


def test_suite_has_distinct_requests_in_each_workload_and_corpus():
    assert len({c["id"] for c in CASES}) == 24
    assert len({c["query"] for c in CASES}) == 24
    for corpus in ("cargo", "werkzeug"):
        for workload in ("small", "feature"):
            assert (
                sum(c["corpus"] == corpus and c["workload"] == workload for c in CASES)
                == 6
            )
    assert all(c["checks"] and c["edits"] for c in CASES)


def test_early_failure_cannot_be_a_speed_win_or_completed_workflow():
    old = {
        "id": "case",
        "variant": "old",
        "workload": "small",
        "repeat": 0,
        "acceptance_pass": True,
        "review_seconds": 2,
        "total_seconds": 10,
    }
    new = {
        **old,
        "variant": "task",
        "acceptance_pass": False,
        "review_seconds": 0.1,
        "total_seconds": 0.2,
    }
    summary = summarize([old, new])
    assert summary["small"]["paired_successes"] == 0
    assert not summary["small"]["gate_pass"]
    assert summary["promotion"] == "do not promote"
    assert not completed({"acceptance_pass": True, "execution": {"status": "failed"}})
    assert completed(
        {"acceptance_pass": True, "execution": {"status": "applied_unvalidated"}}
    )


def test_quick_comparison_checks_workflow_completion_and_exact_trial_keys():
    old = {
        "id": "a",
        "repeat": 0,
        "variant": "old",
        "acceptance_pass": True,
        "execution": {"status": "applied"},
        "review_seconds": 2,
        "coding_seconds": 4,
        "total_seconds": 5,
    }
    new = {**old, "variant": "quick", "execution": {"status": "failed"}}
    summary = summarize_quick([old, new], ["a"], 1)
    assert summary["complete"]
    assert summary["variants"]["quick"]["behavior_passes"] == 1
    assert summary["variants"]["quick"]["workflow_passes"] == 0
    assert summary["paired_successes"] == 0
    assert not summarize_quick([old, old], ["a"], 1)["complete"]
    assert not summarize_quick([old, new], ["a", "b"], 1)["complete"]


def test_live_prompt_audit_normalizes_only_checkout_location(tmp_path):
    (tmp_path / "protocol.json").write_text(
        json.dumps({"case_ids": ["a"], "repeats": 1})
    )
    (tmp_path / "results.json").write_text("[]")
    (tmp_path / "final-audit.json").write_text("{}")
    for variant in ("old", "quick"):
        directory = tmp_path / f"a-0-{variant}"
        (directory / "artifacts").mkdir(parents=True)
        (directory / "pool.json").write_text('[{"code": "original source"}]')
        request = {
            "messages": [
                {"content": f"/tmp/devstill-coding-v2-{variant}/repo original source"}
            ],
            "schema_name": "devstill_plan",
        }
        (directory / "artifacts/definition.jsonl").write_text(
            json.dumps(request) + "\n"
        )
    report = audit_quick(tmp_path)
    assert not report["complete"]
    assert report["request_pairs"][0]["same_initial_request"]
    request["messages"][0]["content"] += " extra instructions"
    (directory / "artifacts/definition.jsonl").write_text(json.dumps(request) + "\n")
    assert not audit_quick(tmp_path)["request_pairs"][0]["same_initial_request"]
    for variant in ("old", "quick"):
        path = tmp_path / f"a-0-{variant}/artifacts/definition.jsonl"
        path.write_text(
            json.dumps(
                {
                    "messages": [{"role": "user", "characters": 100}],
                    "schema_name": "devstill_plan",
                }
            )
            + "\n"
        )
    pair = audit_quick(tmp_path)["request_pairs"][0]
    assert pair["same_request_summary"]
    assert pair["same_initial_request"] is None
