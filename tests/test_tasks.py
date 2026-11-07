from __future__ import annotations

import json
import subprocess
import sys
from typing import ClassVar
from uuid import uuid4

import pytest

from devstill.config import DevstillConfig
from devstill.planning import PlanItem, PlanLabel
from devstill.task_runs import run_tasks
from devstill.tasks import (
    DevstillTask,
    TaskDraft,
    TaskPlan,
    create_task_draft,
    digest_file,
    save_task_draft,
    validate_task_sources,
)
from tests.test_context import make_result


def item(path="button.css", label="Modify"):
    return PlanItem(label=label, target=path, reason="Use the requested color")


def task(task_id="t1", *, items=None, depends=()):
    return DevstillTask(
        id=task_id,
        title="Update button",
        instruction="Change green to blue",
        depends_on=list(depends),
        acceptance=["Submit button is blue"],
        items=items or [item()],
    )


def make_repo(tmp_path):
    subprocess.run(["git", "init", "--quiet", str(tmp_path)], check=True)
    (tmp_path / "button.css").write_text("button { color: green; }\n")
    return tmp_path


@pytest.fixture
def repo(tmp_path):
    return make_repo(tmp_path)


def draft(repo, tasks=None):
    return TaskDraft(
        id=uuid4().hex,
        query="Make the submit button blue",
        repository=str(repo),
        model="local",
        mode="review",
        plan=TaskPlan(
            shared_contract=["Keep existing markup"], tasks=tasks or [task()]
        ),
        source_hashes={"button.css": digest_file(repo / "button.css")},
        evidence=[],
    )


def compact(plan):
    return {
        "tasks": [
            {
                "instruction": t.instruction,
                "acceptance": t.acceptance,
                "depends_on": t.depends_on,
                "items": [
                    {"label": i.label.value, "target": i.target} for i in t.items
                ],
            }
            for t in plan.tasks
        ],
        "shared_contract": plan.shared_contract,
        "blockers": plan.blockers,
    }


def edits(old="green", new="blue", path="button.css", operation="replace"):
    return {
        "summary": "Updated the requested behavior",
        "edits": [{"path": path, "operation": operation, "old": old, "new": new}],
    }


