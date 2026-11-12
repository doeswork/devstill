from __future__ import annotations

import asyncio

from textual.widgets import Button, Select

from devstill import cli
from devstill.cli import parser
from devstill.tui import PromptTextArea
from devstill.tui_commands import TuiCommandKind, TuiResult, parse_tui_command
from tests.test_features import feature
from tests.test_tasks import make_repo
from tests.test_tui import FakeWorkflow, build_app, wait_until_idle


def setup_repo(tmp_path):
    make_repo(tmp_path)
    for name in ("seed.py", "endpoint.py", "ui.txt"):
        (tmp_path / name).write_text("initial\n")
    return tmp_path


class FeatureWorkflow(FakeWorkflow):
    def __init__(self, draft):
        super().__init__()
        self.draft = draft

    def execute(self, command, progress=None):
        self.calls.append(command)
        if command.kind == TuiCommandKind.FEATURE:
            return TuiResult(
                "Feature Review",
                "# Feature\n\nThree Quick tasks",
                task_review=self.draft,
            )
        return TuiResult(
            "Feature result",
            "Completed",
            task_state={"engine": "composed_quick", "status": "applied_unvalidated"},
        )


def test_feature_selector_review_run_and_explicit_quick_override(tmp_path):
    repo = setup_repo(tmp_path)

    async def scenario():
        workflow = FeatureWorkflow(feature(repo))
        app = build_app(repo, workflow)
        async with app.run_test(size=(100, 40)) as pilot:
            assert app.query_one("#request-type", Select).value == "quick"
            app.query_one("#request-type", Select).value = "feature"
            await pilot.pause()
            await app.submit_text("Build feature")
            await wait_until_idle(app, pilot)
            assert workflow.calls[-1].kind == TuiCommandKind.FEATURE
            assert str(app.query_one("#run-task", Button).label) == "Run feature"
            await pilot.click("#run-task")
            await wait_until_idle(app, pilot)
            assert workflow.calls[-1].kind == TuiCommandKind.FEATURE_RUN
            assert len(workflow.calls) == 2
            await app.submit_text("/yolo small edit")
            await wait_until_idle(app, pilot)
            assert workflow.calls[-1].kind == TuiCommandKind.YOLO

    asyncio.run(scenario())


def test_feature_edit_stays_feature_and_search_bypasses_feature(tmp_path):
    repo = setup_repo(tmp_path)

    async def scenario():
        workflow = FeatureWorkflow(feature(repo))
        app = build_app(repo, workflow)
        async with app.run_test(size=(80, 35)) as pilot:
            await app.submit_text("/feature Build feature")
            await wait_until_idle(app, pilot)
            await pilot.click("#edit-task")
            await pilot.pause()
            assert app.query_one("#request-type", Select).value == "feature"
            assert app.query_one("#task-mode", Select).value == "review"
            assert (
                app.query_one("#command-input", PromptTextArea).text
                == workflow.draft.query
            )
            app.query_one("#task-mode", Select).value = "yolo"
            await pilot.pause()
            await app.submit_text("Build feature")
            await wait_until_idle(app, pilot)
            assert workflow.calls[-1].kind == TuiCommandKind.FEATURE_YOLO
            app.query_one("#task-mode", Select).value = "search"
            await pilot.pause()
            assert app.query_one("#request-type", Select).disabled
            await app.submit_text("lookup")
            await wait_until_idle(app, pilot)
            assert workflow.calls[-1].kind == TuiCommandKind.SEARCH

    asyncio.run(scenario())


