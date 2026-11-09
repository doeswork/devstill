"""Overnight campaign orchestration (no model, Pi or Docker needed)."""

import json
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from benchmarks import bench_suite
from benchmarks.retrieval.pi_comparison import validate_verification

FAKE_SUITE = """
import json, sys
from pathlib import Path
output, resume = Path(sys.argv[1]), "--resume" in sys.argv
def row(case, variant, passed):
    return {"id": case, "repeat": 0, "variant": variant, "workload": "small",
            "corpus": "werkzeug", "acceptance_pass": passed,
            "execution": {"status": "applied"}, "worker_wall_seconds": 2.0,
            "coding_seconds": 2.0, "transport": {"model_calls": 1}}
if not resume:
    output.mkdir(parents=True)
    (output / "protocol.json").write_text(json.dumps({"expected_attempts": 2,
        "variants": ["pi", "runtime"]}))
    (output / "cases.json").write_text(json.dumps([{"id": "c1", "corpus": "werkzeug"}]))
    (output / "results.json").write_text(json.dumps([row("c1", "pi", True)]))
    print("c1 0 pi pass 2.0 s", flush=True)
    sys.exit(3)  # crash after one trial
rows = json.loads((output / "results.json").read_text())
rows.append(row("c1", "runtime", True))
(output / "results.json").write_text(json.dumps(rows))
print("c1 0 runtime pass 2.0 s", flush=True)
"""


@pytest.fixture
def registry(tmp_path):
    dataset = tmp_path / "cases.json"
    dataset.write_text(json.dumps([{"id": "c1"}, {"id": "c2"}]))
    path = tmp_path / "suites.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "defaults": {
                    "variants": "pi,runtime",
                    "repeats": 1,
                    "timeout": 60,
                    "acceptance_budget": 60,
                    "requires": [],
                },
                "suites": [
                    {"name": "a", "dataset": str(dataset), "tags": ["core"]},
                    {"name": "b", "dataset": str(dataset), "tags": ["core"]},
                    {
                        "name": "c",
                        "dataset": str(dataset),
                        "tags": ["external"],
                        "requires": ["docker"],
                        "repeats": 3,
                    },
                ],
            }
        )
    )
    return path


def test_registry_selection_and_settings(registry):
    loaded = bench_suite.load_registry(registry)

    def names(suites):
        return [s["name"] for s in suites]

    assert names(bench_suite.select_suites(loaded)) == ["a", "b", "c"]
    assert names(bench_suite.select_suites(loaded, tags=["external"])) == ["c"]
    assert names(bench_suite.select_suites(loaded, names=["b"], tags=["external"])) == [
        "b",
        "c",
    ]
    with pytest.raises(ValueError, match="Unknown suite"):
        bench_suite.select_suites(loaded, names=["missing"])
    c = loaded["suites"][2]
    assert bench_suite.resolve(loaded, c)["repeats"] == 3
    assert (
        bench_suite.resolve(loaded, c, {"repeats": 1, "timeout": None})["repeats"] == 1
    )
    plan = bench_suite.plan(loaded, [c])
    assert plan == [
        {
            "name": "c",
            "cases": 2,
            "trials": 12,
            "worst_case_hours": 0.4,
            "requires": ["docker"],
        }
    ]


def test_the_real_registry_is_valid():
    loaded = bench_suite.load_registry()
    for suite in loaded["suites"]:
        settings = bench_suite.resolve(loaded, suite)
        assert (bench_suite.ROOT / settings["dataset"]).exists(), suite["name"]
        assert settings["variants"].split(",")[0] == "pi"


