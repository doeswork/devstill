from __future__ import annotations

import json
import sys
from contextlib import nullcontext

import pytest

from devstill.context import context_record
from devstill.feature_planning import (
    FeatureBudget,
    FeaturePlan,
    build_feature_draft,
    create_feature_draft,
    load_feature,
    save_feature,
)
from devstill.feature_runs import run_feature
from tests.test_context import make_result
from tests.test_executor import FakePiRunner
from tests.test_planning import FakeClient
from tests.test_tasks import config, make_repo


def plan_data():
    return {
        "requirements": ["Expose the service result", "Show it in the UI"],
        "contracts": [
            {"description": "service.value() returns 42; UI displays 42", "owner": 0}
        ],
        "tasks": [
            {
                "outcome": "Create service module",
                "requirements": [1],
                "acceptance": ["value returns 42"],
                "inputs": [{"path": "seed.py"}],
                "writes": [{"path": "service.py", "operation": "create"}],
                "outputs": [{"path": "service.py", "symbol": "value"}],
                "contracts": [1],
            },
            {
                "outcome": "Wire backend endpoint",
                "requirements": [1],
                "acceptance": ["endpoint returns service value"],
                "depends_on": [1],
                "inputs": [
                    {"path": "service.py", "symbol": "value", "producer": 1},
                    {"path": "endpoint.py"},
                ],
                "writes": [{"path": "endpoint.py", "operation": "update"}],
                "contracts": [1],
            },
            {
                "outcome": "Update UI",
                "requirements": [2],
                "acceptance": ["UI displays 42"],
                "inputs": [{"path": "ui.txt"}],
                "writes": [{"path": "ui.txt", "operation": "update"}],
                "contracts": [1],
            },
        ],
    }


def proposal_data():
    return {
        "interfaces": ["service.value() returns 42; UI displays 42"],
        "tasks": [
            {
                "name": "service",
                "outcome": "Create service module",
                "acceptance": ["value returns 42"],
                "reads": ["seed.py"],
                "files": ["service.py"],
            },
            {
                "name": "endpoint",
                "outcome": "Wire backend endpoint",
                "after": ["service"],
                "acceptance": ["endpoint returns service value"],
                "reads": ["service.py", "endpoint.py"],
                "files": ["endpoint.py"],
            },
            {
                "name": "ui",
                "outcome": "Update UI",
                "acceptance": ["UI displays 42"],
                "reads": ["ui.txt"],
                "files": ["ui.txt"],
            },
        ],
    }


@pytest.fixture
def repo(tmp_path):
    make_repo(tmp_path)
    for name, value in {
        "seed.py": "# service convention\n",
        "endpoint.py": "result = None\n",
        "ui.txt": "pending\n",
    }.items():
        (tmp_path / name).write_text(value)
    return tmp_path


def results(repo, paths):
    return [
        make_result(
            path=p,
            name="value" if p == "service.py" else "context",
            code=(repo / p).read_text(),
            start_line=1,
            end_line=2,
        )
        for p in paths
    ]


def feature(repo, cfg=None):
    return build_feature_draft(
        FeaturePlan.model_validate(plan_data()),
        query="Build service, endpoint and UI",
        repo=repo,
        model="local",
        mode="review",
        evidence=[
            context_record(r)
            for r in results(repo, ["seed.py", "endpoint.py", "ui.txt"])
        ],
        config=cfg or config(),
    )


def controlled(
    monkeypatch, repo, *, scope_error=False, omit_input=False, stop_after=None
):
    calls, retrieved = [], []
    outputs = [
        {
            "items": [
                {"label": "Read", "target": "seed.py", "reason": "Convention"},
                {
                    "label": "New",
                    "target": "service.py",
                    "reason": "Create service module",
                },
            ]
        },
        {
            "items": [
                {"label": "Read", "target": "service.py", "reason": "Use service"},
                {"label": "Modify", "target": "endpoint.py", "reason": "Wire endpoint"},
            ]
        },
        {"items": [{"label": "Modify", "target": "ui.txt", "reason": "Display value"}]},
    ]
    if scope_error:
        outputs[0]["items"][0]["label"] = "Modify"

    def client(*args, **kwargs):
        c = FakeClient(json.dumps(outputs.pop(0)))
        calls.append(c)
        return nullcontext(c)

    monkeypatch.setattr("devstill.feature_runs.FeatureClient", client)

    def mutate(workspace):
        if (workspace / "seed.py").exists():
            (workspace / "service.py").write_text("def value():\n    return 42\n")
        elif (workspace / "endpoint.py").exists():
            assert "return 42" in (workspace / "service.py").read_text()
            (workspace / "endpoint.py").write_text(
                "from service import value\nresult = value()\n"
            )
        else:
            (workspace / "ui.txt").write_text("42\n")
        if stop_after:
            stop_after(workspace)

    runner = FakePiRunner(mutate)
    monkeypatch.setattr("devstill.feature_runs.FeaturePiRunner", lambda budget: runner)

    def retrieve(query):
        retrieved.append(query)
        if query.startswith("Create"):
            paths = ["seed.py"]
        elif query.startswith("Wire"):
            assert (repo / "service.py").exists()
            assert (repo / ".devstill/.tasks-need-index").exists()
            paths = ["endpoint.py"] if omit_input else ["service.py", "endpoint.py"]
        else:
            paths = ["ui.txt"]
        return results(repo, paths)

    return retrieve, runner, calls, retrieved


