"""Serial composition of unchanged Quick tasks, with durable scope and source checks."""

from __future__ import annotations

import fcntl
import hashlib
import json
import re
import time
from pathlib import Path

from devstill.executor import _atomic_write, _safe_target, run_validation_commands
from devstill.feature_context import task_candidates
from devstill.feature_planning import (
    FeatureBudget,
    FeatureClient,
    child_request,
    feature_directory,
    policy_hash,
    save_feature,
    verify_sources,
)
from devstill.pi_runtime import PiRunner
from devstill.planning import PlanLabel
from devstill.quick_tasks import create_quick_draft, run_quick_task
from devstill.tasks import digest_file, save_task_draft


class FeaturePiRunner(PiRunner):
    def __init__(self, budget):
        super().__init__()
        self.budget = budget

    def run(self, **kwargs):
        remaining = self.budget.remaining()
        kwargs["executor"] = kwargs["executor"].model_copy(
            update={
                "timeout_seconds": min(kwargs["executor"].timeout_seconds, remaining)
            }
        )
        progress = kwargs.get("progress") or (lambda message: None)

        def bounded_progress(message):
            self.budget.remaining()
            progress(message)

        kwargs["progress"] = bounded_progress
        result = super().run(**kwargs)
        self.budget.remaining()
        return result


def check_child_scope(draft, number, child, completed=()):
    task = draft.plan.tasks[number - 1]
    allowed = {w.path: w.operation for w in task.writes}
    actual = child.plan.tasks[0].items
    for item in actual:
        if item.label == PlanLabel.READ:
            continue
        if item.label == PlanLabel.SEARCH:
            raise ValueError(f"Quick needs more context: {item.target}")
        operation = (
            "create"
            if item.label == PlanLabel.NEW
            else "delete"
            if item.label == PlanLabel.DELETE
            else "update"
        )
        if allowed.get(item.target) != operation:
            raise ValueError(
                f"Quick exceeds reviewed feature scope: {operation} {item.target}"
            )
    read = {i.target for i in actual if i.label != PlanLabel.NEW}
    observed = []
    for other_n, other in enumerate(draft.plan.tasks, 1):
        if (
            other_n == number
            or other_n in draft.plan.ancestors(number)
            or number in draft.plan.ancestors(other_n)
        ):
            continue
        if read & {w.path for w in other.writes}:
            if other_n in completed:
                observed.append(other_n)
                continue
            raise ValueError(
                f"Actual Quick inputs conflict with independent T{other_n}"
            )
    for inp in task.inputs:
        records = [r for r in child.evidence if r["path"] == inp.path]
        if not records or (
            inp.symbol
            and not any(
                r.get("symbol") == inp.symbol or inp.symbol in r.get("code", "")
                for r in records
            )
        ):
            raise ValueError(
                f"Required input absent from compact context: {inp.path}:{inp.symbol}"
            )
    return observed


def commands(config, ids):
    if ids is None:
        return list(config.executor.validation_commands)
    return list(dict.fromkeys(c for key in ids for c in config.feature.checks[key]))


def render_feature_run(state):
    lines = [
        f"# Feature · {state['status']}",
        "",
        f"Run: `{state['id']}`",
        "",
        "| Task | State | Quick run |",
        "| --- | --- | --- |",
    ]
    for name, task in state["tasks"].items():
        lines.append(f"| {name} | {task['status']} | {task.get('quick_id', '—')} |")
        if task.get("error"):
            lines += ["", f"{name}: {task['error']}", ""]
        if task.get("impacts"):
            lines += ["", f"### {name} file impacts", ""]
            lines += [
                f"- {i['label']} `{i['target']}`: {i['reason']}"
                for i in task["impacts"]
            ]
        for check in task.get("validation", []):
            lines += [
                "",
                f"{name} check {'passed' if check['returncode'] == 0 else 'failed'}: `{check['command']}`",
            ]
    for check in state.get("validation", []):
        lines += [
            "",
            f"Feature check {'passed' if check['returncode'] == 0 else 'failed'}: `{check['command']}`",
        ]
    if state.get("error"):
        lines += ["", state["error"]]
    lines += [
        "",
        "Child reviews, patches and logs are saved under `.devstill/tasks/QUICK_ID/`.",
        "Applied changes remain available when a feature stops. Validation reports configured checks only.",
    ]
    return "\n".join(lines) + "\n"


