from __future__ import annotations

import asyncio
import json
from contextlib import nullcontext

import pytest
from textual.widgets import Button, Select

from devstill.cli import parser
from devstill.tasks import TaskPlan
from devstill.tui import ComposerMode, PromptTextArea
from devstill.tui_commands import (
    TuiCommand,
    TuiCommandKind,
    TuiResult,
    TuiWorkflow,
    parse_tui_command,
)
from tests.test_context import make_result
from tests.test_executor import FakePiRunner
from tests.test_planning import FakeClient
from tests.test_tasks import config, draft, make_repo
from tests.test_tui import FakeWorkflow, build_app, wait_until_idle


@pytest.fixture
def repo(tmp_path):
    return make_repo(tmp_path)


def test_commands_expose_review_and_yolo():
    assert parse_tui_command("/task change button").kind == TuiCommandKind.TASK
    assert parse_tui_command("/yolo change button").kind == TuiCommandKind.YOLO
    args = parser().parse_args(["task", "change button", "--yolo"])
    assert args.yolo and args.query == "change button"
    args = parser().parse_args(["task", "--run", "a" * 32, "--json"])
    assert args.run_id and not args.yolo


@pytest.mark.parametrize("mode", [TuiCommandKind.TASK, TuiCommandKind.YOLO])
def test_workflow_review_and_yolo_share_scope_and_execution(repo, monkeypatch, mode):
    cfg = config(context_window=12288)
    value = draft(repo)
    client = FakeClient(
        json.dumps(
            {"items": [i.model_dump(mode="json") for i in value.plan.tasks[0].items]}
        )
    )
    monkeypatch.setattr(
        "devstill.tui_commands.AgentClient", lambda *a, **kw: nullcontext(client)
    )
    runner = FakePiRunner(
        lambda workspace: (workspace / "button.css").write_text(
            "button { color: blue; }\n"
        )
    )
    monkeypatch.setattr("devstill.executor.PiRunner", lambda: runner)

    class Search:
        def query(self, *args, **kwargs):
            assert args == (value.query,)
            assert kwargs == {"limit": 12}
            return [
                make_result(
                    path="button.css",
                    code="button { color: green; }\n",
                    start_line=1,
                    end_line=1,
                )
            ]

        def close(self):
            pass

    workflow = TuiWorkflow(repo / "devstill.json", cfg)
    monkeypatch.setattr(workflow, "_open_search", lambda report: Search())
    try:
        result = workflow.execute(TuiCommand(mode, "request", value.query))
        if mode == TuiCommandKind.TASK:
            assert result.task_review is not None
            assert result.task_review.engine == "quick"
            assert "Depends on" not in result.markdown
            assert "green" in (repo / "button.css").read_text()
            assert len(client.calls) == 1
            assert runner.calls == 0
            result = workflow.execute(
                TuiCommand(TuiCommandKind.TASK_RUN, "run", result.task_review.id)
            )
        assert result.task_review is None
        assert result.task_state["status"] == "applied_unvalidated"
        assert len(client.calls) == 1
        assert client.calls[0][1]["schema_name"] == "devstill_plan"
        assert runner.calls == 1
        assert "blue" in (repo / "button.css").read_text()
    finally:
        workflow.close()


class TaskUiWorkflow(FakeWorkflow):
    def __init__(self, value):
        super().__init__()
        self.draft = value

    def execute(self, command, progress=None):
        self.calls.append(command)
        if command.kind == TuiCommandKind.TASK:
            return TuiResult(
                "Task Review",
                "# Task Review\n\n**Modify** `button.css`",
                task_review=self.draft,
            )
        return TuiResult(
            "Task result",
            "# Task applied",
            task_state={"status": "applied_unvalidated"},
        )


def test_task_review_has_one_run_action_and_no_second_patch_approval(repo):
    async def scenario():
        workflow = TaskUiWorkflow(draft(repo))
        app = build_app(repo, workflow)
        async with app.run_test(size=(120, 40)) as pilot:
            assert app.query_one("#task-mode", Select).value == "review"
            await app.submit_text("Make button blue")
            await wait_until_idle(app, pilot)
            assert app._composer_mode == ComposerMode.TASK_REVIEW
            assert app.query_one("#run-task", Button).display
            assert len(workflow.calls) == 1
            await pilot.click("#run-task")
            await wait_until_idle(app, pilot)
            assert workflow.calls[-1].kind == TuiCommandKind.TASK_RUN
            assert len(workflow.calls) == 2
            assert app._composer_mode == ComposerMode.NORMAL
            assert not app.query_one("#apply-execution", Button).display

    asyncio.run(scenario())


def test_yolo_skips_review_and_edit_request_returns_to_review_mode(repo):
    async def scenario():
        workflow = TaskUiWorkflow(draft(repo))
        app = build_app(repo, workflow)
        async with app.run_test(size=(100, 35)) as pilot:
            app.query_one("#task-mode", Select).value = "yolo"
            await pilot.pause()
            await app.submit_text("Make button blue")
            await wait_until_idle(app, pilot)
            assert workflow.calls[-1].kind == TuiCommandKind.YOLO
            assert app._composer_mode == ComposerMode.NORMAL
            assert not app.query_one("#run-task", Button).display
            await app.submit_text("/task Make button blue")
            await wait_until_idle(app, pilot)
            await pilot.click("#edit-task")
            assert app.query_one("#task-mode", Select).value == "review"
            assert (
                app.query_one("#command-input", PromptTextArea).text
                == workflow.draft.query
            )
            assert len(workflow.calls) == 2

    asyncio.run(scenario())


def test_unresolved_task_disables_run(repo):
    async def scenario():
        value = draft(repo)
        value.plan = TaskPlan(
            tasks=value.plan.tasks, blockers=["Missing integration contract"]
        )
        app = build_app(repo, TaskUiWorkflow(value))
        async with app.run_test(size=(100, 35)) as pilot:
            await app.submit_text("/task feature")
            await wait_until_idle(app, pilot)
            assert app.query_one("#run-task", Button).disabled
            await pilot.press("escape")
            assert app._composer_mode == ComposerMode.NORMAL

    asyncio.run(scenario())


def test_cli_review_json_does_not_execute(repo, monkeypatch, capsys):
    import json

    from devstill import cli

    value = draft(repo)

    class Workflow:
        def __init__(self, *args):
            pass

        def execute(self, command, report):
            assert command.kind == TuiCommandKind.TASK
            return TuiResult("Task Review", "review", task_review=value)

        def run_task(self, *args):
            raise AssertionError("Review-only JSON must not execute")

        def close(self):
            pass

    monkeypatch.setattr(cli, "TuiWorkflow", Workflow)
    monkeypatch.setattr(cli, "_configured", lambda: (repo / "devstill.json", config()))
    assert cli.task_command(parser().parse_args(["task", "button", "--json"])) == 0
    assert json.loads(capsys.readouterr().out)["id"] == value.id


def test_cli_yolo_json_reports_failures_without_confirmation(repo, monkeypatch, capsys):
    import json

    from devstill import cli

    class Workflow:
        def __init__(self, *args):
            pass

        def execute(self, command, report):
            assert command.kind == TuiCommandKind.YOLO
            return TuiResult("Task result", "failed", task_state={"status": "failed"})

        def close(self):
            pass

    monkeypatch.setattr(cli, "TuiWorkflow", Workflow)
    monkeypatch.setattr(cli, "_configured", lambda: (repo / "devstill.json", config()))
    assert (
        cli.task_command(parser().parse_args(["task", "button", "--yolo", "--json"]))
        == 1
    )
    assert json.loads(capsys.readouterr().out)["status"] == "failed"
