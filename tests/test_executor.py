from __future__ import annotations

import json
from pathlib import Path

import pytest

from devstill.config import DevstillConfig
from devstill.executor import (
    ExecutionError,
    RepositoryExecutor,
    select_plan_artifact,
    validate_reviewed_plan,
)
from devstill.pi_runtime import PiRunResult
from devstill.planning import PlanItem, PlanLabel, PlanOutput, render_plan_json


class FakePiRunner:
    def __init__(self, mutate, *, stop_reason=None):
        self.mutate = mutate
        self.stop_reason = stop_reason
        self.calls = 0
        self.prompts: list[str] = []
        self.provider_max_tokens: list[int] = []

    def run(self, **kwargs):
        self.calls += 1
        self.prompts.append(kwargs["prompt"])
        provider = json.loads(
            (kwargs["config_dir"] / "models.json").read_text(encoding="utf-8")
        )
        self.provider_max_tokens.append(
            provider["providers"]["devstill"]["models"][0]["maxTokens"]
        )
        self.mutate(kwargs["workspace"])
        kwargs["log_path"].parent.mkdir(parents=True, exist_ok=True)
        kwargs["log_path"].write_text('{"type":"agent_end"}\n', encoding="utf-8")
        return PiRunResult(
            events=[{"type": "agent_end"}], tool_calls=3, elapsed_seconds=0.2,
            stop_reason=self.stop_reason,
        )


def configured() -> DevstillConfig:
    return DevstillConfig(
        llm={
            "enabled": True,
            "base_url": "http://localhost:1234/v1",
            "model": "local-model",
        },
        executor={"context_window": 12288},
    )


def write_plan(
    root: Path,
    artifact_root: Path,
    items: list[PlanItem],
    *,
    deep: bool = False,
) -> Path:
    plan = PlanOutput(
        query="implement the requested change",
        repository=str(root),
        model="local-model",
        items=items,
    )
    path = artifact_root / (".deep-plan.json" if deep else ".plan.json")
    artifact_root.mkdir(parents=True, exist_ok=True)
    path.write_text(render_plan_json(plan), encoding="utf-8")
    return path


def test_prepare_and_apply_only_approved_targets(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "modify.py").write_text("value = 1\n", encoding="utf-8")
    (repo / "read.py").write_text("REFERENCE = True\n", encoding="utf-8")
    artifacts = repo / ".devstill"
    write_plan(
        repo,
        artifacts,
        [
            PlanItem(label=PlanLabel.MODIFY, target="modify.py", reason="Update it."),
            PlanItem(label=PlanLabel.READ, target="read.py", reason="Inspect it."),
            PlanItem(label=PlanLabel.NEW, target="new.py", reason="Create it."),
        ],
    )

    def mutate(workspace: Path) -> None:
        (workspace / "modify.py").write_text("value = 2\n", encoding="utf-8")
        (workspace / "new.py").write_text("created = True\n", encoding="utf-8")

    runner = FakePiRunner(mutate)
    executor = RepositoryExecutor(
        repo,
        artifacts,
        configured(),
        runner=runner,
    )
    prepared = executor.prepare(source="plan")
    try:
        assert prepared.result.status == "preview"
        assert prepared.result.changed_files == ["modify.py", "new.py"]
        assert runner.provider_max_tokens == [4096]
        assert "diff --git a/modify.py b/modify.py" in prepared.patch
        assert "diff --git a/new.py b/new.py" in prepared.patch
        assert (repo / "modify.py").read_text(encoding="utf-8") == "value = 1\n"
        assert not (repo / "new.py").exists()
        result = executor.apply(prepared)
        assert result.status == "applied"
    finally:
        prepared.close()
    assert (repo / "modify.py").read_text(encoding="utf-8") == "value = 2\n"
    assert (repo / "read.py").read_text(encoding="utf-8") == "REFERENCE = True\n"
    assert (repo / "new.py").read_text(encoding="utf-8") == "created = True\n"
    assert json.loads((artifacts / ".execute.json").read_text())["status"] == "applied"