def test_unverifiable_cases_are_excluded_and_the_rest_still_validates(tmp_path):
    cases = [
        {"id": "ok", "corpus": "werkzeug"},
        {"id": "already", "corpus": "werkzeug"},
        {"id": "broken", "corpus": "werkzeug"},
        {"id": "crashed", "corpus": "werkzeug"},
    ]
    verification = [
        {"id": "ok", "base_pass": False, "reference_pass": True},
        {"id": "already", "base_pass": True, "reference_pass": True},
        {"id": "broken", "base_pass": False, "reference_pass": False},
        {"id": "crashed", "base_pass": None, "reference_pass": False, "error": "boom"},
    ]
    kept, excluded = bench_suite.usable_cases(cases, verification)
    assert [c["id"] for c in kept] == ["ok"]
    assert excluded == {
        "already": "original code already passes the acceptance check",
        "broken": "reference solution fails the acceptance check",
        "crashed": "verification error: boom",
    }
    source = tmp_path / "verified"
    source.mkdir()
    (source / "verification.json").write_text(json.dumps(verification))
    for case in cases:
        for suffix in ("-base.json", "-reference.json", ".patch"):
            (source / f"{case['id']}{suffix}").write_text("x")
    filtered = bench_suite.filtered_verification(source, kept, tmp_path / "subset")
    validate_verification(filtered, kept)  # accepted by pi_comparison
    assert sorted(p.name for p in filtered.iterdir()) == [
        "cases.json",
        "manifest.json",
        "ok-base.json",
        "ok-reference.json",
        "ok.patch",
        "verification.json",
    ]


def test_supervisor_streams_output_and_reports_exit(tmp_path):
    lines = []
    code, timed_out = bench_suite.Supervisor().run(
        [sys.executable, "-c", "print('a 0 pi pass 1 s'); print('done')"],
        tmp_path / "log",
        suite_deadline=time.monotonic() + 60,
        campaign_deadline=time.monotonic() + 60,
        on_line=lines.append,
    )
    assert (code, timed_out) == (0, False)
    assert lines == ["a 0 pi pass 1 s", "done"]
    assert "done" in (tmp_path / "log").read_text()


def test_suite_time_limit_terminates_then_kills_a_stuck_child(tmp_path):
    supervisor = bench_suite.Supervisor(grace=1)
    started = time.monotonic()
    code, timed_out = supervisor.run(
        [
            sys.executable,
            "-c",
            (
                "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
                "print('ready', flush=True); time.sleep(60)"
            ),
        ],
        tmp_path / "log",
        suite_deadline=time.monotonic() + 1,
        campaign_deadline=time.monotonic() + 600,
    )
    assert timed_out and code == -9
    assert time.monotonic() - started < 30
    # A suite time limit is not a campaign stop.
    assert supervisor.stop_reason is None


def test_stop_request_ends_the_child_and_marks_the_campaign(tmp_path):
    supervisor = bench_suite.Supervisor()
    threading.Timer(1, supervisor.stop, args=("stopped by signal 15",)).start()
    code, timed_out = supervisor.run(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        tmp_path / "log",
        suite_deadline=time.monotonic() + 600,
        campaign_deadline=time.monotonic() + 600,
    )
    assert code == -15 and not timed_out
    assert supervisor.stop_reason == "stopped by signal 15"


def test_stray_processes_naming_the_suite_are_killed(tmp_path):
    marker = str(tmp_path / "suite-output")
    stray = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)", marker],
        start_new_session=True,
    )
    try:
        assert bench_suite.kill_strays(marker) == 1
        assert stray.wait(timeout=10) == -9
    finally:
        stray.kill()


