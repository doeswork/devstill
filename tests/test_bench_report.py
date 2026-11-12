"""Deterministic reports and code-quality scores (no model needed)."""

import json
import subprocess

import pytest

from benchmarks import bench_quality, bench_report


def row(case="c1", variant="runtime", repeat=0, **fields):
    base = {
        "id": case,
        "repeat": repeat,
        "variant": variant,
        "workload": "small",
        "corpus": "werkzeug",
        "acceptance_pass": False,
        "execution": {"status": "applied"},
        "worker_wall_seconds": 10.0,
        "coding_seconds": 9.0,
        "transport": {"model_calls": 3},
    }
    return {**base, **fields}


def trial(tmp_path, patch="diff --git a/x.py b/x.py\n", acceptance=None):
    directory = tmp_path / "trial"
    directory.mkdir(exist_ok=True)
    (directory / "change.patch").write_text(patch)
    if acceptance is not None:
        (directory / "acceptance.json").write_text(json.dumps(acceptance))
    return directory


@pytest.mark.parametrize(
    "fields,acceptance,patch,expected",
    [
        (
            {"harness_error": True, "error": "harness_error: OSError: disk"},
            None,
            "x",
            "harness_error",
        ),
        ({"endpoint_error": True}, None, "x", "endpoint_error"),
        ({"acceptance_pass": True}, None, "x", "pass"),
        ({"error": "TimeoutExpired"}, None, "x", "timeout"),
        (
            {"error": "TimeoutExpired", "acceptance_pass": True},
            None,
            "x",
            "timeout_after_correct_edit",
        ),
        ({"error": "CalledProcessError"}, None, "x", "worker_crash"),
        (
            {
                "acceptance_pass": True,
                "error": "gave up",
                "execution": {"status": "failed"},
            },
            None,
            "x",
            "incomplete_workflow",
        ),
        (
            {
                "error": "No verified change",
                "failure_stage": "turn_limit",
                "execution": {"status": "failed"},
            },
            None,
            "",
            "no_change",
        ),
        (
            {
                "error": "No verified change",
                "failure_stage": "turn_limit",
                "execution": {"status": "failed"},
            },
            None,
            "diff",
            "gave_up:turn_limit",
        ),
        (
            {},
            [{"returncode": 1, "output": "Traceback\nAssertionError: wrong"}],
            "diff",
            "wrong_fix:behavior_check",
        ),
        (
            {},
            [
                {"returncode": 0},
                {"returncode": 1, "output": "FAILED tests/t.py::x - boom\n1 failed"},
            ],
            "diff",
            "wrong_fix:regression_tests",
        ),
        (
            {},
            [
                {
                    "returncode": 1,
                    "resolved": False,
                    "report": {"patch_successfully_applied": False},
                }
            ],
            "diff",
            "wrong_fix:patch_does_not_apply",
        ),
        (
            {},
            [
                {
                    "returncode": 1,
                    "resolved": False,
                    "report": {"tests_status": {"FAIL_TO_PASS": {"failure": ["t1"]}}},
                }
            ],
            "diff",
            "wrong_fix:issue_tests_fail",
        ),
        (
            {},
            [
                {
                    "returncode": 1,
                    "resolved": False,
                    "report": {"tests_status": {"PASS_TO_PASS": {"failure": ["t2"]}}},
                }
            ],
            "diff",
            "wrong_fix:broke_existing_tests",
        ),
    ],
)
def test_every_failure_gets_a_stable_category(
    tmp_path, fields, acceptance, patch, expected
):
    category, detail = bench_report.classify(
        row(**fields), trial(tmp_path, patch, acceptance)
    )
    assert category == expected
    if expected != "pass":
        assert detail


def test_failure_detail_keeps_the_cause_of_a_traceback():
    error = "Your latest check fails:\nTraceback (most recent call last):\nAssertionError: 3 != 4"
    assert (
        bench_report.gist(error) == "Your latest check fails: … AssertionError: 3 != 4"
    )
    assert bench_report.gist("one line") == "one line"


