import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest

from benchmarks.retrieval.pi_comparison import (
    source_manifest,
    summarize,
    trial_order,
    validate_verification,
)
from benchmarks.retrieval.recording_proxy import recording_proxy, transport_metrics


def test_order_balances_every_arm_across_repetitions():
    variants = ["pi", "quick", "feature"]
    orders = [trial_order(variants, 0, n) for n in range(3)]
    for position in range(3):
        assert {o[position] for o in orders} == set(variants)


@pytest.mark.parametrize(
    "variant,engine,mode,frozen",
    [
        ("quick", "quick", "yolo", False),
        ("review", "quick", "review", False),
        ("baseline", "old", "yolo", True),
    ],
)
def test_comparison_routes_review_and_frozen_baseline(
    tmp_path, monkeypatch, variant, engine, mode, frozen
):
    from types import SimpleNamespace

    from benchmarks.retrieval.pi_comparison import worker

    runtime = tmp_path / "runtime.json"
    runtime.write_text('{"llm":{},"executor":{}}')
    query = tmp_path / "query.txt"
    query.write_text("Original request")
    calls = []
    monkeypatch.setattr(
        "benchmarks.retrieval.pi_comparison.campaign.worker",
        lambda *a, **kw: calls.append((a, kw)),
    )
    # worker replaces the runtime provider; restore it after this test.
    from benchmarks.retrieval import coding_campaign

    monkeypatch.setattr(
        coding_campaign, "runtime_settings", coding_campaign.runtime_settings
    )
    worker(
        SimpleNamespace(
            runtime=runtime,
            query=query,
            case=["case"],
            workload="small",
            corpus="werkzeug",
            variant=variant,
            repo=tmp_path,
            output=tmp_path,
        )
    )
    args, kwargs = calls[0]
    assert args[0]["query"] == "Original request" and args[1] == engine
    assert kwargs == {"private_index": True, "baseline_quick": frozen, "mode": mode}


def trial(variant, passed=True, seconds=10, **kwargs):
    return {
        "id": "a",
        "repeat": 0,
        "variant": variant,
        "workload": "small",
        "acceptance_pass": passed,
        "execution": {"status": "applied_unvalidated"},
        "worker_wall_seconds": seconds,
        "coding_seconds": seconds - 1,
        "end_to_end_seconds": seconds + 1,
        "transport": {"model_calls": 3},
        **kwargs,
    }


def test_no_speed_credit_for_failures_partial_workflows_or_missing_attempts():
    cases = [{"id": "a", "workload": "small"}]
    rows = [trial("pi"), trial("quick", seconds=1, execution={"status": "failed"})]
    report = summarize(rows, cases, ["pi", "quick"], 1)
    group = report["workloads"]["small"]["versus_pi"]["quick"]
    assert report["complete"]
    assert group["pi_only_passes"] == 1
    assert group["shared_successes"] == 0
    assert not group["descriptive_target_met"]
    rows[1] = trial("quick", seconds=5)
    report = summarize(rows, cases, ["pi", "quick"], 1)
    assert report["workloads"]["small"]["versus_pi"]["quick"]["descriptive_target_met"]
    assert not summarize(rows, cases, ["pi", "quick"], 2)["complete"]
    assert not summarize([rows[0], rows[0]], cases, ["pi", "quick"], 1)["complete"]


def test_snapshot_hash_includes_policy_and_benchmark_not_only_python(tmp_path):
    policy = tmp_path / "src/devstill/pi/policy.ts"
    policy.parent.mkdir(parents=True)
    policy.write_text("first")
    before = source_manifest(tmp_path)
    policy.write_text("second")
    assert source_manifest(tmp_path) != before


