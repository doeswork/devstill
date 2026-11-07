"""Bounded task generation with ordered application and per-run provenance."""

from __future__ import annotations

import fcntl
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Lock

from devstill.agent import AgentClient
from devstill.config import DevstillConfig
from devstill.executor import (
    PreparedExecution,
    _atomic_write,
    _safe_target,
    run_validation_commands,
)
from devstill.planning import PlanLabel
from devstill.task_execution import TaskExecutor
from devstill.tasks import (
    TaskDraft,
    digest_file,
    task_directory,
    validate_task_sources,
    verify_draft_sources,
)


def render_task_run(state: dict) -> str:
    lines = [
        f"# Task run · {state['status']}",
        "",
        f"Run: `{state['id']}`",
        "",
        "| Task | State | Changes |",
        "| --- | --- | --- |",
    ]
    for task_id, result in state["tasks"].items():
        files = ", ".join(f"`{p}`" for p in result.get("changed_files", [])) or "—"
        lines.append(f"| {task_id} | **{result['status']}** | {files} |")
    for task_id, result in state["tasks"].items():
        if result.get("summary"):
            lines += ["", f"### {task_id}", "", result["summary"]]
        if result.get("error"):
            lines += ["", f"{task_id}: {result['error']}", ""]
        for check in result.get("execution", {}).get("validation", []):
            outcome = "passed" if check["returncode"] == 0 else "failed"
            lines += ["", f"Check {outcome}: `{check['command']}`"]
            if check["returncode"] and check["output"]:
                lines += [
                    "",
                    *["> " + line for line in check["output"].splitlines()[:30]],
                ]
    if state.get("engine") == "quick":
        lines += [
            "",
            "Quick uses the existing bounded Pi execution policy.",
            "Configured validation results appear above; without checks, changes remain unvalidated.",
        ]
    else:
        lines += [
            "",
            f"Coding generation attempts: **{state['coding_attempts']}**. No automatic repair or replanning.",
            "Acceptance conditions are review criteria; only configured validation commands have been executed.",
        ]
    if state.get("error"):
        lines += ["", state["error"]]
    for check in state.get("validation", []):
        outcome = "passed" if check["returncode"] == 0 else "failed"
        lines += ["", f"Combined check {outcome}: `{check['command']}`"]
        if check["returncode"] and check["output"]:
            lines += ["", *["> " + line for line in check["output"].splitlines()[:30]]]
    if (
        state["status"] in {"failed", "blocked", "partial"}
        and state.get("engine") != "quick"
    ):
        lines += [
            "",
            "Earlier applied task changes remain in the working tree. Inspect the saved patches before starting another run.",
        ]
    return "\n".join(lines) + "\n"