def make_campaign(root, name, started, rows, *, status="completed"):
    directory = root / name
    output = directory / "suites/s1"
    output.mkdir(parents=True)
    (output / "results.json").write_text(json.dumps(rows))
    (output / "protocol.json").write_text(
        json.dumps({"variants": ["pi", "runtime"], "expected_attempts": len(rows)})
    )
    for r in rows:
        directory_name = f"{r['id']}-{r['repeat']}-{r['variant']}"
        (output / directory_name).mkdir()
        (output / directory_name / "change.patch").write_text("diff\n")
        (output / directory_name / "acceptance.json").write_text(
            json.dumps(
                [
                    {
                        "returncode": 0 if r["acceptance_pass"] else 1,
                        "output": "AssertionError: no",
                    }
                ]
            )
        )
    (directory / "campaign.json").write_text(
        json.dumps(
            {
                "id": name,
                "status": status,
                "started_at": started,
                "finished_at": started,
                "git": {"short": "abc1234", "branch": "main", "dirty": False},
                "model": {"configured_model": "model-a", "base_url": "http://x/v1"},
                "pi": {"version": "1.5.0"},
                "settings": {"sampling": "greedy"},
                "suites": [
                    {
                        "name": "s1",
                        "status": "completed",
                        "settings": {
                            "repeats": 1,
                            "timeout": 120,
                            "variants": "pi,runtime",
                        },
                        "excluded_cases": {
                            "c9": "reference solution fails the acceptance check"
                        },
                    }
                ],
            }
        )
    )
    return directory


def test_report_compares_with_the_previous_campaign_and_detects_tampering(tmp_path):
    tracked = tmp_path / "tracked"
    first = make_campaign(
        tmp_path,
        "20260101T000000Z-aaaaaaa",
        "2026-01-01T00:00:00+00:00",
        [
            row("c1", "pi", acceptance_pass=True, worker_wall_seconds=20.0),
            row("c1", "runtime", acceptance_pass=True, worker_wall_seconds=10.0),
            row("c2", "pi", acceptance_pass=True),
            row("c2", "runtime", acceptance_pass=False),
        ],
    )
    bench_report.write_campaign_report(first, tracked / "index.jsonl")
    bench_report.archive(first, tracked)
    second = make_campaign(
        tmp_path,
        "20260102T000000Z-bbbbbbb",
        "2026-01-02T00:00:00+00:00",
        [
            row("c1", "pi", acceptance_pass=True, worker_wall_seconds=20.0),
            row("c1", "runtime", acceptance_pass=True, worker_wall_seconds=5.0),
            row("c2", "pi", acceptance_pass=True),
            row("c2", "runtime", acceptance_pass=True),
        ],
    )
    report = bench_report.write_campaign_report(second, tracked / "index.jsonl")
    bench_report.archive(second, tracked)

    suite = report["suites"][0]
    assert suite["arms"]["runtime"]["passes"] == 2
    assert suite["paired"]["runtime"]["median_ratio"] == 0.62  # median(0.25, 1.0)
    previous = report["previous"]
    assert previous["id"] == "20260101T000000Z-aaaaaaa"
    assert previous["suites"]["s1"]["arms"]["runtime"] == {
        "before": "1/2",
        "now": "2/2",
    }
    assert previous["suites"]["s1"]["flips"] == [
        {"id": "c2", "variant": "runtime", "before": "0/1", "now": "1/1"}
    ]
    markdown = (second / "REPORT.md").read_text()
    assert report["totals_paired"]["runtime"]["median_ratio"] == 0.62
    assert "| **All** |" in markdown and "**0.62**" in markdown
    assert "c2 (Devstill): 0/1 → 1/1" in markdown
    assert "c9: excluded" in markdown
    # Same records, same report; edited records are caught.
    assert bench_report.check_report(second, tracked / "index.jsonl") == []
    rows = json.loads((second / "suites/s1/results.json").read_text())
    rows[3]["acceptance_pass"] = False
    (second / "suites/s1/results.json").write_text(json.dumps(rows))
    problems = bench_report.check_report(second, tracked / "index.jsonl")
    assert "section 'suites' differs from the raw records" in problems
    # History keeps one line per campaign, newest first in the table.
    lines = (tracked / "index.jsonl").read_text().splitlines()
    assert [json.loads(line)["id"] for line in lines] == [
        "20260101T000000Z-aaaaaaa",
        "20260102T000000Z-bbbbbbb",
    ]
    history = (tracked / "HISTORY.md").read_text()
    assert history.index("20260102T") < history.index("20260101T")


