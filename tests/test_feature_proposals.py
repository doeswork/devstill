import pytest

from devstill.feature_proposals import compile_proposal
from tests.test_tasks import config


def brief(name, files, reads=(), after=(), delete=()):
    return {
        "name": name,
        "outcome": name,
        "acceptance": [name],
        "files": files,
        "reads": list(reads),
        "after": list(after),
        "delete": list(delete),
    }


def test_compiler_resolves_names_file_operations_and_latest_producer(tmp_path):
    (tmp_path / "route.py").write_text("value = None\n")
    plan, records = compile_proposal(
        {
            "tasks": [
                brief("consume", ["route.py"], ["service.py"], ["extend"]),
                brief("create", ["service.py", "service.py"]),
                brief("extend", ["service.py"], after=["create"]),
            ]
        },
        repo=tmp_path,
        evidence=[],
        config=config(),
    )
    assert [t.outcome for t in plan.tasks] == ["create", "extend", "consume"]
    assert [t.writes[0].operation for t in plan.tasks] == ["create", "update", "update"]
    assert plan.tasks[2].inputs[0].producer == 2
    assert plan.ancestors(3) == {1, 2}
    assert records[0]["path"] == "route.py"


def test_compiler_infers_shared_write_and_read_order_without_serializing_independent_work(
    tmp_path,
):
    (tmp_path / "shared.py").write_text("before\n")
    plan, _ = compile_proposal(
        {
            "tasks": [
                brief("read", ["a.py"], ["shared.py"]),
                brief("write", ["shared.py"]),
                brief("independent", ["b.py"]),
            ]
        },
        repo=tmp_path,
        evidence=[],
        config=config(),
    )
    tasks = {t.outcome: t for t in plan.tasks}
    assert tasks["write"].depends_on == [1]
    assert tasks["independent"].depends_on == []


@pytest.mark.parametrize(
    "tasks",
    [
        [brief(str(n), [f"f{n}.py"]) for n in range(4)],
        [],
        [brief("one", ["x.py"], after=["missing"])],
        [brief("one", ["x.py"], after=["two"]), brief("two", ["y.py"], after=["one"])],
        [brief("one", ["x.py"], ["missing.py"])],
        [brief("one", ["../x.py"])],
        [brief("one", [".git/config"])],
        [brief("same", ["x.py"]), brief("same", ["y.py"])],
        [brief("delete", [], delete=["missing.py"])],
    ],
)
def test_compiler_rejects_ambiguous_or_unsafe_proposals(tmp_path, tasks):
    with pytest.raises(ValueError):
        compile_proposal({"tasks": tasks}, repo=tmp_path, evidence=[], config=config())


def test_compiler_does_not_follow_symlinks_or_excluded_paths(tmp_path):
    (tmp_path / "real.py").write_text("data")
    (tmp_path / "link.py").symlink_to(tmp_path / "real.py")
    with pytest.raises(ValueError):
        compile_proposal(
            {"tasks": [brief("change", ["link.py"])]},
            repo=tmp_path,
            evidence=[],
            config=config(),
        )
    cfg = config()
    cfg.retrieval.exclude = ["real.py"]
    with pytest.raises(ValueError, match="excluded"):
        compile_proposal(
            {"tasks": [brief("change", ["real.py"])]},
            repo=tmp_path,
            evidence=[],
            config=cfg,
        )


def test_feature_context_supports_empty_module_and_excludes_unrelated_neighbors(
    tmp_path,
):
    from devstill.feature_context import task_candidates
    from devstill.feature_planning import FeatureTask
    from tests.test_context import make_result

    (tmp_path / "__init__.py").write_text("")
    task = FeatureTask(
        outcome="export API",
        requirements=[1],
        acceptance=["exported"],
        writes=[{"path": "__init__.py", "operation": "update"}],
    )
    result = task_candidates(
        tmp_path, task, [make_result(path="CHANGELOG.md", code="history")]
    )
    assert len(result) == 1 and result[0].chunk.path == "__init__.py"
    assert result[0].chunk.code == ""


def test_child_read_can_record_completed_producer_but_cannot_consume_future_writer():
    from types import SimpleNamespace

    from devstill.feature_planning import FeaturePlan
    from devstill.feature_runs import check_child_scope
    from devstill.planning import PlanItem

    plan = FeaturePlan.model_validate(
        {
            "requirements": ["feature"],
            "tasks": [
                {
                    "outcome": "produce",
                    "requirements": [1],
                    "acceptance": ["ready"],
                    "writes": [{"path": "api.py", "operation": "update"}],
                },
                {
                    "outcome": "consume",
                    "requirements": [1],
                    "acceptance": ["wired"],
                    "writes": [{"path": "ui.py", "operation": "update"}],
                },
            ],
        }
    )
    child = SimpleNamespace(
        evidence=[],
        plan=SimpleNamespace(
            tasks=[
                SimpleNamespace(
                    items=[
                        PlanItem(label="Read", target="api.py", reason="API"),
                        PlanItem(label="Modify", target="ui.py", reason="UI"),
                    ]
                )
            ]
        ),
    )
    draft = SimpleNamespace(plan=plan)
    assert check_child_scope(draft, 2, child, {1}) == [1]
    with pytest.raises(ValueError, match="conflict"):
        check_child_scope(draft, 2, child, set())