def test_verification_requires_matching_full_dataset(tmp_path):
    cases = [{"id": "a"}]
    (tmp_path / "cases.json").write_text(json.dumps(cases))
    (tmp_path / "manifest.json").write_text('{"cases_sha256":"incorrect"}')
    (tmp_path / "verification.json").write_text(
        '[{"id":"a","base_pass":false,"reference_pass":true}]'
    )
    with pytest.raises(ValueError, match="matching this exact dataset"):
        validate_verification(tmp_path, cases)


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("disable_reasoning", [False, True])
def test_proxy_preserves_requests_streams_and_secrets_and_enforces_budget(
    tmp_path, stream, disable_reasoning
):
    received = []

    class Upstream(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            received.append(
                (
                    self.headers.get("Authorization"),
                    self.rfile.read(int(self.headers["Content-Length"])),
                )
            )
            payload = {
                "choices": [{"delta" if stream else "message": {"reasoning": "trace"}}],
                "usage": {"total_tokens": 42},
            }
            body = (
                ("data: " + json.dumps(payload) + "\n\ndata: [DONE]\n\n").encode()
                if stream
                else json.dumps(payload).encode()
            )
            self.send_response(200)
            self.send_header(
                "Content-Type", "text/event-stream" if stream else "application/json"
            )
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    directory = tmp_path / "transport"
    request = b'{"messages":[{"role":"user","content":"exact original prompt"}],"tools":[],"chat_template_kwargs":{"enable_thinking":true,"other_setting":42}}'
    try:
        with (
            recording_proxy(
                f"http://127.0.0.1:{server.server_port}/v1",
                directory,
                max_calls=1,
                disable_reasoning=disable_reasoning,
            ) as url,
            httpx.Client() as client,
        ):
            first = client.post(
                url + "/chat/completions",
                content=request,
                headers={"Authorization": "Bearer private-test-secret"},
            )
            assert first.status_code == 200
            second = client.post(url + "/chat/completions", content=request)
            assert second.status_code == 429
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
    expected = request
    if disable_reasoning:
        changed = json.loads(request)
        changed["chat_template_kwargs"]["enable_thinking"] = False
        expected = json.dumps(changed).encode()
        assert (directory / "0001.original-request").read_bytes() == request
    else:
        assert not (directory / "0001.original-request").exists()
    assert received == [("Bearer private-test-secret", expected)]
    assert (directory / "0001.request").read_bytes() == expected
    assert (
        json.loads((directory / "0001.json").read_text())[
            "reasoning_explicitly_disabled"
        ]
        == disable_reasoning
    )
    assert all(
        b"private-test-secret" not in p.read_bytes() for p in directory.iterdir()
    )
    metrics = transport_metrics(directory)
    assert metrics["model_calls"] == 1
    assert metrics["total_tokens"] == 42
    assert metrics["budget_rejections"] == 1
    # The recorder verifies actual output, even when a server ignores the flag.
    assert metrics["responses_with_reasoning"] == 1
    assert metrics["reasoning_output_characters"] == 5


def test_missing_stream_usage_stays_unknown(tmp_path):
    (tmp_path / "0001.json").write_text(
        json.dumps(
            {
                "generation": True,
                "budget_blocked": False,
                "status": 200,
                "content_type": "text/event-stream",
            }
        )
    )
    (tmp_path / "0001.response").write_text('data: {"choices":[]}\n\ndata: [DONE]\n\n')
    assert transport_metrics(tmp_path)["total_tokens"] is None


@pytest.mark.parametrize(
    "reason,expected",
    [("stop", "applied_unvalidated"), ("error", "failed"), ("length", "failed")],
)
def test_direct_pi_requires_normal_completion_and_uses_stock_tools(
    tmp_path, monkeypatch, reason, expected
):
    import subprocess

    from benchmarks.retrieval.pi_comparison import direct_pi
    from devstill.config import DevstillConfig
    from devstill.pi_runtime import PiResolvedLimits

    repo = tmp_path / "repo"
    repo.mkdir()
    output = tmp_path / "out"
    output.mkdir()
    cfg = DevstillConfig()
    cfg.llm.model = "test-model"
    monkeypatch.setenv("DEVSTILL_BENCH_TOOL_PATH", "/prepared/python:/prepared/rust")
    monkeypatch.setattr(
        "devstill.pi_runtime.resolve_pi_limits",
        lambda *args: PiResolvedLimits("auto", 8192, 4096, "test"),
    )

    def run(command, **kwargs):
        assert command[command.index("--tools") + 1] == "read,bash,edit,write"
        assert "--extension" not in command
        assert command[-1] == "the original request"
        assert kwargs["cwd"] == repo
        assert kwargs["env"]["PATH"].startswith("/prepared/python:/prepared/rust:")
        events = [
            {
                "type": "message_end",
                "message": {"role": "assistant", "stopReason": reason},
            },
            {"type": "agent_end"},
        ]
        for event in events:
            kwargs["stdout"].write(json.dumps(event) + "\n")
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr("benchmarks.retrieval.pi_comparison.subprocess.run", run)
    direct_pi(
        {
            "id": "case",
            "query": "the original request",
            "workload": "small",
            "corpus": "werkzeug",
        },
        repo,
        output,
        cfg.model_dump(mode="json"),
    )
    assert (
        json.loads((output / "result.json").read_text())["execution"]["status"]
        == expected
    )


def test_evaluator_restores_original_regressions_without_changing_generated_repo(
    tmp_path, monkeypatch
):
    from types import SimpleNamespace

    from benchmarks.retrieval import coding_campaign

    original = tmp_path / "bench/repos/werkzeug-3.1.3/tests"
    original.mkdir(parents=True)
    (original / "test_http.py").write_text("original regression")
    repo = tmp_path / "repo"
    (repo / "tests").mkdir(parents=True)
    (repo / "tests/test_http.py").write_text("weakened regression")
    monkeypatch.setattr(coding_campaign, "BENCH", tmp_path / "bench")
    seen = []

    def run(command, **kwargs):
        seen.append((Path(kwargs["cwd"]) / "tests/test_http.py").read_text())
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(coding_campaign.subprocess, "run", run)
    passed, _ = coding_campaign.accept(
        {"corpus": "werkzeug", "checks": "pass"},
        repo,
        tmp_path / "acceptance.json",
        protect_regressions=True,
    )
    assert passed and seen == ["original regression", "original regression"]
    assert (repo / "tests/test_http.py").read_text() == "weakened regression"


def test_quick_worker_opens_only_private_index(tmp_path, monkeypatch):
    from benchmarks.retrieval import coding_campaign
    from tests.test_tasks import config

    cfg = config()
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setattr(
        "devstill.config.load_config", lambda p: cfg.model_copy(deep=True)
    )
    monkeypatch.setattr(
        coding_campaign, "runtime_settings", lambda *args: (cfg.llm, cfg.executor)
    )
    copies = []
    monkeypatch.setattr(
        coding_campaign, "copy_index", lambda source, dest: copies.append(dest)
    )

    class Search:
        def __init__(self, path, config):
            assert config.index.path == str(repo / ".devstill/chroma")
            assert config.repo.root == str(repo)
            raise LookupError("stop before opening Chroma or calling a model")

    monkeypatch.setattr("devstill.search.HybridSearch", Search)
    with pytest.raises(LookupError, match="stop before"):
        coding_campaign.worker(
            {"id": "a", "corpus": "werkzeug", "workload": "small"},
            "quick",
            repo,
            tmp_path,
            private_index=True,
        )
    assert copies == [repo / ".devstill"]


def test_input_manifest_ignores_empty_wal_but_detects_real_wal_changes(
    tmp_path, monkeypatch
):
    from benchmarks.retrieval import coding_campaign
    from benchmarks.retrieval.pi_comparison import input_manifest
    from tests.test_tasks import config

    cfg = tmp_path / "configs/baseline.json"
    cfg.parent.mkdir()
    cfg.write_text("{}")
    index = tmp_path / "index/chroma"
    index.mkdir(parents=True)
    (index / "chroma.sqlite3").write_bytes(b"chroma data")
    (index.parent / "metadata.sqlite").write_bytes(b"source metadata")
    monkeypatch.setattr(coding_campaign, "BENCH", tmp_path)
    monkeypatch.setattr("devstill.config.load_config", lambda p: config())
    monkeypatch.setattr("devstill.config.resolve_index_path", lambda *args: index)
    cases = [{"corpus": "werkzeug"}]
    before = input_manifest(cases)
    wal = index.parent / "metadata.sqlite-wal"
    wal.write_bytes(b"")
    assert input_manifest(cases) == before
    wal.write_bytes(b"committed SQLite changes")
    assert input_manifest(cases) != before