def test_serial_composition_uses_real_quick_and_new_predecessor_source(
    repo, monkeypatch
):
    check = f"{sys.executable} -c \"from endpoint import result; from pathlib import Path; assert result == 42; assert Path('ui.txt').read_text().strip() == '42'\""
    cfg = config(context_window=12288, validation_commands=[check])
    cfg.feature.task_checks = []  # Full integration runs only after all children.
    value = feature(repo, cfg)
    root = repo / ".devstill"
    save_feature(root, value)
    assert load_feature(root, value.id) == value
    retrieve, runner, clients, queries = controlled(monkeypatch, repo)
    state = run_feature(
        value, repo=repo, artifact_root=root, config=cfg, retrieve=retrieve
    )
    assert state["status"] == "validated", state
    assert runner.calls == 3
    assert len(queries) == 3
    assert all(
        len(c.calls) == 1 and c.calls[0][1]["schema_name"] == "devstill_plan"
        for c in clients
    )
    for t in state["tasks"].values():
        saved = json.loads((root / "tasks" / t["quick_id"] / "task.json").read_text())
        assert saved["engine"] == "quick"
    assert "Wire backend endpoint" in runner.prompts[1]
    assert "Update UI" not in runner.prompts[1]  # No sibling prompt/context leak.
    assert state["validation"][0]["returncode"] == 0
    with pytest.raises(ValueError, match="already started"):
        run_feature(value, repo=repo, artifact_root=root, config=cfg, retrieve=retrieve)


@pytest.mark.parametrize(
    "problem",
    [
        "scope",
        "coding",
        "local_check",
        "final_check",
        "cancel",
        "outside_write",
    ],
)
def test_failure_preserves_partial_work_and_never_releases_dependents(
    repo, monkeypatch, problem
):
    cfg = config(context_window=12288)
    if problem == "local_check":
        cfg.executor.validation_commands = [
            f'{sys.executable} -c "raise SystemExit(1)"'
        ]
    if problem == "final_check":
        cfg.feature.task_checks = []
        cfg.executor.validation_commands = [
            f'{sys.executable} -c "raise SystemExit(1)"'
        ]
    value = feature(repo, cfg)
    cancelled = False

    def stop(workspace):
        nonlocal cancelled
        if problem == "cancel":
            cancelled = True
        if problem == "outside_write":
            (repo / "ui.txt").write_text("someone else's edit\n")
        if problem == "coding":
            raise RuntimeError("controlled coding failure")

    retrieve, runner, _, _ = controlled(
        monkeypatch,
        repo,
        scope_error=problem == "scope",
        omit_input=problem == "missing_input",
        stop_after=stop,
    )
    state = run_feature(
        value,
        repo=repo,
        artifact_root=repo / ".devstill",
        config=cfg,
        retrieve=retrieve,
        cancelled=lambda: cancelled,
    )
    assert state["status"] in {"failed", "cancelled"}, state
    assert state["tasks"]["t3"]["status"] == (
        "applied_unvalidated" if problem == "final_check" else "blocked"
    )
    if problem != "final_check":
        assert (repo / "endpoint.py").read_text() == "result = None\n"
    if problem in {"scope", "coding", "cancel", "outside_write"}:
        assert not (repo / "service.py").exists()
    else:
        assert (repo / "service.py").exists()
    if problem == "scope":
        assert runner.calls == 0


@pytest.mark.parametrize(
    "mutation,match",
    [
        (lambda p: p["tasks"][0].update(depends_on=[2]), "cycle"),
        (lambda p: p["tasks"][1].update(depends_on=[]), "ancestor"),
        (lambda p: p["tasks"][1]["inputs"][0].update(producer=0), "producer"),
        (
            lambda p: p["tasks"][2]["writes"][0].update(path="endpoint.py"),
            "Conflicting",
        ),
        (lambda p: p["tasks"][2].update(requirements=[1]), "requirement"),
        (
            lambda p: p["tasks"][0]["writes"][0].update(path="../escape"),
            "repository-relative",
        ),
        (lambda p: p["contracts"][0].update(owner=1), "Contract consumer"),
    ],
)
def test_invalid_graphs_are_rejected(mutation, match):
    data = plan_data()
    mutation(data)
    with pytest.raises(ValueError, match=match):
        FeaturePlan.model_validate(data)