def run_feature(
    draft, *, repo, artifact_root, config, retrieve, progress=None, cancelled=None
):
    report = progress or (lambda message: None)
    cancelled = cancelled or (lambda: False)
    if not config.llm.enabled or not config.llm.model or not config.executor.enabled:
        raise ValueError("Feature execution requires the configured agent and executor")
    if Path(draft.repository).resolve() != repo.resolve():
        raise ValueError("Feature belongs to another repository")
    if draft.policy_hash != policy_hash(config):
        raise ValueError(
            "Feature configuration changed since review; create a fresh feature"
        )
    if draft.plan.unresolved:
        raise ValueError("Feature blocked: " + "; ".join(draft.plan.unresolved))
    budget = FeatureBudget(
        config.feature.timeout_seconds, draft.discovery_seconds, cancelled
    )
    budget.remaining()
    directory = feature_directory(artifact_root, draft.id)
    if not (directory / "feature.json").exists():
        save_feature(artifact_root, draft)
    saved = json.loads((directory / "feature.json").read_text())
    if saved != draft.model_dump(mode="json"):
        raise ValueError("Feature plan differs from its saved review")
    artifact_root.mkdir(parents=True, exist_ok=True)
    with (artifact_root / ".feature-run.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError("Another feature is already running") from exc
        if (directory / "run.json").exists():
            raise ValueError(
                "Feature already started; inspect its results and create a fresh run"
            )
        expected = dict(draft.source_hashes)
        verify_sources(repo, expected)
        state = {
            "id": draft.id,
            "status": "running",
            "engine": "composed_quick",
            "mode": draft.mode,
            "plan_sha256": hashlib.sha256(draft.model_dump_json().encode()).hexdigest(),
            "discovery_seconds": draft.discovery_seconds,
            "tasks": {
                f"t{n}": {"status": "waiting"}
                for n in range(1, len(draft.plan.tasks) + 1)
            },
        }

        def save():
            state["active_seconds"] = budget.spent
            state["source_hashes"] = expected
            _atomic_write(directory / "run.json", json.dumps(state, indent=2) + "\n")
            _atomic_write(directory / "result.md", render_feature_run(state))

        save()
        done, active = set(), None
        try:
            while len(done) < len(draft.plan.tasks):
                budget.remaining()
                verify_sources(repo, expected)
                number = next(
                    n
                    for n, t in enumerate(draft.plan.tasks, 1)
                    if n not in done and set(t.depends_on) <= done
                )
                active = state["tasks"][f"t{number}"]
                task = draft.plan.tasks[number - 1]
                active["status"] = "preparing"
                save()
                report(f"T{number}: retrieving current context — {task.outcome}")
                query = child_request(draft, number)
                started = time.monotonic()
                candidates = task_candidates(repo, task, retrieve(task.outcome))
                active["retrieval_seconds"] = time.monotonic() - started
                budget.remaining()
                # Index refresh must not silently accept another writer's changes.
                verify_sources(repo, expected)
                started = time.monotonic()
                with FeatureClient(
                    config.llm,
                    budget=budget,
                    log_path=directory / f"t{number}-definition.jsonl",
                ) as client:
                    child = create_quick_draft(
                        client,
                        query=query,
                        repo=repo,
                        candidates=candidates,
                        mode=draft.mode,
                        excluded=config.retrieval.exclude,
                    )
                active["classification_seconds"] = time.monotonic() - started
                save_task_draft(artifact_root, child)
                active["quick_id"] = child.id
                active["impacts"] = [
                    i.model_dump(mode="json") for i in child.plan.tasks[0].items
                ]
                active["observed_dependencies"] = check_child_scope(
                    draft, number, child, done
                )
                for path, value in child.source_hashes.items():
                    expected.setdefault(path, value)
                verify_sources(repo, expected)
                remaining = budget.remaining()
                child_config = config.model_copy(deep=True)
                child_config.executor.timeout_seconds = min(
                    config.executor.timeout_seconds, remaining
                )
                child_config.llm.timeout_seconds = min(
                    config.llm.timeout_seconds, remaining
                )
                # Feature-local checks are timed outside the active execution budget.
                # Quick's coding path is unchanged; the coordinator owns check status.
                child_config.executor.validation_commands = []
                active["status"] = "running"
                save()
                started = time.monotonic()

                def stop_apply(active=active):
                    try:
                        budget.remaining()
                        verify_sources(repo, expected)
                        return False
                    except (ValueError, TimeoutError) as exc:
                        active["error"] = str(exc)
                        return True

                result = run_quick_task(
                    child,
                    repo=repo,
                    artifact_root=artifact_root,
                    config=child_config,
                    progress=report,
                    cancelled=stop_apply,
                    runner=FeaturePiRunner(budget),
                )
                active["coding_seconds"] = time.monotonic() - started
                active["execution"] = result
                if result["status"] not in {"validated", "applied_unvalidated"}:
                    raise ValueError(
                        active.get("error") or json.dumps(result["tasks"]["t1"])
                    )
                changed = result["tasks"]["t1"].get("changed_files", [])
                active["changed_files"] = changed
                for path in changed:
                    target = repo / path
                    expected[path] = digest_file(target) if target.is_file() else None
                active["outputs"] = {}
                for out in task.outputs:
                    target = _safe_target(repo, out.path, must_exist=True)
                    if out.symbol and not re.search(
                        r"\b" + re.escape(out.symbol.rsplit(".", 1)[-1]) + r"\b",
                        target.read_text(),
                    ):
                        raise ValueError(
                            f"Declared output symbol missing: {out.path}:{out.symbol}"
                        )
                    active["outputs"][out.path] = digest_file(target)
                checks = list(
                    dict.fromkeys(
                        commands(config, config.feature.task_checks)
                        + commands(config, task.checks)
                    )
                )
                with budget.validation():
                    started = time.monotonic()
                    validation = run_validation_commands(
                        repo, checks, config.executor.timeout_seconds, report
                    )
                    active["validation_seconds"] = time.monotonic() - started
                active["validation"] = [r.to_dict() for r in validation]
                if any(r.returncode for r in validation):
                    raise ValueError("Required task checks failed")
                budget.remaining()
                verify_sources(repo, expected)
                active["status"] = "validated" if validation else "applied_unvalidated"
                done.add(number)
                save()
                active = None
            budget.remaining()
            verify_sources(repo, expected)
            with budget.validation():
                started = time.monotonic()
                validation = run_validation_commands(
                    repo,
                    commands(config, config.feature.final_checks),
                    config.executor.timeout_seconds,
                    report,
                )
                state["validation_seconds"] = time.monotonic() - started
            state["validation"] = [r.to_dict() for r in validation]
            if cancelled():
                raise ValueError("Feature cancelled")
            verify_sources(repo, expected)
            state["status"] = (
                "failed"
                if any(r.returncode for r in validation)
                else "validated"
                if validation
                else "applied_unvalidated"
            )
        except Exception as exc:  # noqa: BLE001 - persist partial feature outcomes
            state["status"] = "cancelled" if cancelled() else "failed"
            state["error"] = str(exc)
            if active is not None:
                active["status"] = state["status"]
                active["error"] = str(exc)
        except BaseException:
            state["status"] = "interrupted"
            if active is not None:
                active["status"] = "interrupted"
            raise
        finally:
            for value in state["tasks"].values():
                if value["status"] == "waiting":
                    value["status"] = "blocked"
            save()
        return state