class FakeClient:
    outputs: ClassVar[list] = []
    calls: ClassVar[list] = []

    def __init__(self, config, **kwargs):
        self.config = config

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def chat(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        assert kwargs["max_attempts"] == 1
        assert kwargs["reasoning"] == "off"
        response = self.outputs.pop(0)
        return response if isinstance(response, str) else json.dumps(response)


@pytest.fixture
def client(monkeypatch):
    FakeClient.outputs = []
    FakeClient.calls = []
    monkeypatch.setattr("devstill.task_runs.AgentClient", FakeClient)
    return FakeClient


def config(**executor):
    return DevstillConfig(llm={"enabled": True, "model": "local"}, executor=executor)


def execute(repo, value, client, outputs, cfg=None):
    root = repo / ".devstill"
    save_task_draft(root, value)
    client.outputs = outputs
    return run_tasks(value, repo=repo, artifact_root=root, config=cfg or config())


def test_review_has_one_generation_and_does_not_edit(repo, client):
    value = draft(repo)
    client.outputs = [compact(value.plan)]
    result = create_task_draft(
        client(config().llm),
        query=value.query,
        repo=repo,
        candidates=[
            make_result(
                path="button.css",
                code="button { color: green; }\n",
                start_line=1,
                end_line=1,
            )
        ],
        mode="review",
        excluded=[],
    )
    assert result.plan.tasks[0].items[0].label == PlanLabel.MODIFY
    assert (repo / "button.css").read_text() == "button { color: green; }\n"
    assert len(client.calls) == 1


def test_task_applies_one_structured_edit_and_records_unvalidated(repo, client):
    value = draft(repo)
    state = execute(repo, value, client, [edits()])
    assert state["status"] == "applied_unvalidated"
    assert state["coding_attempts"] == 1
    assert (repo / "button.css").read_text() == "button { color: blue; }\n"
    assert (repo / ".devstill/.tasks-need-index").exists()
    assert (repo / ".devstill/tasks" / value.id / "t1/.execute.patch").is_file()
    with pytest.raises(ValueError, match="already started"):
        run_tasks(value, repo=repo, artifact_root=repo / ".devstill", config=config())


def test_dependent_task_reads_accepted_predecessor_source(repo, client):
    value = draft(repo, [task(), task("t2", depends=["t1"])])
    state = execute(repo, value, client, [edits(), edits("blue", "navy")])
    assert state["coding_attempts"] == 2
    assert state["status"] == "applied_unvalidated"
    assert "color: blue" in client.calls[1][0][1]["content"]
    assert "color: navy" in (repo / "button.css").read_text()


def test_new_file_is_available_to_dependent_task(repo, client):
    a = task(items=[item("new.py", "New")])
    b = task("t2", items=[item("new.py")], depends=["t1"])
    value = draft(repo, [a, b])
    state = execute(
        repo,
        value,
        client,
        [edits("", "value = 1\n", "new.py", "create"), edits("1", "2", "new.py")],
    )
    assert state["status"] == "applied_unvalidated"
    assert (repo / "new.py").read_text() == "value = 2\n"


@pytest.mark.parametrize(
    "response",
    [
        edits(path="unapproved.py"),
        edits("missing", "blue"),
        {"summary": "Need more evidence", "edits": []},
        "not json",
    ],
)
def test_invalid_edits_do_not_touch_repository_or_retry(repo, client, response):
    value = draft(repo, [task(), task("t2", depends=["t1"])])
    state = execute(repo, value, client, [response])
    assert state["status"] == "failed"
    assert state["tasks"]["t2"]["status"] == "blocked"
    assert len(client.calls) == 1
    assert "green" in (repo / "button.css").read_text()
    assert not (repo / "unapproved.py").exists()


def test_ambiguous_anchor_is_not_applied(repo, client):
    (repo / "button.css").write_text("button { color: green; background: green; }")
    state = execute(repo, draft(repo), client, [edits()])
    assert state["status"] == "failed"
    assert "exactly once" in state["tasks"]["t1"]["error"]


def test_read_impact_cannot_be_edited(repo, client):
    value = draft(repo, [task(items=[item(label="Read")])])
    state = execute(repo, value, client, [edits()])
    assert state["status"] == "failed"
    assert "write scope" in state["tasks"]["t1"]["error"]


def test_review_snapshot_and_exclusions_are_checked_before_model(repo, client):
    value = draft(repo)
    (repo / "button.css").write_text("user edit\n")
    with pytest.raises(ValueError, match="changed since task review"):
        execute(repo, value, client, [edits()])
    assert not client.calls
    with pytest.raises(ValueError, match="excluded"):
        validate_task_sources(value.plan, repo, {"button.css"}, ["*.css"])


def test_validation_failure_retains_applied_patch_and_blocks_successor(repo, client):
    value = draft(repo, [task(), task("t2", depends=["t1"])])
    state = execute(
        repo,
        value,
        client,
        [edits()],
        config(task_validation_commands=[f'{sys.executable} -c "raise SystemExit(1)"']),
    )
    assert state["status"] == "failed"
    assert state["tasks"]["t1"]["execution"]["status"] == "validation_failed"
    assert state["tasks"]["t2"]["status"] == "blocked"
    assert "blue" in (repo / "button.css").read_text()
    assert len(client.calls) == 1


def test_validation_success_is_recorded(repo, client):
    check = f"{sys.executable} -c \"from pathlib import Path; assert chr(98)+chr(108)+chr(117)+chr(101) in Path('button.css').read_text()\""
    state = execute(
        repo, draft(repo), client, [edits()], config(validation_commands=[check])
    )
    assert state["status"] == "validated"


@pytest.mark.parametrize(
    "tasks",
    [
        [task(), task()],
        [task(depends=["t9"])],
        [task(depends=["t2"]), task("t2", depends=["t1"])],
        [task(), task("t2")],
        [task(), task("t2", items=[item(label="Read")])],
    ],
)
def test_invalid_graph_or_undeclared_conflict_is_rejected(tasks):
    with pytest.raises(ValueError):
        TaskPlan(tasks=tasks)


def test_independent_tasks_are_distinguished_from_dependencies():
    plan = TaskPlan(tasks=[task(), task("t2", items=[item("frontend.css")])])
    assert plan.ancestors("t2") == set()


def test_stale_vector_source_is_rejected_before_generation(repo, client):
    with pytest.raises(ValueError, match="Indexed source is stale"):
        create_task_draft(
            client(config().llm),
            query="edit",
            repo=repo,
            candidates=[
                make_result(
                    path="button.css", code="outdated", start_line=1, end_line=1
                )
            ],
            mode="yolo",
            excluded=[],
        )
    assert not client.calls


def test_combined_validation_waits_for_all_tasks(repo, client):
    value = draft(repo, [task(), task("t2", depends=["t1"])])
    check = f"{sys.executable} -c \"from pathlib import Path; assert 'navy' in Path('button.css').read_text()\""
    state = execute(
        repo,
        value,
        client,
        [edits(), edits("blue", "navy")],
        config(validation_commands=[check]),
    )
    assert state["status"] == "validated"
    assert len(client.calls) == 2
    assert len(state["validation"]) == 1


@pytest.mark.parametrize(
    "path",
    [".git/hooks/post-checkout", ".devstill/metadata.sqlite", "a//b.py", "a/./b.py"],
)
def test_task_cannot_target_metadata_or_noncanonical_aliases(repo, path):
    value = draft(repo, [task(items=[item(path, "New")])])
    with pytest.raises(ValueError, match="canonical source path"):
        validate_task_sources(value.plan, repo, set(), [])


def test_late_invalid_edit_leaves_original_files_intact(repo, client):
    response = edits()
    response["edits"].append(
        {
            "path": "button.css",
            "operation": "replace",
            "old": "not present",
            "new": "bad",
        }
    )
    state = execute(repo, draft(repo), client, [response])
    assert state["status"] == "failed"
    assert "green" in (repo / "button.css").read_text()


def test_independent_tasks_generate_concurrently_and_successor_sees_both(
    repo, client, monkeypatch
):
    from threading import Barrier

    (repo / "frontend.css").write_text("button { color: green; }\n")
    value = draft(
        repo,
        [
            task(),
            task("t2", items=[item("frontend.css")]),
            task(
                "t3",
                items=[item(label="Read"), item("frontend.css", "Read")],
                depends=["t1", "t2"],
            ),
        ],
    )
    value.source_hashes["frontend.css"] = digest_file(repo / "frontend.css")
    barrier = Barrier(2)
    calls = []

    class ConcurrentClient(FakeClient):
        def chat(self, messages, **kwargs):
            payload = json.loads(messages[1]["content"])
            task_id = payload["task"]["id"]
            calls.append(task_id)
            if task_id in {"t1", "t2"}:
                barrier.wait(timeout=3)
                return json.dumps(edits(path=payload["task"]["items"][0]["target"]))
            assert all("color: blue" in r["code"] for r in payload["source"])
            return json.dumps({"summary": "Both sides use blue", "edits": []})

    monkeypatch.setattr("devstill.task_runs.AgentClient", ConcurrentClient)
    state = execute(repo, value, client, [], config(task_concurrency=2))
    assert state["status"] == "applied_unvalidated"
    assert state["coding_attempts"] == 3
    assert set(calls[:2]) == {"t1", "t2"}
    assert calls[-1] == "t3"


def test_cancellation_after_generation_does_not_apply(repo, client, monkeypatch):
    cancelled = False
    value = draft(repo)
    save_task_draft(repo / ".devstill", value)

    class CancellingClient(FakeClient):
        def chat(self, *args, **kwargs):
            nonlocal cancelled
            cancelled = True
            return json.dumps(edits())

    monkeypatch.setattr("devstill.task_runs.AgentClient", CancellingClient)
    state = run_tasks(
        value,
        repo=repo,
        artifact_root=repo / ".devstill",
        config=config(),
        cancelled=lambda: cancelled,
    )
    assert state["status"] == "cancelled"
    assert "green" in (repo / "button.css").read_text()


def test_ignored_new_file_cannot_be_reported_as_applied(repo, client):
    (repo / ".gitignore").write_text("ignored.py\n")
    value = draft(
        repo, [task(items=[item(".gitignore", "Read"), item("ignored.py", "New")])]
    )
    value.source_hashes[".gitignore"] = digest_file(repo / ".gitignore")
    state = execute(
        repo, value, client, [edits("", "value = 1\n", "ignored.py", "create")]
    )
    assert state["status"] == "failed"
    assert "not captured in the patch" in state["tasks"]["t1"]["error"]
    assert not (repo / "ignored.py").exists()


@pytest.mark.parametrize("enabled", ["agent", "executor"])
def test_saved_task_respects_disabled_execution_settings(repo, client, enabled):
    cfg = config()
    if enabled == "agent":
        cfg.llm.enabled = False
    else:
        cfg.executor.enabled = False
    with pytest.raises(ValueError, match="disabled"):
        execute(repo, draft(repo), client, [edits()], cfg)
    assert not client.calls
    assert "green" in (repo / "button.css").read_text()


def test_index_fingerprint_accepts_normalized_source_but_rejects_changed_file(
    repo, client
):
    source = repo / "button.css"
    source.write_text("button { color: green; } /* parser omits comment */\n")
    result = make_result(
        path="button.css", code="button { color: green; }", start_line=1, end_line=1
    )
    result.source_hash = digest_file(source)
    client.outputs = [compact(draft(repo).plan)]
    kwargs = {
        "query": "Make blue",
        "repo": repo,
        "candidates": [result],
        "mode": "review",
        "excluded": [],
    }
    value = create_task_draft(client(config().llm), **kwargs)
    assert "parser omits comment" in value.evidence[0]["code"]
    source.write_text("button { color: red; }\n")
    with pytest.raises(ValueError, match="Indexed source is stale"):
        create_task_draft(client(config().llm), **kwargs)
    assert len(client.calls) == 1


def test_compact_schema_constrains_existing_files():
    from devstill.tasks import compact_schema

    impact = compact_schema(["button.css"])["$defs"]["CompactTask"]["properties"][
        "items"
    ]["items"]
    assert impact["properties"]["target"]["enum"] == ["button.css"]
    assert "New" not in impact["properties"]["label"]["enum"]
    assert impact["required"] == ["label", "target"]


def test_large_task_source_preserves_separated_anchors(repo):
    from devstill.task_execution import source_view

    text = "".join(
        f"# filler line {i:04} with enough text to occupy space\n" for i in range(500)
    )
    text = text.replace("# filler line 0020", "def first_anchor(): # filler line 0020")
    text = text.replace("# filler line 0400", "def second_anchor(): # filler line 0400")
    value = draft(repo)
    value.evidence = [
        {"path": "button.css", "code": "def first_anchor():"},
        {"path": "button.css", "code": "def second_anchor():"},
    ]
    view = source_view("button.css", text, value.plan.tasks[0], value)
    combined = "\n".join(s["code"] for s in view["spans"])
    assert "first_anchor" in combined and "second_anchor" in combined
    assert len(combined) <= 6002


def test_compiler_coalesces_unordered_shared_writes_without_widening_scope():
    from devstill.tasks import coalesce_tasks

    a = task(items=[item(), item(label="Read")])
    b = task("t2", items=[item()])
    result = coalesce_tasks([a, b])
    assert len(result) == 1
    assert [(i.target, i.label) for i in result[0].items] == [
        ("button.css", PlanLabel.MODIFY)
    ]
    assert result[0].depends_on == []
    ordered = coalesce_tasks([task(), task("t2", depends=["t1"])])
    assert len(ordered) == 2 and ordered[1].depends_on == ["t1"]


def test_compact_consumer_gets_reviewed_predecessor_source():
    from devstill.tasks import CompactPlan

    data = compact(
        TaskPlan(tasks=[task(), task("t2", items=[item("route.py")], depends=["t1"])])
    )
    plan = CompactPlan.model_validate(data).expand()
    assert any(
        i.target == "button.css" and i.label == PlanLabel.READ
        for i in plan.tasks[1].items
    )


def test_compiler_rejects_conflicting_lifecycle_and_cycles():
    from devstill.tasks import coalesce_tasks

    with pytest.raises(ValueError, match="lifecycle"):
        coalesce_tasks([task(items=[item(label="Delete"), item(label="Modify")])])
    with pytest.raises(Exception, match="cycle"):
        coalesce_tasks([task(depends=["t2"]), task("t2", depends=["t1"])])


def test_coding_schema_only_permits_approved_writable_files():
    from devstill.task_execution import edit_schema

    schema = edit_schema(
        {
            "source.py": PlanLabel.MODIFY,
            "reference.py": PlanLabel.READ,
            "new.py": PlanLabel.NEW,
        }
    )
    assert schema["$defs"]["TaskEdit"]["properties"]["path"]["enum"] == [
        "source.py",
        "new.py",
    ]
    assert (
        edit_schema({"source.py": PlanLabel.READ})["properties"]["edits"]["maxItems"]
        == 0
    )
