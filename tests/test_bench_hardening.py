"""Unattended-run guarantees of the Pi comparison harness (no model needed)."""

import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

from benchmarks.retrieval import pi_comparison as harness
from benchmarks.retrieval.recording_proxy import recording_proxy, transport_metrics

CASES = [
    {"id": "a", "workload": "small", "corpus": "werkzeug"},
    {"id": "b", "workload": "small", "corpus": "werkzeug"},
]
VARIANTS = ["pi", "runtime"]


def good_row(case, repeat, variant, passed=True):
    return {
        "id": case["id"],
        "repeat": repeat,
        "variant": variant,
        "workload": case["workload"],
        "corpus": case["corpus"],
        "acceptance_pass": passed,
        "execution": {"status": "applied"},
        "worker_wall_seconds": 1.0,
        "coding_seconds": 1.0,
        "end_to_end_seconds": 1.0,
        "transport": {"model_calls": 1},
    }


def test_pending_trials_skip_finished_and_retry_infrastructure_failures():
    rows = [
        good_row(CASES[0], 0, "pi"),
        {**good_row(CASES[0], 0, "runtime"), "harness_error": True},
        {**good_row(CASES[1], 0, "pi"), "endpoint_error": True},
    ]
    pending = [
        (c["id"], n, v) for c, n, v in harness.pending_trials(CASES, VARIANTS, 1, rows)
    ]
    # Fixed rotation order is preserved for whatever remains.
    assert pending == [("a", 0, "runtime"), ("b", 0, "runtime"), ("b", 0, "pi")]


def test_one_crashing_trial_is_recorded_and_the_campaign_continues(tmp_path, capsys):
    calls = []

    def trial(case, repeat, variant, directory):
        calls.append((case["id"], variant))
        if case["id"] == "a" and variant == "runtime":
            raise OSError("disk full")
        row = good_row(case, repeat, variant)
        (directory / "result.json").write_text(json.dumps(row))
        return row

    rows = harness.run_trials(tmp_path, CASES, VARIANTS, 1, [], trial)
    assert len(rows) == 4
    failed = [r for r in rows if r.get("harness_error")]
    assert len(failed) == 1 and failed[0]["error"].startswith(
        "harness_error: OSError: disk full"
    )
    assert failed[0]["attempt"] == 2  # retried once, then recorded
    assert calls.count(("a", "runtime")) == 2
    assert "OSError" in (tmp_path / "a-0-runtime/harness-error.txt").read_text()
    assert (tmp_path / "a-0-runtime.discarded/harness-error.txt").exists()
    saved = json.loads((tmp_path / "results.json").read_text())
    assert [(r["id"], r["variant"]) for r in saved] == [
        (r["id"], r["variant"]) for r in rows
    ]
    summary = json.loads((tmp_path / "summary.json").read_text())
    assert summary["complete"] and summary["attempts"] == 4
    assert "FAIL" in capsys.readouterr().out


def test_a_trial_that_stalls_before_any_model_call_is_retried_once(tmp_path):
    attempts = {}

    def stalls_once(case, repeat, variant, directory):
        key = (case["id"], variant)
        attempts[key] = attempts.get(key, 0) + 1
        if key == ("a", "pi") and attempts[key] == 1:
            return {
                **good_row(case, repeat, variant, passed=False),
                "error": "TimeoutExpired",
                "transport": {"model_calls": 0},
                "startup_stall": True,
            }
        return good_row(case, repeat, variant)

    rows = harness.run_trials(tmp_path, CASES, VARIANTS, 1, [], stalls_once)
    retried = next(r for r in rows if (r["id"], r["variant"]) == ("a", "pi"))
    assert retried["acceptance_pass"] and retried["attempt"] == 2
    assert retried["retry_of"] == "retryable"
    stalled = json.loads((tmp_path / "a-0-pi.discarded/result.json").read_text())
    assert stalled["startup_stall"] and stalled["error"] == "TimeoutExpired"
    assert attempts[("a", "pi")] == 2 and len(rows) == 4


def test_retry_succeeds_and_resume_reruns_only_unfinished_or_infra_failed(tmp_path):
    attempts = {}

    def flaky(case, repeat, variant, directory):
        key = (case["id"], variant)
        attempts[key] = attempts.get(key, 0) + 1
        if key == ("b", "pi") and attempts[key] == 1:
            raise RuntimeError("clone failed")
        return good_row(case, repeat, variant)

    rows = harness.run_trials(tmp_path, CASES, VARIANTS, 1, [], flaky)
    retried = next(r for r in rows if (r["id"], r["variant"]) == ("b", "pi"))
    assert not retried.get("harness_error") and retried["attempt"] == 2

    # Resume: one finished row, one infra failure, a leftover directory from a crash.
    previous = [
        good_row(CASES[0], 0, "pi"),
        {**good_row(CASES[0], 0, "runtime"), "endpoint_error": True, "error": "x"},
    ]
    resume_dir = tmp_path / "resume"
    resume_dir.mkdir()
    (resume_dir / "b-0-runtime").mkdir()
    (resume_dir / "b-0-runtime/partial.log").write_text("killed")
    ran = []

    def trial(case, repeat, variant, directory):
        ran.append((case["id"], variant))
        return good_row(case, repeat, variant)

    rows = harness.run_trials(resume_dir, CASES, VARIANTS, 1, previous, trial)
    assert sorted(ran) == [("a", "runtime"), ("b", "pi"), ("b", "runtime")]
    assert len(rows) == 4 and not any(harness.retryable(r) for r in rows)
    redone = next(r for r in rows if (r["id"], r["variant"]) == ("a", "runtime"))
    assert redone["retry_of"] == "x"
    assert (resume_dir / "b-0-runtime.discarded/partial.log").exists()