def test_execution_instruction_overrides_query_without_expanding_plan(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text("value = 1\n", encoding="utf-8")
    artifacts = repo / ".devstill"
    write_plan(
        repo,
        artifacts,
        [PlanItem(label=PlanLabel.MODIFY, target="app.py", reason="Update it.")],
    )

    def mutate(workspace: Path) -> None:
        (workspace / "app.py").write_text("value = 2\n", encoding="utf-8")

    runner = FakePiRunner(mutate)
    prepared = RepositoryExecutor(
        repo,
        artifacts,
        configured(),
        runner=runner,
    ).prepare(
        source="plan",
        instruction="Use the existing helper and keep the change focused.",
    )
    try:
        assert prepared.result.query == "implement the requested change"
        assert (
            prepared.result.instruction
            == "Use the existing helper and keep the change focused."
        )
        assert (
            'ORIGINAL_PLAN_QUERY: "implement the requested change"' in runner.prompts[0]
        )
        assert (
            'EXECUTION_REQUEST: "Use the existing helper and keep the change focused."'
            in runner.prompts[0]
        )
        assert "Use the scoped tools" in runner.prompts[0]
        assert '"target": "app.py"' in runner.prompts[0]
        result_artifact = json.loads((artifacts / ".execute.json").read_text())
        assert result_artifact["instruction"] == prepared.result.instruction
    finally:
        prepared.close()


def test_blank_execution_instruction_uses_original_plan_query(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text("value = 1\n", encoding="utf-8")
    artifacts = repo / ".devstill"
    write_plan(
        repo,
        artifacts,
        [PlanItem(label=PlanLabel.MODIFY, target="app.py", reason="Update it.")],
    )
    runner = FakePiRunner(
        lambda workspace: (workspace / "app.py").write_text(
            "value = 2\n", encoding="utf-8"
        )
    )
    prepared = RepositoryExecutor(
        repo,
        artifacts,
        configured(),
        runner=runner,
    ).prepare(instruction="  \n")
    try:
        assert prepared.result.instruction == "implement the requested change"
        assert (
            'EXECUTION_REQUEST: "implement the requested change"' in runner.prompts[0]
        )
    finally:
        prepared.close()


def test_execution_passes_refinement_history_and_blocks_excluded_target(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text("value = 1\n", encoding="utf-8")
    artifacts = repo / ".devstill"
    plan = PlanOutput(
        query="change app",
        repository=str(repo),
        model="local-model",
        items=[PlanItem(label=PlanLabel.MODIFY, target="app.py", reason="Update it.")],
        refinements=["Keep compatibility."],
    )
    artifacts.mkdir()
    (artifacts / ".plan.json").write_text(render_plan_json(plan), encoding="utf-8")
    runner = FakePiRunner(
        lambda workspace: (workspace / "app.py").write_text("value = 2\n")
    )
    prepared = RepositoryExecutor(repo, artifacts, configured(), runner=runner).prepare()
    try:
        assert 'CUMULATIVE_PLAN_REQUEST: "change app\\n\\nKeep compatibility."' in (
            runner.prompts[0]
        )
    finally:
        prepared.close()

    excluded = DevstillConfig.model_validate(
        {
            **configured().model_dump(mode="json"),
            "retrieval": {"exclude": ["app.py"]},
        }
    )
    with pytest.raises(ExecutionError, match="permanently excluded"):
        RepositoryExecutor(repo, artifacts, excluded, runner=runner).prepare()


def test_search_entries_block_execution(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    artifacts = repo / ".devstill"
    write_plan(
        repo,
        artifacts,
        [PlanItem(label=PlanLabel.SEARCH, target="auth", reason="Find it.")],
    )
    runner = FakePiRunner(lambda _workspace: None)
    executor = RepositoryExecutor(repo, artifacts, configured(), runner=runner)
    with pytest.raises(ExecutionError, match="Search entries"):
        executor.prepare()
    assert runner.calls == 0


def test_directory_intent_new_target_blocks_execution_before_pi(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    artifacts = repo / ".devstill"
    write_plan(
        repo,
        artifacts,
        [
            PlanItem(
                label=PlanLabel.NEW,
                target="static/zombie_textures",
                reason="Create a directory for zombie texture assets.",
            )
        ],
    )
    runner = FakePiRunner(lambda _workspace: None)
    with pytest.raises(ExecutionError, match="exact file, not a directory"):
        RepositoryExecutor(repo, artifacts, configured(), runner=runner).prepare()
    assert runner.calls == 0


@pytest.mark.parametrize("stop_reason", [None, "tool_limit", "verification_stalled"])
def test_partial_execution_gets_one_focused_repair(tmp_path: Path, stop_reason) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text("value = 1\n", encoding="utf-8")
    artifacts = repo / ".devstill"
    write_plan(
        repo,
        artifacts,
        [
            PlanItem(label=PlanLabel.MODIFY, target="app.py", reason="Update it."),
            PlanItem(label=PlanLabel.NEW, target="new.py", reason="Create it."),
        ],
    )
    runner: FakePiRunner

    def mutate(workspace: Path) -> None:
        if runner.calls == 1:
            (workspace / "app.py").write_text("value = 2\n", encoding="utf-8")
        else:
            (workspace / "new.py").write_text("created = True\n", encoding="utf-8")

    runner = FakePiRunner(mutate, stop_reason=stop_reason)
    prepared = RepositoryExecutor(repo, artifacts, configured(), runner=runner).prepare()
    try:
        assert runner.calls == 2
        assert "Repair only the unmet contracts" in runner.prompts[1]
        assert 'UNMET_TARGETS: ["new.py"]' in runner.prompts[1]
        assert prepared.result.tool_calls == 6
    finally:
        prepared.close()


@pytest.mark.parametrize("stop_reason", [None, "tool_limit", "verification_stalled"])
def test_zero_progress_does_not_retry(tmp_path: Path, stop_reason) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text("value = 1\n", encoding="utf-8")
    artifacts = repo / ".devstill"
    write_plan(
        repo,
        artifacts,
        [PlanItem(label=PlanLabel.MODIFY, target="app.py", reason="Update it.")],
    )
    runner = FakePiRunner(lambda _workspace: None, stop_reason=stop_reason)
    with pytest.raises(ExecutionError, match="did not change planned Modify"):
        RepositoryExecutor(repo, artifacts, configured(), runner=runner).prepare()
    assert runner.calls == 1


def test_reviewed_plan_allows_search_but_validates_concrete_targets(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text("value = 1\n", encoding="utf-8")
    plan = PlanOutput(
        query="update app",
        repository=str(repo),
        model="local-model",
        items=[
            PlanItem(label=PlanLabel.SEARCH, target="**/auth*", reason="Find auth."),
            PlanItem(label=PlanLabel.READ, target="app.py", reason="Inspect app."),
        ],
    )

    validate_reviewed_plan(plan, repo)

    invalid = plan.model_copy(
        update={
            "items": [
                plan.items[0],
                PlanItem(
                    label=PlanLabel.READ,
                    target="missing.py",
                    reason="Inspect the missing file.",
                ),
            ]
        }
    )
    with pytest.raises(ExecutionError, match="missing or not a file"):
        validate_reviewed_plan(invalid, repo)


@pytest.mark.parametrize("stop_reason", [None, "tool_limit", "plan_complete"])
def test_read_file_mutation_and_unapproved_file_are_rejected(tmp_path: Path, stop_reason) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "read.py").write_text("value = 1\n", encoding="utf-8")
    artifacts = repo / ".devstill"
    write_plan(
        repo,
        artifacts,
        [PlanItem(label=PlanLabel.READ, target="read.py", reason="Inspect it.")],
    )

    def mutate_read(workspace: Path) -> None:
        (workspace / "read.py").write_text("value = 2\n", encoding="utf-8")

    with pytest.raises(ExecutionError, match="read-only"):
        RepositoryExecutor(
            repo,
            artifacts,
            configured(),
            runner=FakePiRunner(mutate_read, stop_reason=stop_reason),
        ).prepare()

    write_plan(
        repo,
        artifacts,
        [PlanItem(label=PlanLabel.MODIFY, target="read.py", reason="Update it.")],
    )

    def add_unapproved(workspace: Path) -> None:
        (workspace / "surprise.py").write_text("bad = True\n", encoding="utf-8")

    with pytest.raises(ExecutionError, match="unapproved"):
        RepositoryExecutor(
            repo,
            artifacts,
            configured(),
            runner=FakePiRunner(add_unapproved, stop_reason=stop_reason),
        ).prepare()


def test_completed_budget_limited_run_produces_preview_without_auto_applying(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    target = repo / "app.py"
    target.write_text("value = 1\n")
    artifacts = repo / ".devstill"
    write_plan(repo, artifacts, [
        PlanItem(label=PlanLabel.MODIFY, target="app.py", reason="Update it."),
    ])
    runner = FakePiRunner(
        lambda workspace: (workspace / "app.py").write_text("value = 2\n"),
        stop_reason="tool_limit",
    )
    prepared = RepositoryExecutor(repo, artifacts, configured(), runner=runner).prepare()
    try:
        assert prepared.result.status == "preview"
        assert "+value = 2" in prepared.patch
        assert target.read_text() == "value = 1\n"
        assert runner.calls == 1
    finally:
        prepared.close()


def test_apply_rejects_source_changed_after_preview(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    target = repo / "app.py"
    target.write_text("value = 1\n", encoding="utf-8")
    artifacts = repo / ".devstill"
    write_plan(
        repo,
        artifacts,
        [PlanItem(label=PlanLabel.MODIFY, target="app.py", reason="Update it.")],
    )

    def mutate(workspace: Path) -> None:
        (workspace / "app.py").write_text("value = 2\n", encoding="utf-8")

    executor = RepositoryExecutor(
        repo,
        artifacts,
        configured(),
        runner=FakePiRunner(mutate),
    )
    prepared = executor.prepare()
    try:
        target.write_text("user change\n", encoding="utf-8")
        with pytest.raises(ExecutionError, match="Source changed"):
            executor.apply(prepared)
    finally:
        prepared.close()
    assert target.read_text(encoding="utf-8") == "user change\n"


def test_delete_must_remove_file_but_remove_and_modify_must_preserve_it(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    target = repo / "obsolete.py"
    target.write_text("obsolete = True\n", encoding="utf-8")
    artifacts = repo / ".devstill"

    def remove(workspace: Path) -> None:
        (workspace / "obsolete.py").unlink()

    for label in (PlanLabel.MODIFY, PlanLabel.REMOVE):
        write_plan(
            repo,
            artifacts,
            [PlanItem(label=label, target="obsolete.py", reason="Change it.")],
        )
        with pytest.raises(ExecutionError, match="did not label Delete"):
            RepositoryExecutor(
                repo,
                artifacts,
                configured(),
                runner=FakePiRunner(remove),
            ).prepare()

    def retain(workspace: Path) -> None:
        (workspace / "obsolete.py").write_text("obsolete = False\n", encoding="utf-8")

    write_plan(
        repo,
        artifacts,
        [PlanItem(label=PlanLabel.DELETE, target="obsolete.py", reason="Delete it.")],
    )
    with pytest.raises(ExecutionError, match="did not delete planned Delete"):
        RepositoryExecutor(
            repo,
            artifacts,
            configured(),
            runner=FakePiRunner(retain),
        ).prepare()

    write_plan(
        repo,
        artifacts,
        [PlanItem(label=PlanLabel.DELETE, target="obsolete.py", reason="Delete it.")],
    )
    executor = RepositoryExecutor(
        repo,
        artifacts,
        configured(),
        runner=FakePiRunner(remove),
    )
    prepared = executor.prepare()
    try:
        result = executor.apply(prepared)
    finally:
        prepared.close()
    assert result.status == "applied"
    assert not target.exists()


def test_validation_failure_preserves_applied_change(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    target = repo / "app.py"
    target.write_text("value = 1\n", encoding="utf-8")
    artifacts = repo / ".devstill"
    write_plan(
        repo,
        artifacts,
        [PlanItem(label=PlanLabel.MODIFY, target="app.py", reason="Update it.")],
    )
    config = configured()
    config.executor.validation_commands = [
        "python3 -c 'import sys; print(\"bad\"); sys.exit(4)'"
    ]

    def mutate(workspace: Path) -> None:
        (workspace / "app.py").write_text("value = 2\n", encoding="utf-8")

    executor = RepositoryExecutor(
        repo,
        artifacts,
        config,
        runner=FakePiRunner(mutate),
    )
    prepared = executor.prepare()
    try:
        result = executor.apply(prepared)
    finally:
        prepared.close()
    assert result.status == "validation_failed"
    assert result.validation[0].returncode == 4
    assert "bad" in result.validation[0].output
    assert target.read_text(encoding="utf-8") == "value = 2\n"


def test_newest_plan_selection_and_explicit_source(tmp_path: Path) -> None:
    artifacts = tmp_path / ".devstill"
    artifacts.mkdir()
    ordinary = artifacts / ".plan.json"
    deep = artifacts / ".deep-plan.json"
    ordinary.write_text("{}", encoding="utf-8")
    deep.write_text("{}", encoding="utf-8")
    ordinary.touch()
    assert select_plan_artifact(artifacts)[0] == "plan"
    assert select_plan_artifact(artifacts, "deep-plan") == ("deep-plan", deep)