def test_campaign_resumes_a_crashed_suite_and_keeps_going(
    tmp_path, registry, monkeypatch
):
    monkeypatch.setattr(bench_suite, "LOCAL", tmp_path / "local")
    monkeypatch.setattr(bench_suite, "TRACKED", tmp_path / "tracked")
    monkeypatch.setattr(bench_suite.signal, "signal", lambda *args: None)
    fake = tmp_path / "fake_suite.py"
    fake.write_text(FAKE_SUITE)

    def preflight(suites, registry, overrides, endpoint_wait):
        return {
            "checks": [],
            "suites": {"a": [], "b": [], "c": ["Docker is not available"]},
            "blocked": [],
            "git": {"short": "abc1234", "branch": "main", "dirty": False, "status": []},
            "model": {"configured_model": "m", "base_url": "http://x/v1", "served": []},
            "pi": {"version": "1.5.0"},
            "tools": {},
            "host": "test",
            "platform": "test",
        }

    def prepare_suite(campaign, settings):
        if settings["name"] == "b":
            raise RuntimeError("no case passed reference verification")
        campaign.suite(settings["name"])["excluded_cases"] = {}
        return Path(settings["dataset"]), tmp_path

    commands = []

    def suite_command(settings, dataset, verified, output, record, resume):
        commands.append((settings["name"], resume))
        return [sys.executable, str(fake), str(output)] + (
            ["--resume"] if resume else []
        )

    monkeypatch.setattr(bench_suite, "preflight", preflight)
    monkeypatch.setattr(bench_suite, "prepare_suite", prepare_suite)
    monkeypatch.setattr(bench_suite, "suite_command", suite_command)
    monkeypatch.setattr(bench_suite, "wait_for_model", lambda seconds: None)

    code = bench_suite.main(["run", "--registry", str(registry), "--note", "test run"])

    assert code == 1  # suite b failed
    latest = tmp_path / "local/latest"
    record = json.loads((latest / "campaign.json").read_text())
    states = {s["name"]: (s["status"], s["reason"]) for s in record["suites"]}
    assert states["a"] == ("completed", None)
    assert states["b"][0] == "failed" and "reference verification" in states["b"][1]
    assert states["c"] == ("skipped", "Docker is not available")
    assert record["status"] == "completed_with_failures"
    assert commands == [("a", False), ("a", True)]
    attempts = record["suites"][0]["attempts"]
    assert [(a["exit_code"], a["resume"], a["trials_recorded"]) for a in attempts] == [
        (3, False, 1),
        (0, True, 2),
    ]
    report = (latest / "REPORT.md").read_text()
    assert "| a | completed | 2/2 | 1/1 | 1/1 |" in report
    assert "no case passed reference verification" in report
    assert "Docker is not available" in report
    assert (
        "pass" in (latest / "status.txt").read_text()
        or "REPORT.md" in (latest / "status.txt").read_text()
    )
    assert not (tmp_path / "local/runner.pid").exists()
    tracked = tmp_path / "tracked"
    index = [
        json.loads(line) for line in (tracked / "index.jsonl").read_text().splitlines()
    ]
    assert [e["id"] for e in index] == [record["id"]]
    assert index[0]["suites"]["a"]["passes"] == {"pi": "1/1", "runtime": "1/1"}
    assert record["id"] in (tracked / "HISTORY.md").read_text()
    assert (tracked / record["id"] / "suites/a/results.compact.json").exists()
    assert bench_suite.main(["report", str(latest.resolve()), "--check"]) == 0
    assert bench_suite.main(["status"]) == 0


def test_blocked_campaign_records_why_and_runs_nothing(tmp_path, registry, monkeypatch):
    monkeypatch.setattr(bench_suite, "LOCAL", tmp_path / "local")
    monkeypatch.setattr(bench_suite, "TRACKED", tmp_path / "tracked")

    def preflight(*args):
        return {
            "checks": [],
            "suites": {},
            "blocked": [{"name": "model_endpoint", "detail": "connection refused"}],
            "git": {"short": "abc1234", "branch": "main", "dirty": False, "status": []},
            "model": None,
            "pi": {},
            "tools": {},
            "host": "test",
            "platform": "test",
        }

    monkeypatch.setattr(bench_suite, "preflight", preflight)
    monkeypatch.setattr(
        bench_suite, "run_suite", lambda *a: pytest.fail("no suite may run")
    )
    assert bench_suite.main(["run", "--registry", str(registry)]) == 2
    record = json.loads((tmp_path / "local/latest/campaign.json").read_text())
    assert record["status"] == "blocked"
    assert "connection refused" in record["reason"]
    assert all(s["status"] == "not_run" for s in record["suites"])
    assert "connection refused" in (tmp_path / "local/latest/REPORT.md").read_text()