def run_tasks(
    draft: TaskDraft,
    *,
    repo: Path,
    artifact_root: Path,
    config: DevstillConfig,
    progress=None,
    cancelled=None,
) -> dict:
    if draft.engine == "quick":
        from devstill.quick_tasks import run_quick_task

        return run_quick_task(
            draft,
            repo=repo,
            artifact_root=artifact_root,
            config=config,
            progress=progress,
            cancelled=cancelled,
        )
    report = progress or (lambda message: None)
    if not config.llm.enabled or not config.llm.model:
        raise ValueError(
            "Agent is disabled or not configured; task execution was not started"
        )
    if not config.executor.enabled:
        raise ValueError("Task execution is disabled in devstill.json")
    is_cancelled = cancelled or (lambda: False)
    if is_cancelled():
        raise ValueError("Task run was cancelled")
    directory = task_directory(artifact_root, draft.id)
    state_path = directory / "run.json"
    # A persistent inode plus flock is released after a crash. It serializes
    # Devstill task writers in this repository without deleting another lock.
    artifact_root.mkdir(parents=True, exist_ok=True)
    with (artifact_root / ".task-writer.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError("Another task run is writing this repository") from exc
        if state_path.exists():
            raise ValueError(
                "This task run has already started. Review its results and create a fresh task; runs are not replayed automatically."
            )
        if draft.plan.unresolved:
            raise ValueError("Task is blocked: " + "; ".join(draft.plan.unresolved))
        verify_draft_sources(draft, repo)
        validate_task_sources(
            draft.plan, repo, set(draft.source_hashes), config.retrieval.exclude
        )
        state = {
            "id": draft.id,
            "mode": draft.mode,
            "status": "running",
            "coding_attempts": 0,
            "tasks": {t.id: {"status": "waiting"} for t in draft.plan.tasks},
        }
        expected = dict(draft.source_hashes)
        absent = {
            i.target
            for t in draft.plan.tasks
            for i in t.items
            if i.label == PlanLabel.NEW
        }

        def save():
            _atomic_write(state_path, json.dumps(state, indent=2) + "\n")
            _atomic_write(directory / "result.md", render_task_run(state))

        pending: dict[str, PreparedExecution | Exception] = {}
        progress_lock = Lock()

        def check_inputs():
            if is_cancelled():
                state["cancelled"] = True
                raise ValueError("Task run cancelled before applying further changes")
            for path, wanted in expected.items():
                current = _safe_target(repo, path, must_exist=True)
                if digest_file(current) != wanted:
                    raise ValueError(f"Task input changed outside this run: {path}")
            for path in absent:
                _safe_target(repo, path, must_exist=False)

        def prepare_one(task):
            root = directory / task.id

            def coding_progress(message):
                with progress_lock:
                    if message == f"{task.id}: generating one edit response":
                        state["coding_attempts"] += 1
                        save()
                    report(message)

            with AgentClient(config.llm, log_path=root / "agent.jsonl") as client:
                return TaskExecutor(repo, root, config).prepare(
                    draft, task, client, coding_progress
                )

        save()
        try:
            remaining = {t.id: t for t in draft.plan.tasks}
            successful: set[str] = set()
            while remaining:
                ready = [
                    t for t in remaining.values() if set(t.depends_on) <= successful
                ]
                if not ready:
                    for task_id in remaining:
                        state["tasks"][task_id] = {
                            "status": "blocked",
                            "error": "A required predecessor did not complete",
                        }
                    break
                if (
                    not pending
                    and config.executor.task_concurrency > 1
                    and len(ready) > 1
                ):
                    check_inputs()
                    batch = ready[: config.executor.task_concurrency]
                    for task in batch:
                        state["tasks"][task.id] = {"status": "generating"}
                    save()
                    report(
                        f"Generating {len(batch)} independent tasks in isolated workspaces"
                    )
                    with ThreadPoolExecutor(max_workers=len(batch)) as pool:
                        futures = [
                            (task.id, pool.submit(prepare_one, task)) for task in batch
                        ]
                        for task_id, future in futures:
                            try:
                                pending[task_id] = future.result()
                            except Exception as exc:  # noqa: BLE001 - collect every workspace for cleanup
                                pending[task_id] = exc
                task = next((t for t in ready if t.id in pending), ready[0])
                report(
                    f"{task.id}: {task.title} · {len(ready)} task(s) ready; executing in dependency order"
                )
                state["tasks"][task.id] = {"status": "running"}
                save()
                prepared = None
                try:
                    check_inputs()
                    root = directory / task.id
                    candidate = (
                        pending.pop(task.id)
                        if task.id in pending
                        else prepare_one(task)
                    )
                    if isinstance(candidate, Exception):
                        raise candidate
                    prepared = candidate
                    check_inputs()
                    # Persist freshness before application, including interrupted runs.
                    if prepared.patch:
                        _atomic_write(
                            artifact_root / ".tasks-need-index", draft.id + "\n"
                        )
                    result = TaskExecutor(repo, root, config).apply(
                        prepared, progress=report
                    )
                    status = (
                        "failed"
                        if result.status == "validation_failed"
                        else (
                            "validated" if result.validation else "applied_unvalidated"
                        )
                    )
                    state["tasks"][task.id] = {
                        "status": status,
                        "summary": json.loads((root / "response.json").read_text())[
                            "summary"
                        ],
                        "changed_files": result.changed_files,
                        "execution": result.to_dict(),
                    }
                    if status != "failed":
                        successful.add(task.id)
                        # Refresh all approved paths, including Read files: those
                        # must not change unexpectedly due to validation commands.
                        for path in result.changed_files:
                            absent.discard(path)
                            if (repo / path).is_file():
                                expected[path] = digest_file(repo / path)
                            else:
                                expected.pop(path, None)
                                absent.add(path)
                except Exception as exc:  # noqa: BLE001 - persist task failures without retry
                    state["tasks"][task.id] = {"status": "failed", "error": str(exc)}
                finally:
                    if prepared is not None:
                        prepared.close()
                remaining.pop(task.id)
                save()
                if state["tasks"][task.id]["status"] == "failed":
                    for task_id in remaining:
                        state["tasks"][task_id] = {
                            "status": "blocked",
                            "error": "Run stopped after a failed task",
                        }
                    break
            statuses = [r["status"] for r in state["tasks"].values()]
            if not any(s in {"failed", "blocked"} for s in statuses):
                report("Checking the combined task outcome")
                state["validation"] = [
                    v.to_dict()
                    for v in run_validation_commands(
                        repo,
                        config.executor.validation_commands,
                        config.executor.timeout_seconds,
                        report,
                    )
                ]
            combined = state.get("validation", [])
            state["status"] = (
                "failed"
                if any(s in {"failed", "blocked"} for s in statuses)
                or any(v["returncode"] for v in combined)
                else (
                    "validated"
                    if combined or (len(statuses) == 1 and statuses[0] == "validated")
                    else "applied_unvalidated"
                )
            )
            if state.get("cancelled"):
                state["status"] = "cancelled"
        except BaseException as exc:
            state["status"] = "cancelled" if state.get("cancelled") else "interrupted"
            state["error"] = str(exc) or type(exc).__name__
            save()
            raise
        finally:
            for candidate in pending.values():
                if isinstance(candidate, PreparedExecution):
                    candidate.close()
        save()
        return state
