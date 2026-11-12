"""Task Review/YOLO presentation over the existing compact Plan/Pi primitive.

No task decomposition, new retrieval policy or second planning generation.
"""

from __future__ import annotations

import fcntl
import json
from uuid import uuid4

from devstill.context import context_record
from devstill.executor import RepositoryExecutor, _atomic_write, validate_execution_plan
from devstill.planning import (
    PlanOutput,
    generate_plan,
    render_plan_json,
    render_plan_markdown,
    select_planning_context,
    write_plan_artifacts,
)
from devstill.tasks import (
    DevstillTask,
    TaskDraft,
    TaskPlan,
    digest_file,
    task_directory,
    verify_draft_sources,
)


def create_quick_draft(client, *, query, repo, candidates, mode, excluded):
    selected = select_planning_context(
        candidates, client=client, query=query, repository=str(repo)
    )
    hashes = {}
    for result in selected:
        path = repo / result.chunk.path
        digest = digest_file(path)
        if result.source_hash and result.source_hash != digest:
            raise ValueError(
                f"Indexed source is stale: {result.chunk.path}. Run /index first."
            )
        hashes[result.chunk.path] = digest
    plan = generate_plan(
        client,
        query=query,
        repository=str(repo),
        repo_root=repo,
        results=selected,
        retrieval_exclude=excluded,
    )
    # Only presentation metadata is synthesized here. The original request and
    # original file-impact classification are what the Quick executor receives.
    draft = TaskDraft(
        engine="quick",
        id=uuid4().hex,
        query=query,
        repository=str(repo.resolve()),
        model=plan.model,
        mode=mode,
        plan=TaskPlan(
            tasks=[
                DevstillTask(
                    id="t1",
                    title=query.strip()[:160],
                    instruction="Carry out the original request within the reviewed file scope.",
                    acceptance=[
                        "Complete the requested change and run configured checks."
                    ],
                    items=plan.items,
                )
            ]
        ),
        source_hashes=hashes,
        evidence=[context_record(r) for r in selected],
    )
    verify_draft_sources(draft, repo)
    return draft


def quick_plan(draft: TaskDraft) -> PlanOutput:
    if (
        draft.engine != "quick"
        or len(draft.plan.tasks) != 1
        or draft.plan.tasks[0].depends_on
    ):
        raise ValueError("Quick executes one task without a coordinating plan")
    return PlanOutput(
        query=draft.query,
        repository=draft.repository,
        model=draft.model,
        items=draft.plan.tasks[0].items,
    )


def run_quick_task(
    draft, *, repo, artifact_root, config, progress=None, cancelled=None, runner=None
):
    report = progress or (lambda message: None)
    is_cancelled = cancelled or (lambda: False)
    if not config.llm.enabled or not config.llm.model:
        raise ValueError("Agent is disabled or not configured")
    if not config.executor.enabled:
        raise ValueError("Task execution is disabled in devstill.json")
    if is_cancelled():
        raise ValueError("Task run was cancelled")
    if draft.plan.unresolved:
        raise ValueError("Task is blocked: " + "; ".join(draft.plan.unresolved))
    plan = quick_plan(draft)
    directory = task_directory(artifact_root, draft.id)
    state_path = directory / "run.json"
    artifact_root.mkdir(parents=True, exist_ok=True)
    with (artifact_root / ".task-writer.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError("Another task run is writing this repository") from exc
        if state_path.exists():
            raise ValueError("This task run has already started; create a fresh task")
        verify_draft_sources(draft, repo)
        validate_execution_plan(plan, repo, config.retrieval.exclude)
        state = {
            "id": draft.id,
            "engine": "quick",
            "mode": draft.mode,
            "status": "running",
            "tasks": {"t1": {"status": "running"}},
        }

        def save():
            from devstill.task_runs import render_task_run

            _atomic_write(state_path, json.dumps(state, indent=2) + "\n")
            _atomic_write(directory / "result.md", render_task_run(state))

        save()
        # Per-run artifacts avoid overwriting a separate /plan review.
        root = directory / "quick"
        prepared = None
        try:
            write_plan_artifacts(
                root, render_plan_markdown(plan), render_plan_json(plan)
            )
            executor = RepositoryExecutor(repo, root, config, runner=runner)
            report("Running Quick with the existing Pi executor")
            prepared = executor.prepare(source="plan", progress=report)
            if is_cancelled():
                state["status"] = "cancelled"
                state["tasks"]["t1"] = {"status": "cancelled"}
            else:
                verify_draft_sources(draft, repo)
                if prepared.patch:
                    _atomic_write(artifact_root / ".tasks-need-index", draft.id + "\n")
                result = executor.apply(prepared, progress=report)
                status = (
                    "failed"
                    if result.status == "validation_failed"
                    else ("validated" if result.validation else "applied_unvalidated")
                )
                state["status"] = status
                state["tasks"]["t1"] = {
                    "status": status,
                    "changed_files": result.changed_files,
                    "execution": result.to_dict(),
                }
        except Exception as exc:  # noqa: BLE001 - persist execution failures
            state["status"] = "failed"
            state["tasks"]["t1"] = {"status": "failed", "error": str(exc)}
        except BaseException as exc:
            state["status"] = "interrupted"
            state["error"] = str(exc) or type(exc).__name__
            raise
        finally:
            if prepared is not None:
                prepared.close()
            save()
        return state