def test_campaign_level_failures_stop_the_run(tmp_path):
    def before_trial():
        raise harness.EndpointUnavailable("down for good")

    with pytest.raises(harness.EndpointUnavailable):
        harness.run_trials(
            tmp_path,
            CASES,
            VARIANTS,
            1,
            [],
            lambda *a: pytest.fail("trial must not run"),
            before_trial=before_trial,
        )


def test_endpoint_wait_rides_out_a_restart_then_gives_up(monkeypatch):
    replies = iter([httpx.ConnectError("refused"), httpx.ConnectError("refused"), "ok"])

    class Client:
        def __init__(self, **kwargs):
            assert kwargs["trust_env"] is False

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def get(self, url, headers):
            reply = next(replies)
            if isinstance(reply, Exception):
                raise reply
            return httpx.Response(200, request=httpx.Request("GET", url))

    monkeypatch.setattr(harness.httpx, "Client", Client)
    naps = []
    harness.wait_for_endpoint("http://x/v1", {}, 3600, interval=5, sleep=naps.append)
    assert naps == [5, 5]

    replies = iter([httpx.ConnectError("refused")] * 3)
    with pytest.raises(harness.EndpointUnavailable, match="unavailable"):
        harness.wait_for_endpoint("http://x/v1", {}, 0, sleep=naps.append)


def test_server_model_identity_ignores_volatile_fields():
    first = {"data": [{"id": "m", "root": "/w", "max_model_len": 8, "created": 1}]}
    second = {"data": [{"id": "m", "root": "/w", "max_model_len": 8, "created": 2}]}
    other = {"data": [{"id": "n", "root": "/w", "max_model_len": 8, "created": 2}]}
    assert harness.server_model_ids(first) == harness.server_model_ids(second)
    assert harness.server_model_ids(first) != harness.server_model_ids(other)


def test_proxy_fixes_sampling_for_generation_requests_only(tmp_path):
    received = []

    class Upstream(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            self.do_POST()

        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            received.append((self.path, self.rfile.read(length)))
            body = b'{"choices":[],"usage":{"total_tokens":3}}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    directory = tmp_path / "transport"
    request = b'{"messages":[],"temperature":0.7}'
    try:
        with (
            recording_proxy(
                f"http://127.0.0.1:{server.server_port}/v1",
                directory,
                sampling=harness.SAMPLING_PROFILES["greedy"],
            ) as url,
            httpx.Client() as client,
        ):
            client.post(url + "/chat/completions", content=request)
            client.get(url + "/models")
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
    sent = json.loads(received[0][1])
    assert sent["temperature"] == 0 and sent["seed"] == 1
    assert received[1] == ("/v1/models", b"")
    assert (directory / "0001.original-request").read_bytes() == request
    record = json.loads((directory / "0001.json").read_text())
    assert record["sampling_override"] == {"temperature": 0, "seed": 1}
    assert (
        json.loads((directory / "0002.json").read_text())["sampling_override"] is None
    )


def test_unreachable_server_is_counted_as_infrastructure(tmp_path):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]  # closed again: nothing listens here
    directory = tmp_path / "transport"
    with (
        recording_proxy(f"http://127.0.0.1:{port}/v1", directory) as url,
        pytest.raises(httpx.HTTPError),
    ):
        httpx.post(url + "/chat/completions", content=b"{}")
    metrics = transport_metrics(directory)
    assert metrics["model_calls"] == 1 and metrics["upstream_unreachable"] == 1
    assert transport_metrics(tmp_path / "missing")["model_calls"] == 0


def test_trial_workspace_is_the_same_path_and_starts_empty(tmp_path):
    target = tmp_path / "work"
    with harness.trial_workspace(target) as first:
        (first / "leftover").write_text("x")
    (target / "stale").mkdir(parents=True)  # e.g. a crash skipped cleanup
    with harness.trial_workspace(target) as second:
        assert second == first == target
        assert list(second.iterdir()) == []
    assert not target.exists()


def test_case_verification_records_a_crash_and_checks_the_rest(tmp_path, monkeypatch):
    from benchmarks.retrieval import coding_campaign

    def verify_case(output, case):
        if case["id"] == "bad":
            raise OSError("clone failed")
        return {"id": case["id"], "base_pass": False, "reference_pass": True}

    monkeypatch.setattr(coding_campaign, "verify_case", verify_case)
    cases = [{"id": "bad"}, {"id": "good"}]
    with pytest.raises(RuntimeError, match="verification failed"):
        coding_campaign.verify(tmp_path / "verified", cases)
    results = json.loads((tmp_path / "verified/verification.json").read_text())
    assert results == [
        {
            "id": "bad",
            "base_pass": None,
            "reference_pass": False,
            "error": "OSError: clone failed",
        },
        {"id": "good", "base_pass": False, "reference_pass": True},
    ]
    assert (tmp_path / "verified/manifest.json").exists()


def test_resume_refuses_changed_settings():
    protocol = {"repeats": 2, "variants": ["pi", "runtime"], "sampling_override": None}
    same = {"repeats": 2, "variants": ["pi", "runtime"], "sampling_override": None}
    assert harness.resume_mismatches(protocol, same) == []
    changed = {**same, "repeats": 3, "sampling_override": {"temperature": 0}}
    assert harness.resume_mismatches(protocol, changed) == [
        "repeats",
        "sampling_override",
    ]
