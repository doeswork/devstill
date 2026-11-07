"""The Task interface must preserve Quick's prompts, scope and executor behavior."""

from __future__ import annotations

import json
import sys

import pytest

from devstill.executor import RepositoryExecutor
from devstill.planning import (
    generate_plan,
    render_plan_json,
    render_plan_markdown,
    select_planning_context,
    write_plan_artifacts,
)
from devstill.quick_tasks import create_quick_draft, quick_plan
from devstill.task_runs import run_tasks
from devstill.tasks import TaskDraft, load_task_draft, save_task_draft
from tests.test_context import make_result
from tests.test_executor import FakePiRunner
from tests.test_planning import FakeClient
from tests.test_tasks import config, draft, make_repo


@pytest.fixture
def repo(tmp_path):
    return make_repo(tmp_path)


@pytest.mark.parametrize("repair", [False, True])
def test_quick_review_preserves_exact_old_prompt_schema_and_plan(repo, repair):
    query = "Make the submit button blue"
    candidate = make_result(path="button.css", code=(repo / "button.css").read_text())
    response = json.dumps(
        {"items": [{"label": "Modify", "target": "button.css", "reason": "Use blue"}]}
    )
    responses = (["invalid JSON"] if repair else []) + [response]
    old, new = FakeClient(*responses), FakeClient(*responses)
    selected = select_planning_context(
        [candidate], client=old, query=query, repository=str(repo)
    )
    plan = generate_plan(
        old,
        query=query,
        repository=str(repo),
        repo_root=repo,
        results=selected,
        retrieval_exclude=[],
    )
    value = create_quick_draft(
        new, query=query, repo=repo, candidates=[candidate], mode="review", excluded=[]
    )
    assert new.calls == old.calls
    assert len(new.calls) == (2 if repair else 1)
    assert quick_plan(value) == plan
    root = repo / ".devstill"
    save_task_draft(root, value)
    assert quick_plan(load_task_draft(root, value.id)) == plan
    assert "green" in (repo / "button.css").read_text()


def quick(repo):
    return draft(repo).model_copy(update={"engine": "quick"})


def blue(workspace):
    (workspace / "button.css").write_text("button { color: blue; }\n")


def test_quick_uses_identical_pi_prompt_and_produces_same_patch(repo, monkeypatch):
    value = quick(repo)
    plan = quick_plan(value)
    cfg = config(context_window=12288)
    artifacts = repo / ".devstill"
    write_plan_artifacts(artifacts, render_plan_markdown(plan), render_plan_json(plan))
    runner = FakePiRunner(blue)
    executor = RepositoryExecutor(repo, artifacts, cfg, runner=runner)
    old = executor.prepare(source="plan")
    try:
        old_patch = old.patch
    finally:
        old.close()
    monkeypatch.setattr("devstill.executor.PiRunner", lambda: runner)
    directory = save_task_draft(artifacts, value)
    state = run_tasks(value, repo=repo, artifact_root=artifacts, config=cfg)
    assert state["status"] == "applied_unvalidated"
    assert runner.calls == 2
    assert runner.prompts[0] == runner.prompts[1]
    assert runner.provider_max_tokens[0] == runner.provider_max_tokens[1]
    assert (directory / "quick/.execute.patch").read_text() == old_patch
    assert "blue" in (repo / "button.css").read_text()
    assert (artifacts / ".tasks-need-index").exists()
    # Task-specific execution must not consume/overwrite another /plan review.
    assert json.loads((artifacts / ".plan.json").read_text()) == plan.model_dump(
        mode="json"
    )
    with pytest.raises(ValueError, match="already started"):
        run_tasks(value, repo=repo, artifact_root=artifacts, config=cfg)
    assert runner.calls == 2


@pytest.mark.parametrize("failure", [False, True])
def test_quick_runs_real_configured_validation_and_reports_failure(
    repo, monkeypatch, failure
):
    value = quick(repo)
    root = repo / ".devstill"
    save_task_draft(root, value)
    monkeypatch.setattr("devstill.executor.PiRunner", lambda: FakePiRunner(blue))
    check = (
        f'{sys.executable} -c "from pathlib import Path; assert '
        + ("False" if failure else "'blue' in Path('button.css').read_text()")
        + '"'
    )
    state = run_tasks(
        value,
        repo=repo,
        artifact_root=root,
        config=config(context_window=12288, validation_commands=[check]),
    )
    assert state["status"] == ("failed" if failure else "validated")
    assert state["tasks"]["t1"]["execution"]["validation"][0]["returncode"] == int(
        failure
    )
    assert "blue" in (repo / "button.css").read_text()


@pytest.mark.parametrize("change", ["cancel", "stale"])
def test_changes_during_quick_generation_prevent_application(repo, monkeypatch, change):
    value = quick(repo)
    root = repo / ".devstill"
    directory = save_task_draft(root, value)
    stopped = False

    def mutate(workspace):
        nonlocal stopped
        blue(workspace)
        if change == "stale":
            (repo / "button.css").write_text("button { color: red; }\n")
        stopped = True

    runner = FakePiRunner(mutate)
    monkeypatch.setattr("devstill.executor.PiRunner", lambda: runner)
    state = run_tasks(
        value,
        repo=repo,
        artifact_root=root,
        config=config(context_window=12288),
        cancelled=lambda: change == "cancel" and stopped,
    )
    assert state["status"] == ("cancelled" if change == "cancel" else "failed")
    assert "blue" not in (repo / "button.css").read_text()
    assert not (root / ".tasks-need-index").exists()
    assert json.loads((directory / "run.json").read_text()) == state


def test_quick_rejects_stale_index_before_model_call(repo):
    candidate = make_result(path="button.css")
    candidate.source_hash = "obsolete"
    client = FakeClient()
    with pytest.raises(ValueError, match="Indexed source is stale"):
        create_quick_draft(
            client,
            query="Make button blue",
            repo=repo,
            candidates=[candidate],
            mode="yolo",
            excluded=[],
        )
    assert not client.calls


def test_old_saved_drafts_keep_their_original_engine(repo):
    saved = draft(repo).model_dump()
    saved.pop("engine")
    assert TaskDraft.model_validate(saved).engine == "structured"