def test_coordinator_generates_once_and_does_not_modify_source(repo):
    client = FakeClient(json.dumps(proposal_data()))
    client.config = config().llm
    before = (repo / "endpoint.py").read_text()
    draft = create_feature_draft(
        client,
        query="Build feature",
        repo=repo,
        candidates=results(repo, ["seed.py", "endpoint.py", "ui.txt"]),
        mode="review",
        config=config(),
    )
    assert len(client.calls) == 1
    assert client.calls[0][1]["schema_name"] == "devstill_feature"
    assert len(draft.plan.tasks) == 3
    assert (repo / "endpoint.py").read_text() == before
    assert not (repo / "service.py").exists()


def test_coordinator_has_only_one_schema_repair(repo):
    client = FakeClient("bad", "bad")
    client.config = config().llm
    with pytest.raises(ValueError, match="one repair"):
        create_feature_draft(
            client,
            query="Build feature",
            repo=repo,
            candidates=results(repo, ["seed.py"]),
            mode="review",
            config=config(),
        )
    assert len(client.calls) == 2
    assert all(c[1]["max_attempts"] == 1 for c in client.calls)


@pytest.mark.parametrize("problem", ["source", "policy", "deadline", "plan"])
def test_preconditions_stop_before_child_calls(repo, problem):
    cfg = config()
    draft = feature(repo, cfg)
    root = repo / ".devstill"
    save_feature(root, draft)
    if problem == "source":
        (repo / "endpoint.py").write_text("external change")
    elif problem == "policy":
        cfg.executor.validation_commands = ["false"]
    elif problem == "deadline":
        draft.discovery_seconds = 181
    else:
        draft.query = "Changed request"
    with pytest.raises((ValueError, TimeoutError)):
        run_feature(
            draft,
            repo=repo,
            artifact_root=root,
            config=cfg,
            retrieve=lambda q: pytest.fail("must not retrieve"),
        )


def test_budget_charges_active_work_and_excludes_validation(monkeypatch):
    clock = [10.0]
    monkeypatch.setattr("devstill.feature_planning.time.monotonic", lambda: clock[0])
    budget = FeatureBudget(10, spent=2)
    assert budget.remaining() == 8
    with budget.validation():
        clock[0] += 5
    assert budget.remaining() == 8
    clock[0] += 9
    with pytest.raises(TimeoutError):
        budget.remaining()


def test_feature_http_requests_use_remaining_deadline(monkeypatch):
    import httpx

    from devstill.feature_planning import FeatureClient

    now = [0.0]
    monkeypatch.setattr("devstill.feature_planning.time.monotonic", lambda: now[0])
    observed = []

    def respond(request):
        observed.append(request.extensions["timeout"]["read"])
        now[0] += 1
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}]},
        )

    with (
        httpx.Client(transport=httpx.MockTransport(respond)) as http,
        FeatureClient(config().llm, budget=FeatureBudget(3), client=http) as client,
    ):
        client.chat([{"role": "user", "content": "one"}])
        client.chat([{"role": "user", "content": "two"}])
        with pytest.raises(TimeoutError):
            client.chat([{"role": "user", "content": "three"}])
    assert observed == [3, 2, 1]


def test_feature_pi_repairs_share_deadline(monkeypatch):
    from devstill.feature_runs import FeaturePiRunner
    from devstill.pi_runtime import PiRunner

    now = [0.0]
    monkeypatch.setattr("devstill.feature_planning.time.monotonic", lambda: now[0])
    limits = []

    def fake_run(self, **kwargs):
        limits.append(kwargs["executor"].timeout_seconds)
        now[0] += 2

    monkeypatch.setattr(PiRunner, "run", fake_run)
    runner = FeaturePiRunner(FeatureBudget(5))
    cfg = config()
    runner.run(executor=cfg.executor)
    runner.run(executor=cfg.executor)
    with pytest.raises(TimeoutError):
        runner.run(executor=cfg.executor)
    assert limits == [5, 3, 1]
    assert cfg.executor.timeout_seconds == 300


def test_required_predecessor_context_is_restored_when_semantic_search_misses_it(
    repo, monkeypatch
):
    value = feature(repo)
    retrieve, runner, _, _ = controlled(monkeypatch, repo, omit_input=True)
    state = run_feature(
        value,
        repo=repo,
        artifact_root=repo / ".devstill",
        config=config(),
        retrieve=retrieve,
    )
    assert state["status"] == "applied_unvalidated", state
    assert runner.calls == 3
    child = json.loads(
        (
            repo / ".devstill/tasks" / state["tasks"]["t2"]["quick_id"] / "task.json"
        ).read_text()
    )
    assert any(
        r["path"] == "service.py" and "return 42" in r["code"]
        for r in child["evidence"]
    )
