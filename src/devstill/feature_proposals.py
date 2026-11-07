"""Compile short model task descriptions into a validated execution graph."""

from graphlib import CycleError, TopologicalSorter
from typing import Annotated

from pydantic import Field, StringConstraints

from devstill.executor import ExecutionError, _safe_target
from devstill.feature_planning import FeaturePath, FeaturePlan
from devstill.retrieval_policy import matches_retrieval_exclude
from devstill.tasks import TaskModel

Text = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1200)
]
Name = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)
]


class TaskBrief(TaskModel):
    name: Name
    outcome: Text
    acceptance: list[Text] = Field(min_length=1, max_length=6)
    files: list[str] = Field(default_factory=list, max_length=5)
    reads: list[str] = Field(default_factory=list, max_length=5)
    after: list[Name] = Field(default_factory=list, max_length=5)
    delete: list[str] = Field(default_factory=list, max_length=5)


class FeatureProposal(TaskModel):
    tasks: list[TaskBrief] = Field(default_factory=list, max_length=6)
    interfaces: list[Text] = Field(default_factory=list, max_length=8)
    blockers: list[Text] = Field(default_factory=list, max_length=8)


def compile_proposal(proposal, *, repo, evidence, config):
    """Assign all numeric references; infer file operations from current source.

    Explicit prerequisites take precedence. Otherwise declaration order resolves
    shared file access. A read of a new file is ordered after its first writer.
    Never expand write scope or turn an unknown reference into an independent task.
    """
    proposal = FeatureProposal.model_validate(proposal)
    briefs = proposal.tasks
    names = {t.name: n for n, t in enumerate(briefs)}
    if len(names) != len(briefs):
        raise ValueError("Task names must be unique")
    if not briefs and not proposal.blockers:
        raise ValueError("Describe at least one task or an explicit blocker")
    exists, records = {}, list(evidence)
    known = {r["path"] for r in records}
    for t in briefs:
        t.files = list(dict.fromkeys(t.files))
        t.reads = list(dict.fromkeys(t.reads))
        t.delete = list(dict.fromkeys(t.delete))
        if not t.files and not t.delete:
            raise ValueError(f"Task {t.name} needs concrete write files")
        if set(t.files) & set(t.delete):
            raise ValueError("A task cannot edit and delete the same file")
        for path in [*t.files, *t.reads, *t.delete]:
            FeaturePath(path=path)
            if matches_retrieval_exclude(path, config.retrieval.exclude):
                raise ValueError(f"Feature path is excluded: {path}")
            if path in exists:
                continue
            present = (repo / path).exists()
            try:
                target = _safe_target(repo, path, must_exist=present)
            except ExecutionError as exc:
                raise ValueError(str(exc)) from exc
            exists[path] = present
            if present and path not in known:
                if target.stat().st_size > 1024 * 1024:
                    raise ValueError(f"Feature source is too large: {path}")
                code = target.read_text()[:2000]
                records.append(
                    {
                        "path": path,
                        "lines": [1, code.count("\n") + 1],
                        "symbol": "",
                        "kind": "file",
                        "code": code,
                    }
                )
                known.add(path)
    edges = {}
    for n, t in enumerate(briefs):
        if set(t.after) - names.keys():
            raise ValueError(f"Unknown prerequisite for {t.name}: {t.after}")
        edges[n] = {names[name] for name in t.after}
        for path in t.reads:
            if not exists[path]:
                writers = [
                    m for m, b in enumerate(briefs) if path in b.files and m != n
                ]
                if not writers:
                    raise ValueError(f"Missing input has no producing task: {path}")
                edges[n].add(writers[0])
    try:
        tuple(TopologicalSorter(edges).static_order())
    except CycleError as exc:
        raise ValueError("Task prerequisites contain a cycle") from exc
    order = []
    while len(order) < len(briefs):
        order.append(
            next(
                n
                for n in range(len(briefs))
                if n not in order and edges[n] <= set(order)
            )
        )
    ids = {old: new for new, old in enumerate(order, 1)}
    tasks, latest, readers = [], {}, {}
    live = dict(exists)
    for old in order:
        t, number = briefs[old], ids[old]
        dependencies = {ids[p] for p in edges[old]}
        inputs, writes = [], []
        for path in t.reads:
            if not live[path]:
                raise ValueError(f"Input is absent at task {t.name}: {path}")
            producer = latest.get(path, 0)
            if producer:
                dependencies.add(producer)
            inputs.append({"path": path, "producer": producer})
        for path in [*t.files, *t.delete]:
            dependencies.update(readers.get(path, set()))
            if path in latest:
                dependencies.add(latest[path])
            operation = (
                "delete" if path in t.delete else "update" if live[path] else "create"
            )
            if operation == "delete" and not live[path]:
                raise ValueError(f"Cannot delete absent file: {path}")
            writes.append({"path": path, "operation": operation})
            live[path] = operation != "delete"
            latest[path] = number
        for path in t.reads:
            readers.setdefault(path, set()).add(number)
        tasks.append(
            {
                "outcome": t.outcome,
                "requirements": [number],
                "acceptance": t.acceptance,
                "depends_on": sorted(dependencies),
                "inputs": inputs,
                "writes": writes,
                "outputs": [{"path": p} for p in t.files],
                "contracts": list(range(1, len(proposal.interfaces) + 1)),
            }
        )
    return FeaturePlan.model_validate(
        {
            "requirements": [briefs[n].outcome for n in order]
            or ["Resolve feature blockers"],
            "contracts": [{"description": s} for s in proposal.interfaces],
            "tasks": tasks,
            "blockers": proposal.blockers,
        }
    ), records