@pytest.fixture
def source_repo(tmp_path):
    repo = tmp_path / "repo"
    (repo / "pkg").mkdir(parents=True)
    (repo / "pkg/mod.py").write_text("import os\n\n\ndef f():\n    return os.sep\n")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "base"],
        cwd=repo,
        check=True,
    )
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True)
    return repo, commit.strip()


def diff_after(repo, path, content):
    original = (repo / path).read_text()
    (repo / path).write_text(content)
    patch = subprocess.check_output(["git", "diff"], cwd=repo, text=True)
    (repo / path).write_text(original)
    return patch


def test_quality_scores_size_scope_tests_and_new_lint(source_repo):
    repo, commit = source_repo
    reference = diff_after(
        repo, "pkg/mod.py", "import os\n\n\ndef f():\n    return os.sep * 2\n"
    )
    candidate = diff_after(
        repo,
        "pkg/mod.py",
        "import os\nimport sys\n\n\ndef f():\n    unused = 1\n    return os.sep * 2\n",
    )
    score = bench_quality.trial_quality(
        candidate, reference, lambda path: bench_quality.original(repo, commit, path)
    )
    assert score["files"] == 1 and score["tests_touched"] == []
    assert score["reference_lines"] == 2 and score["changed_lines"] == 4
    assert score["size_ratio"] == 2.0 and score["extra_files"] == []
    # F401 (unused import) and F841 (unused variable) are new; nothing pre-existing counts.
    assert score["lint"] == {"new_findings": 2, "new_codes": {"F401": 1, "F841": 1}}


def test_quality_of_a_patch_that_does_not_apply_is_reported_not_raised(source_repo):
    repo, commit = source_repo
    bogus = (
        "diff --git a/pkg/mod.py b/pkg/mod.py\n--- a/pkg/mod.py\n+++ b/pkg/mod.py\n"
        "@@ -1,1 +1,1 @@\n-not there\n+changed\n"
    )
    score = bench_quality.trial_quality(
        bogus, None, lambda path: bench_quality.original(repo, commit, path)
    )
    assert score["lint"]["error"].startswith("patch did not apply")


def test_report_tolerates_a_suite_that_never_ran(tmp_path):
    report = bench_report.suite_report(
        tmp_path,
        {"name": "x", "status": "skipped", "reason": "no docker", "settings": {}},
    )
    assert report["trials"] == 0 and report["reason"] == "no docker"
    assert report["failures"] == [] and report["arms"] == {}


def test_no_change_says_what_the_agent_did(tmp_path):
    directory = trial(tmp_path, patch="")
    category, detail = bench_report.classify(
        row(
            execution={
                "status": "applied_unvalidated",
                "tool_calls": 2,
                "stop_reason": "stop",
            }
        ),
        directory,
    )
    assert category == "no_change"
    assert detail == (
        "agent ended its turn after 2 tool call(s) without editing (stop reason: stop)"
    )
    # An explicit error from the agent wins over the generic description.
    _, detail = bench_report.classify(
        row(error="No verified change within 20 model calls"), directory
    )
    assert detail == "No verified change within 20 model calls"


def test_totals_pool_the_paired_timings_of_every_suite(tmp_path):
    def suite(pairs):
        return {
            "paired": {
                "runtime": {
                    "pairs": len(pairs),
                    "both": len(pairs),
                    "pi_only": 1,
                    "candidate_only": 0,
                    "neither": 0,
                    "shared_seconds": pairs,
                }
            }
        }

    pooled = bench_report.pooled_pairs(
        [suite([[10.0, 5.0], [20.0, 30.0]]), suite([[8.0, 4.0]])]
    )["runtime"]
    assert pooled["both"] == 3 and pooled["pi_only"] == 2
    assert pooled["median_shared_seconds"] == {"pi": 10.0, "candidate": 5.0}
    assert pooled["median_ratio"] == 0.5  # median of 0.5, 1.5, 0.5
    assert pooled["candidate_faster"] == 2
    assert bench_report.pooled_pairs([{"paired": {}}]) == {}