def test_cli_feature_json_saves_review_without_running(tmp_path, monkeypatch, capsys):
    from tests.test_tasks import config

    repo = setup_repo(tmp_path)
    value = feature(repo)

    class Workflow:
        def __init__(self, *args):
            pass

        def execute(self, command, report):
            assert command.kind == TuiCommandKind.FEATURE
            return TuiResult("Feature", "review", task_review=value)

        def close(self):
            pass

    monkeypatch.setattr(cli, "TuiWorkflow", Workflow)
    monkeypatch.setattr(cli, "_configured", lambda: (repo / "devstill.json", config()))
    assert (
        cli.task_command(parser().parse_args(["feature", "Build feature", "--json"]))
        == 0
    )
    assert value.id in capsys.readouterr().out
    assert (
        parse_tui_command("/feature-yolo build it").kind == TuiCommandKind.FEATURE_YOLO
    )


def test_stop_feature_prevents_further_work_and_next_request_resets_cancel(tmp_path):
    import threading

    repo = setup_repo(tmp_path)

    class Workflow(FakeWorkflow):
        def __init__(self):
            super().__init__()
            self.cancelled = threading.Event()
            self.resets = 0

        def reset_task_cancellation(self):
            self.cancelled.clear()
            self.resets += 1

        def cancel_tasks(self):
            self.cancelled.set()

        def execute(self, command, progress=None):
            self.calls.append(command)
            if command.kind == TuiCommandKind.FEATURE_YOLO:
                assert self.cancelled.wait(3), (
                    "Stop feature did not signal cancellation"
                )
            else:
                assert not self.cancelled.is_set()
            return TuiResult(
                "Result",
                "Stopped",
                task_state={"status": "cancelled", "engine": "composed_quick"},
            )

    async def scenario():
        workflow = Workflow()
        app = build_app(repo, workflow)
        async with app.run_test(size=(100, 40)) as pilot:
            await app.submit_text("/feature-yolo build")
            await pilot.pause()
            assert app.query_one("#stop-feature", Button).display
            await pilot.click("#stop-feature")
            await wait_until_idle(app, pilot)
            await app.submit_text("/yolo edit")
            await wait_until_idle(app, pilot)
            assert workflow.resets == 2

    asyncio.run(scenario())


def test_real_workflow_saves_feature_review_then_runs_quick_children(
    tmp_path, monkeypatch
):
    import json
    from contextlib import nullcontext

    from devstill.tui_commands import TuiCommand, TuiWorkflow
    from tests.test_features import controlled, proposal_data, results
    from tests.test_planning import FakeClient
    from tests.test_tasks import config

    repo = setup_repo(tmp_path)
    cfg = config(context_window=12288)
    client = FakeClient(
        json.dumps(
            {
                "items": [
                    {"label": "Read", "target": "seed.py", "reason": "Convention"},
                    {"label": "Read", "target": "ui.txt", "reason": "UI"},
                    {"label": "Read", "target": "endpoint.py", "reason": "Endpoint"},
                    {
                        "label": "Search",
                        "target": "service implementation",
                        "reason": "Needs service context",
                    },
                ]
            }
        ),
        json.dumps(proposal_data()),
    )
    client.config = cfg.llm
    monkeypatch.setattr(
        "devstill.tui_commands.FeatureClient", lambda *a, **kw: nullcontext(client)
    )
    retrieve, runner, children, _ = controlled(monkeypatch, repo)

    class Search:
        def query(self, query, limit):
            if query == "Build feature":
                return results(repo, ["seed.py", "endpoint.py", "ui.txt"])
            return retrieve(query)

        def close(self):
            pass

    workflow = TuiWorkflow(repo / "devstill.json", cfg)
    monkeypatch.setattr(workflow, "_open_search", lambda report: Search())
    try:
        result = workflow.execute(
            TuiCommand(
                TuiCommandKind.FEATURE, "/feature Build feature", "Build feature"
            )
        )
        assert result.task_review is not None and runner.calls == 0
        assert len(client.calls) == 2
        result = workflow.execute(
            TuiCommand(TuiCommandKind.FEATURE_RUN, "run", result.task_review.id)
        )
        assert result.task_state["status"] == "applied_unvalidated", result.task_state
        assert runner.calls == 3 and len(children) == 3
        assert "Read `service.py`" in result.markdown
    finally:
        workflow.close()
