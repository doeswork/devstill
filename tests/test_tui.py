from __future__ import annotations

import asyncio
import threading
from pathlib import Path

from textual.widgets import (
    Button,
    Collapsible,
    Input,
    OptionList,
    ProgressBar,
    Select,
    Static,
)

from devstill.config import DevstillConfig, load_config
from devstill.credentials import saved_api_key, store_api_key
from devstill.executor import validate_reviewed_plan
from devstill.models import IndexFileProgress, IndexProgress
from devstill.planning import PlanItem, PlanLabel, PlanOutput
from devstill.tui import (
    ComposerMode,
    DevstillApp,
    ExecutionSessionEntry,
    PlanImpactRow,
    PlanSessionEntry,
    PromptTextArea,
    SessionEntry,
)
from devstill.tui_agent import AgentSetupScreen
from devstill.tui_commands import (
    TuiCommandKind,
    TuiExecutionOffer,
    TuiPreview,
    TuiResult,
)


class FakeWorkflow:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.calls = []
        self.error = error
        self.runtime_state = "cold"
        self.closed = False

    def execute(self, command, progress=None):
        self.calls.append(command)
        if progress:
            progress(f"Working on {command.kind.value}")
        if self.error:
            raise self.error
        return TuiResult(
            command.kind.value,
            f"# {command.kind.value.title()} result\n\nCompleted `{command.raw}`.",
        )

    def close(self):
        self.closed = True


def build_app(tmp_path: Path, workflow=None) -> DevstillApp:
    config_path = tmp_path / "devstill.json"
    config_path.write_text("{}", encoding="utf-8")
    return DevstillApp(
        config_path,
        DevstillConfig(),
        workflow=workflow or FakeWorkflow(),
    )


async def wait_until_idle(app: DevstillApp, pilot) -> None:
    for _ in range(100):
        if not app._busy:
            return
        await pilot.pause(0.02)
    raise AssertionError("TUI command did not finish")


def test_tui_layout_autofocus_and_responsive_sidebar(tmp_path: Path) -> None:
    async def scenario() -> None:
        app = build_app(tmp_path)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            assert app.query_one("#command-input", PromptTextArea).has_focus
            assert app.query_one("#session-feed")
            assert app.query_one("#sidebar").display
            assert app.query_one("#workspace-status").content
            assert "cold" in str(app.query_one("#sidebar-info").content)

            await pilot.resize_terminal(80, 30)
            await pilot.pause()
            assert not app.query_one("#sidebar").display
            await pilot.press("ctrl+b")
            await pilot.pause()
            assert app.query_one("#sidebar").display

    asyncio.run(scenario())


def test_tui_warms_retrieval_runtime_on_startup(tmp_path: Path) -> None:
    class StartupWarmWorkflow(FakeWorkflow):
        def __init__(self) -> None:
            super().__init__()
            self.warm_calls = 0

        def warm(self, progress=None):
            self.warm_calls += 1
            self.runtime_state = "loading"
            if progress:
                progress("Loading startup encoder")
            self.runtime_state = "warm"

    async def scenario() -> None:
        workflow = StartupWarmWorkflow()
        app = build_app(tmp_path, workflow)
        async with app.run_test(size=(120, 40)) as pilot:
            await wait_until_idle(app, pilot)
            assert workflow.warm_calls == 1
            assert workflow.runtime_state == "warm"
            assert (
                "encoder warm"
                in str(app.query_one("#activity", Static).content).lower()
            )
            assert app.query_one("#command-input", PromptTextArea).has_focus
            assert not app.query_one("#command-input", PromptTextArea).disabled

    asyncio.run(scenario())


def test_ctrl_c_exits_even_when_prompt_is_focused(tmp_path: Path) -> None:
    async def scenario() -> None:
        workflow = FakeWorkflow()
        app = build_app(tmp_path, workflow)
        async with app.run_test(size=(120, 40)) as pilot:
            assert app.query_one("#command-input", PromptTextArea).has_focus
            assert app.is_running
            await pilot.press("ctrl+c")
            await pilot.pause()
            assert not app.is_running
        assert workflow.closed

    asyncio.run(scenario())


def test_ctrl_c_copies_prompt_selection_without_exiting(tmp_path: Path) -> None:
    async def scenario() -> None:
        workflow = FakeWorkflow()
        app = build_app(tmp_path, workflow)
        async with app.run_test(size=(120, 40)) as pilot:
            prompt = app.query_one("#command-input", PromptTextArea)
            prompt.text = "copy this"
            prompt.action_select_all()

            await pilot.press("ctrl+c")
            await pilot.pause()

            assert app.is_running
            assert app.clipboard == "copy this"
            assert "copied" in str(app.query_one("#activity", Static).content).lower()

    asyncio.run(scenario())


def test_plain_text_runs_local_search_and_history(tmp_path: Path) -> None:
    async def scenario() -> None:
        workflow = FakeWorkflow()
        app = build_app(tmp_path, workflow)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.press(*list("login"), "enter")
            await wait_until_idle(app, pilot)
            assert len(workflow.calls) == 1
            assert workflow.calls[0].kind is TuiCommandKind.SEARCH
            assert workflow.calls[0].query == "login"
            assert len(app.query(SessionEntry)) == 1
            assert app.query_one("#command-input", PromptTextArea).has_focus

            await pilot.press(*list("register"), "enter")
            await wait_until_idle(app, pilot)
            await pilot.press("up")
            assert app.query_one("#command-input", PromptTextArea).text == "register"
            await pilot.press("up")
            assert app.query_one("#command-input", PromptTextArea).text == "login"
            await pilot.press("down")
            assert app.query_one("#command-input", PromptTextArea).text == "register"

    asyncio.run(scenario())


def test_execute_preview_uses_inline_approval_before_apply(tmp_path: Path) -> None:
    class FakeExecutionResult:
        def __init__(self) -> None:
            self.changed_files = ["frontend/page.tsx"]

    class FakePrepared:
        def __init__(self) -> None:
            self.result = FakeExecutionResult()
            self.closed = False

        def close(self) -> None:
            self.closed = True

    class ExecutionWorkflow(FakeWorkflow):
        def __init__(self) -> None:
            super().__init__()
            self.prepared = FakePrepared()
            self.applied = False

        def execute(self, command, progress=None):
            self.calls.append(command)
            return TuiResult(
                "Execution preview",
                "# Devstill execution\n\n## Patch\n\n```diff\nchange\n```",
                self.prepared,
            )

        def apply_execution(self, prepared, progress=None):
            assert prepared is self.prepared
            self.applied = True
            prepared.close()
            return TuiResult("Execution applied", "# Applied")

    async def scenario() -> None:
        workflow = ExecutionWorkflow()
        app = build_app(tmp_path, workflow)
        async with app.run_test(size=(120, 40)) as pilot:
            await app.submit_text("/execute deep-plan")
            for _ in range(100):
                if app._composer_mode is ComposerMode.PATCH_APPROVAL:
                    break
                await pilot.pause(0.02)
            assert app._composer_mode is ComposerMode.PATCH_APPROVAL
            assert len(app.screen_stack) == 1
            assert not workflow.applied
            assert app.query_one("#cancel-execution", Button).display
            assert app.query_one("#apply-execution", Button).display
            assert app.query_one("#command-input", PromptTextArea).disabled
            entry = next(iter(app.query(SessionEntry)))
            assert "Awaiting approval" in entry.markdown
            assert "preview changes 1 approved file" in entry.markdown
            prompt = app.query_one("#command-input", PromptTextArea)
            await app.on_prompt_text_area_submitted(PromptTextArea.Submitted(prompt))
            assert not workflow.applied
            assert app._composer_mode is ComposerMode.PATCH_APPROVAL
            await pilot.pause()
            await pilot.click("#apply-execution")
            await wait_until_idle(app, pilot)
            assert workflow.applied
            assert workflow.prepared.closed
            entries = list(app.query(SessionEntry))
            assert len(entries) == 1
            assert entries[0].markdown == "# Applied"

    asyncio.run(scenario())


def test_pi_activity_streams_in_feed_then_collapses_above_diff(
    tmp_path: Path,
) -> None:
    class FakeExecutionResult:
        def __init__(self) -> None:
            self.changed_files = ["frontend/page.tsx"]
            self.tool_calls = 2
            self.elapsed_seconds = 1.25

    class FakePrepared:
        def __init__(self) -> None:
            self.result = FakeExecutionResult()
            self.closed = False

        def close(self) -> None:
            self.closed = True

    class StreamingWorkflow(FakeWorkflow):
        def __init__(self) -> None:
            super().__init__()
            self.prepared = FakePrepared()
            self.progress_ready = threading.Event()
            self.release = threading.Event()

        def execute(self, command, progress=None):
            self.calls.append(command)
            assert progress is not None
            progress("Preparing an isolated Pi execution workspace")
            progress("Starting Pi with local-model; reasoning is off")
            progress("Pi · read — frontend/page.tsx")
            self.progress_ready.set()
            assert self.release.wait(timeout=2)
            return TuiResult(
                "Execution preview",
                "# Devstill execution\n\n## Patch\n\n```diff\n+change\n```",
                self.prepared,
            )

    async def scenario() -> None:
        workflow = StreamingWorkflow()
        app = build_app(tmp_path, workflow)
        async with app.run_test(size=(120, 40)) as pilot:
            await app.submit_text("/execute plan")
            for _ in range(100):
                if workflow.progress_ready.is_set():
                    break
                await pilot.pause(0.02)

            entry = app.query_one(ExecutionSessionEntry)
            panel = entry.query_one(Collapsible)
            assert not panel.collapsed
            assert entry.activity == [
                "Preparing an isolated Pi execution workspace",
                "Starting Pi with local-model; reasoning is off",
                "Pi · read — frontend/page.tsx",
            ]
            assert "Pi · read — frontend/page.tsx" in str(
                app.query_one("#activity", Static).content
            )

            workflow.release.set()
            for _ in range(100):
                if app._composer_mode is ComposerMode.PATCH_APPROVAL:
                    break
                await pilot.pause(0.02)

            assert app._composer_mode is ComposerMode.PATCH_APPROVAL
            assert panel.collapsed
            assert "complete · 2 tool calls · 1.2s" in str(panel.title)
            assert "```diff\n+change\n```" in entry.markdown
            assert app.query_one("#apply-execution", Button).display

            panel.collapsed = False
            await pilot.pause()
            assert "Pi · read — frontend/page.tsx" in str(
                entry.query_one(".execution-activity-log", Static).content
            )

    asyncio.run(scenario())


def test_failed_pi_activity_stays_expanded_without_approval(tmp_path: Path) -> None:
    class FailingWorkflow(FakeWorkflow):
        def execute(self, command, progress=None):
            self.calls.append(command)
            assert progress is not None
            progress("Starting Pi with local-model; reasoning is off")
            progress("Pi · edit — frontend/page.tsx")
            raise RuntimeError("Pi stopped unexpectedly")

    async def scenario() -> None:
        app = build_app(tmp_path, FailingWorkflow())
        async with app.run_test(size=(120, 36)) as pilot:
            await app.submit_text("/execute plan")
            await wait_until_idle(app, pilot)

            entry = app.query_one(ExecutionSessionEntry)
            panel = entry.query_one(Collapsible)
            assert not panel.collapsed
            assert "failed" in str(panel.title)
            assert "Pi execution failed: Pi stopped unexpectedly" in entry.activity
            assert "Agent request failed" in entry.markdown
            assert not app.query_one("#apply-execution", Button).display

            entry.cancel_activity()
            assert not panel.collapsed
            assert "cancelled" in str(panel.title)
            assert entry.activity[-1] == "Pi execution cancelled"

    asyncio.run(scenario())


def test_inline_execution_cancellation_closes_the_prepared_workspace(
    tmp_path: Path,
) -> None:
    class FakeExecutionResult:
        def __init__(self) -> None:
            self.changed_files = ["app.py"]

    class FakePrepared:
        def __init__(self) -> None:
            self.result = FakeExecutionResult()
            self.closed = False

        def close(self) -> None:
            self.closed = True

    class ExecutionWorkflow(FakeWorkflow):
        def __init__(self) -> None:
            super().__init__()
            self.prepared = FakePrepared()

        def execute(self, command, progress=None):
            self.calls.append(command)
            return TuiResult(
                "Execution preview",
                "# Devstill execution\n## Patch\n```diff\n+change\n```",
                self.prepared,
            )

    async def scenario() -> None:
        workflow = ExecutionWorkflow()
        app = build_app(tmp_path, workflow)
        async with app.run_test(size=(120, 36)) as pilot:
            await app.submit_text("/execute plan")
            for _ in range(100):
                if app._composer_mode is ComposerMode.PATCH_APPROVAL:
                    break
                await pilot.pause(0.02)
            assert app._composer_mode is ComposerMode.PATCH_APPROVAL
            await pilot.pause()
            await pilot.click("#cancel-execution")
            await pilot.pause()

            assert workflow.prepared.closed
            assert app._composer_mode is ComposerMode.NORMAL
            assert app._pending_execution is None
            entry = next(iter(app.query(SessionEntry)))
            assert "working files are unchanged" in entry.markdown

    asyncio.run(scenario())


def test_successful_plan_offers_pi_with_optional_execution_instruction(
    tmp_path: Path,
) -> None:
    class HandoffWorkflow(FakeWorkflow):
        def execute(self, command, progress=None):
            self.calls.append(command)
            if command.kind is TuiCommandKind.PLAN:
                return TuiResult(
                    "Plan",
                    "# Devstill File-Impact Plan",
                    execution_offer=TuiExecutionOffer(
                        "plan",
                        "geo location needs to auto load",
                    ),
                )
            return TuiResult("Execution", "# Pi preview complete")

    async def scenario() -> None:
        workflow = HandoffWorkflow()
        app = build_app(tmp_path, workflow)
        async with app.run_test(size=(120, 40)) as pilot:
            await app.submit_text("/plan geo location needs to auto load")
            for _ in range(100):
                if app._composer_mode is ComposerMode.PI_HANDOFF:
                    break
                await pilot.pause(0.02)
            assert app._composer_mode is ComposerMode.PI_HANDOFF
            assert len(app.screen_stack) == 1
            await pilot.pause()
            assert app.query_one("#cancel-pi-handoff", Button).display
            assert app.query_one("#start-pi-handoff", Button).display
            await pilot.resize_terminal(72, 30)
            await pilot.pause()
            actions = app.query_one("#composer-actions")
            assert app.query_one("#start-pi-handoff", Button).region.right <= (
                actions.region.right
            )
            await app.submit_text("/files")
            assert len(workflow.calls) == 1
            assert "active Pi step" in str(app.query_one("#activity", Static).content)
            app.query_one(
                "#command-input", PromptTextArea
            ).text = "Reuse the existing permission helper."
            await pilot.click("#start-pi-handoff")
            await wait_until_idle(app, pilot)

            assert [command.kind for command in workflow.calls] == [
                TuiCommandKind.PLAN,
                TuiCommandKind.EXECUTE,
            ]
            execution = workflow.calls[1]
            assert execution.query == "plan"
            assert execution.instruction == "Reuse the existing permission helper."

    asyncio.run(scenario())


def test_pi_handoff_explains_unresolved_search_blocker(tmp_path: Path) -> None:
    plan = PlanOutput(
        query="auto load location",
        repository=str(tmp_path),
        model="local-model",
        items=[
            PlanItem(
                label=PlanLabel.SEARCH,
                target="frontend/src/components/checkin",
                reason="Locate the check-in component.",
            )
        ],
    )

    class SearchPlanWorkflow(FakeWorkflow):
        def execute(self, command, progress=None):
            self.calls.append(command)
            return TuiResult(
                "Deep Plan",
                "# Devstill Deep Plan",
                execution_offer=TuiExecutionOffer(
                    "deep-plan",
                    "auto load location",
                    unresolved_searches=2,
                    plan=plan,
                ),
            )

    async def scenario() -> None:
        workflow = SearchPlanWorkflow()
        app = build_app(tmp_path, workflow)
        async with app.run_test(size=(120, 40)) as pilot:
            await app.submit_text("/deep-plan auto load location")
            for _ in range(100):
                if app._composer_mode is ComposerMode.PI_HANDOFF:
                    break
                await pilot.pause(0.02)
            assert app._composer_mode is ComposerMode.PI_HANDOFF
            assert len(app.screen_stack) == 1
            await pilot.pause()
            screen_text = "\n".join(str(widget.content) for widget in app.query(Static))
            assert "2 unresolved Search items" in screen_text
            assert not app.query_one("#command-input", PromptTextArea).disabled
            assert not app.query_one("#start-pi-handoff", Button).display
            await pilot.click("#cancel-pi-handoff")
            await pilot.pause()
            assert app._composer_mode is ComposerMode.NORMAL
            assert len(workflow.calls) == 1
            assert workflow.calls[0].kind is TuiCommandKind.DEEP_PLAN

    asyncio.run(scenario())


def test_manual_plan_review_resolves_search_and_relabels_existing_file(
    tmp_path: Path,
) -> None:
    component = tmp_path / "frontend/src/pages/CheckInPage/index.tsx"
    package = tmp_path / "frontend/package.json"
    component.parent.mkdir(parents=True)
    package.parent.mkdir(parents=True, exist_ok=True)
    component.write_text("export function CheckInPage() {}\n", encoding="utf-8")
    package.write_text("{}\n", encoding="utf-8")
    plan = PlanOutput(
        query="check-in page needs to auto load geo location",
        repository=str(tmp_path),
        model="local-model",
        items=[
            PlanItem(
                label=PlanLabel.SEARCH,
                target="frontend/src/components/checkin",
                reason="Identify the check-in page component.",
            ),
            PlanItem(
                label=PlanLabel.READ,
                target="frontend/package.json",
                reason="Confirm the geolocation dependency.",
            ),
        ],
    )

    class ReviewWorkflow(FakeWorkflow):
        def __init__(self) -> None:
            super().__init__()
            self.saved_plan: PlanOutput | None = None

        def execute(self, command, progress=None):
            self.calls.append(command)
            if command.kind is TuiCommandKind.DEEP_PLAN:
                return TuiResult(
                    "Deep Plan",
                    "# Devstill Deep Plan\n\nSearch is unresolved.",
                    execution_offer=TuiExecutionOffer(
                        "deep-plan",
                        plan.query,
                        unresolved_searches=1,
                        plan=plan,
                    ),
                )
            return TuiResult("Execution", "# Pi preview complete")

        def save_manual_plan(self, source, reviewed, *, allow_unresolved=False):
            assert source == "deep-plan"
            assert allow_unresolved
            validate_reviewed_plan(reviewed, tmp_path)
            self.saved_plan = reviewed
            unresolved = sum(item.label is PlanLabel.SEARCH for item in reviewed.items)
            return (
                TuiExecutionOffer(source, reviewed.query, unresolved, reviewed),
                "# Devstill Deep Plan\n\nManual labels saved.",
            )

    async def scenario() -> None:
        workflow = ReviewWorkflow()
        app = build_app(tmp_path, workflow)
        async with app.run_test(size=(120, 48)) as pilot:
            await app.submit_text("/deep-plan check-in page needs location")
            for _ in range(100):
                if app._composer_mode is ComposerMode.PI_HANDOFF:
                    break
                await pilot.pause(0.02)
            await pilot.pause()
            assert app._composer_mode is ComposerMode.PI_HANDOFF
            assert len(app.screen_stack) == 1
            entry = app.query_one(PlanSessionEntry)
            rows = list(entry.query(PlanImpactRow))
            app.query_one(
                "#command-input", PromptTextArea
            ).text = "Keep the existing permission helper."
            rows[0].query_one(Select).value = "Modify"
            await pilot.pause()
            assert app._composer_mode is ComposerMode.SEARCH_PATH
            assert app.query_one("#command-input", PromptTextArea).placeholder == (
                "Exact repository-relative file path"
            )

            await pilot.click("#use-search-path")
            await pilot.pause()
            assert "exact repository-relative file path" in str(
                entry.query_one(".plan-impact-status", Static).content
            )

            app.query_one(
                "#command-input", PromptTextArea
            ).text = "frontend/src/pages/CheckInPage/index.tsx"
            await pilot.click("#use-search-path")
            await pilot.pause()
            assert app._composer_mode is ComposerMode.PI_HANDOFF
            assert app.query_one("#command-input", PromptTextArea).text == (
                "Keep the existing permission helper."
            )
            rows = list(entry.query(PlanImpactRow))
            rows[1].query_one(Select).value = "Add"
            for _ in range(20):
                if (
                    workflow.saved_plan is not None
                    and workflow.saved_plan.items[1].label is PlanLabel.ADD
                ):
                    break
                await pilot.pause(0.01)
            assert app.query_one("#start-pi-handoff", Button).display
            assert workflow.saved_plan is not None
            assert [item.label for item in workflow.saved_plan.items] == [
                PlanLabel.MODIFY,
                PlanLabel.ADD,
            ]
            assert workflow.saved_plan.items[0].target == (
                "frontend/src/pages/CheckInPage/index.tsx"
            )
            assert workflow.saved_plan.items[0].reason == (
                "Identify the check-in page component."
            )

            await pilot.click("#start-pi-handoff")
            await wait_until_idle(app, pilot)
            assert workflow.calls[-1].kind is TuiCommandKind.EXECUTE
            assert workflow.calls[-1].query == "deep-plan"
            assert workflow.calls[-1].instruction == (
                "Keep the existing permission helper."
            )

    asyncio.run(scenario())


def test_inline_label_save_failure_reverts_the_dropdown(tmp_path: Path) -> None:
    target = tmp_path / "app.py"
    target.write_text("value = 1\n", encoding="utf-8")
    plan = PlanOutput(
        query="update app",
        repository=str(tmp_path),
        model="local-model",
        items=[
            PlanItem(
                label=PlanLabel.READ,
                target="app.py",
                reason="Inspect the current implementation.",
            )
        ],
    )

    class FailingSaveWorkflow(FakeWorkflow):
        def execute(self, command, progress=None):
            self.calls.append(command)
            return TuiResult(
                "Plan",
                "# Plan",
                execution_offer=TuiExecutionOffer("plan", plan.query, 0, plan),
            )

        def save_manual_plan(self, source, reviewed, *, allow_unresolved=False):
            raise OSError("artifact storage is unavailable")

    async def scenario() -> None:
        app = build_app(tmp_path, FailingSaveWorkflow())
        async with app.run_test(size=(120, 40)) as pilot:
            await app.submit_text("/plan update app")
            for _ in range(100):
                if app._composer_mode is ComposerMode.PI_HANDOFF:
                    break
                await pilot.pause(0.02)
            entry = app.query_one(PlanSessionEntry)
            select = entry.query_one(PlanImpactRow).query_one(Select)
            select.value = PlanLabel.MODIFY.value
            await pilot.pause()

            assert select.value == PlanLabel.READ.value
            assert "artifact storage is unavailable" in str(
                entry.query_one(".plan-impact-status", Static).content
            )
            assert plan.items[0].reason in str(
                entry.query_one(".plan-impact-reason", Static).content
            )

    asyncio.run(scenario())


def test_plan_scrolls_after_layout_and_keeps_long_targets_visible(
    tmp_path: Path,
) -> None:
    items = [
        PlanItem(
            label=PlanLabel.READ,
            target=(
                "frontend/src/pages/CheckInPage/components/"
                f"VeryLongLocationComponent{index}.tsx"
            ),
            reason=f"Inspect location behavior number {index} before implementation.",
        )
        for index in range(12)
    ]
    plan = PlanOutput(
        query="automatically load geolocation",
        repository=str(tmp_path),
        model="local-model",
        items=items,
    )

    class PlanWorkflow(FakeWorkflow):
        def execute(self, command, progress=None):
            self.calls.append(command)
            return TuiResult(
                "Plan",
                "# Plan",
                execution_offer=TuiExecutionOffer("plan", plan.query, 0, plan),
            )

    async def scenario() -> None:
        app = build_app(tmp_path, PlanWorkflow())
        async with app.run_test(size=(64, 20)) as pilot:
            await app.submit_text("/plan automatically load geolocation")
            for _ in range(100):
                if app._composer_mode is ComposerMode.PI_HANDOFF:
                    break
                await pilot.pause(0.02)
            await pilot.pause()
            await pilot.pause()

            feed = app.query_one("#session-feed")
            assert feed.max_scroll_y > 0
            assert feed.scroll_y == feed.max_scroll_y
            target = list(app.query(PlanImpactRow))[-1].query_one(
                ".plan-impact-target", Static
            )
            assert items[-1].target in str(target.content)
            assert target.content_region.height > 1

    asyncio.run(scenario())


def test_search_plan_target_is_identified_as_unresolved(tmp_path: Path) -> None:
    item = PlanItem(
        label=PlanLabel.SEARCH,
        target="CheckInPage captureLocation",
        reason="Locate the implementation before changing it.",
    )

    async def scenario() -> None:
        app = build_app(tmp_path)
        async with app.run_test(size=(80, 24)) as pilot:
            row = PlanImpactRow(0, item)
            await app.query_one("#session-feed").mount(row)
            await pilot.pause()
            assert "unresolved search target" in str(
                row.query_one(".plan-impact-target", Static).content
            )

    asyncio.run(scenario())


def test_search_resolution_merges_an_existing_plan_target(tmp_path: Path) -> None:
    target = tmp_path / "frontend/src/pages/CheckInPage/index.tsx"
    target.parent.mkdir(parents=True)
    target.write_text("export function CheckInPage() {}\n", encoding="utf-8")
    plan = PlanOutput(
        query="auto load location",
        repository=str(tmp_path),
        model="local-model",
        items=[
            PlanItem(
                label=PlanLabel.SEARCH,
                target="CheckInPage captureLocation",
                reason="Locate the capture implementation.",
            ),
            PlanItem(
                label=PlanLabel.MODIFY,
                target="frontend/src/pages/CheckInPage/index.tsx",
                reason="Trigger location capture when the component mounts.",
            ),
        ],
    )

    class MergeWorkflow(FakeWorkflow):
        def __init__(self) -> None:
            super().__init__()
            self.saved_plan: PlanOutput | None = None

        def execute(self, command, progress=None):
            self.calls.append(command)
            return TuiResult(
                "Plan",
                "# Plan",
                execution_offer=TuiExecutionOffer("plan", plan.query, 1, plan),
            )

        def save_manual_plan(self, source, reviewed, *, allow_unresolved=False):
            assert source == "plan"
            assert allow_unresolved
            validate_reviewed_plan(reviewed, tmp_path)
            self.saved_plan = reviewed
            unresolved = sum(item.label is PlanLabel.SEARCH for item in reviewed.items)
            return (
                TuiExecutionOffer(source, reviewed.query, unresolved, reviewed),
                "# Plan",
            )

    async def scenario() -> None:
        workflow = MergeWorkflow()
        app = build_app(tmp_path, workflow)
        async with app.run_test(size=(120, 40)) as pilot:
            await app.submit_text("/plan auto load location")
            for _ in range(100):
                if app._composer_mode is ComposerMode.PI_HANDOFF:
                    break
                await pilot.pause(0.02)
            entry = app.query_one(PlanSessionEntry)
            search_row = next(
                row
                for row in entry.query(PlanImpactRow)
                if row.item.label is PlanLabel.SEARCH
            )
            search_row.query_one(Select).value = PlanLabel.MODIFY.value
            await pilot.pause()
            assert app._composer_mode is ComposerMode.SEARCH_PATH

            app.query_one(
                "#command-input", PromptTextArea
            ).text = "frontend\\src\\pages\\CheckInPage\\index.tsx"
            await pilot.click("#use-search-path")
            await pilot.pause()

            assert workflow.saved_plan is not None
            assert workflow.saved_plan.items == [plan.items[1]]
            assert app._composer_mode is ComposerMode.PI_HANDOFF
            assert app._handoff_offer is not None
            assert app._handoff_offer.unresolved_searches == 0
            assert app.query_one("#start-pi-handoff", Button).display
            visible_rows = [row for row in entry.query(PlanImpactRow) if row.display]
            assert len(visible_rows) == 1
            assert visible_rows[0].item == plan.items[1]
            assert "existing Modify label was kept" in str(
                entry.query_one(".plan-impact-status", Static).content
            )

    asyncio.run(scenario())


def test_multiline_prompt_alignment_growth_and_shift_enter(tmp_path: Path) -> None:
    async def scenario() -> None:
        workflow = FakeWorkflow()
        app = build_app(tmp_path, workflow)
        async with app.run_test(size=(120, 40)) as pilot:
            prompt = app.query_one("#command-input", PromptTextArea)
            marker = app.query_one("#prompt-marker", Static)
            assert marker.content_region.y == prompt.content_region.y

            await pilot.press(*list("first line"), "shift+enter", *list("second line"))
            await pilot.pause()
            assert prompt.text == "first line\nsecond line"
            assert prompt.size.height == 2
            assert marker.content_region.y == prompt.content_region.y

            await pilot.press("enter")
            await wait_until_idle(app, pilot)
            assert len(workflow.calls) == 1
            assert workflow.calls[0].query == "first line\nsecond line"

    asyncio.run(scenario())


def test_tab_completes_slash_palette_without_execution(tmp_path: Path) -> None:
    async def scenario() -> None:
        workflow = FakeWorkflow()
        app = build_app(tmp_path, workflow)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.press("/", "c", "o")
            await pilot.pause()
            palette = app.query_one("#command-palette", OptionList)
            assert palette.has_class("visible")
            assert palette.option_count == 1
            assert palette.get_option_at_index(0).id == "/context "
            await pilot.press("tab")
            await pilot.pause()
            assert app.query_one("#command-input", PromptTextArea).text == "/context "
            assert workflow.calls == []

            app.query_one("#command-input", PromptTextArea).text = "/a"
            await pilot.pause()
            assert palette.option_count == 3
            await pilot.press("escape")
            assert not palette.has_class("visible")

    asyncio.run(scenario())


def test_index_worker_shows_stage_and_determinate_progress(tmp_path: Path) -> None:
    class ProgressWorkflow(FakeWorkflow):
        def __init__(self) -> None:
            super().__init__()
            self.started = threading.Event()
            self.release = threading.Event()

        def execute(self, command, progress=None):
            self.calls.append(command)
            assert progress is not None
            progress(
                IndexProgress(
                    "parsing",
                    "Assessing core/views.py",
                    3,
                    10,
                    files=IndexFileProgress(
                        total=628,
                        checked=628,
                        changed=10,
                        unchanged=618,
                        parsed=3,
                    ),
                )
            )
            self.started.set()
            self.release.wait(timeout=5)
            return TuiResult("Index", "# Index updated")

    async def scenario() -> None:
        workflow = ProgressWorkflow()
        app = build_app(tmp_path, workflow)
        async with app.run_test(size=(80, 30)) as pilot:
            await app.submit_text("/index")
            for _ in range(100):
                if workflow.started.is_set():
                    break
                await pilot.pause(0.01)
            assert workflow.started.is_set()

            progress_bar = app.query_one("#command-progress", ProgressBar)
            assert progress_bar.display
            assert progress_bar.total == 10
            assert progress_bar.progress == 3
            activity = str(app.query_one("#activity").content)
            assert "PARSING" in activity
            assert "Assessing core/views.py (3/10)" in activity
            file_status = app.query_one("#file-progress")
            assert file_status.display
            assert "628 total" in str(file_status.content)
            assert "10 changed" in str(file_status.content)
            assert "3 parsed" in str(file_status.content)

            workflow.release.set()
            await wait_until_idle(app, pilot)
            assert not progress_bar.display
            assert not file_status.display
            assert len(app.query(SessionEntry)) == 1

    asyncio.run(scenario())


def test_failed_index_hides_file_and_stage_progress(tmp_path: Path) -> None:
    class FailedIndexWorkflow(FakeWorkflow):
        def execute(self, command, progress=None):
            assert progress is not None
            progress(
                IndexProgress(
                    "checking",
                    "Checking repository files",
                    4,
                    10,
                    files=IndexFileProgress(total=10, checked=4, changed=2),
                )
            )
            raise RuntimeError("index failed")

    async def scenario() -> None:
        app = build_app(tmp_path, FailedIndexWorkflow())
        async with app.run_test(size=(80, 30)) as pilot:
            await app.submit_text("/index")
            await wait_until_idle(app, pilot)

            assert not app.query_one("#file-progress").display
            assert not app.query_one("#command-progress").display
            entries = list(app.query(SessionEntry))
            assert len(entries) == 1
            assert entries[0].error

    asyncio.run(scenario())


def test_plan_preview_is_replaced_in_place_when_agent_finishes(tmp_path: Path) -> None:
    class PlanWorkflow(FakeWorkflow):
        def __init__(self) -> None:
            super().__init__()
            self.preview_ready = threading.Event()
            self.release = threading.Event()

        def execute(self, command, progress=None):
            self.calls.append(command)
            assert progress is not None
            progress(
                TuiPreview(
                    "# Retrieved plan context\n\n"
                    "## ⚪ Pending — core/views.py\n\n"
                    "Retrieved chunks: `register_api`"
                )
            )
            self.preview_ready.set()
            self.release.wait(timeout=5)
            return TuiResult(
                "Plan",
                "# Devstill File-Impact Plan\n\n"
                "## 🟢 Add — core/views.py\n\n"
                "Add activation-code handling to registration.",
            )

    async def scenario() -> None:
        workflow = PlanWorkflow()
        app = build_app(tmp_path, workflow)
        async with app.run_test(size=(120, 40)) as pilot:
            await app.submit_text("/plan add account activation")
            for _ in range(100):
                if workflow.preview_ready.is_set():
                    break
                await pilot.pause(0.01)
            assert workflow.preview_ready.is_set()
            entries = list(app.query(SessionEntry))
            assert len(entries) == 1
            entry = entries[0]
            assert "⚪ Pending — core/views.py" in entry.markdown
            assert "Add activation-code" not in entry.markdown
            assert (
                "waiting for the agent"
                in str(app.query_one("#activity").content).casefold()
            )

            workflow.release.set()
            await wait_until_idle(app, pilot)
            entries = list(app.query(SessionEntry))
            assert entries == [entry]
            assert "🟢 Add — core/views.py" in entry.markdown
            assert "Add activation-code handling" in entry.markdown
            assert "⚪ Pending" not in entry.markdown

    asyncio.run(scenario())


def test_deep_plan_reuses_one_entry_through_pending_and_final_stages(
    tmp_path: Path,
) -> None:
    class DeepPlanWorkflow(FakeWorkflow):
        def __init__(self) -> None:
            super().__init__()
            self.deep_context_ready = threading.Event()
            self.release = threading.Event()

        def execute(self, command, progress=None):
            self.calls.append(command)
            assert progress is not None
            progress(TuiPreview("# Retrieved Deep Plan context"))
            progress(
                TuiPreview(
                    "# Devstill Deep Plan — Initial Pass\n\n"
                    "## 🟣 Modify — frontend/page.tsx\n\n"
                    "Load location during page mount.",
                    status="Initial Deep Plan ready; searching its reasons",
                )
            )
            progress(
                TuiPreview(
                    "# Deepdive context\n\n## ⚪ Pending — frontend/geo.ts",
                    status="Deepdive context ready; waiting for final plan",
                )
            )
            self.deep_context_ready.set()
            self.release.wait(timeout=5)
            return TuiResult(
                "Deep Plan",
                "# Devstill Deep Plan\n\n"
                "## 🟣 Modify — frontend/page.tsx\n\n"
                "Trigger location automatically.\n\n"
                "## 🔵 Read — frontend/geo.ts\n\n"
                "Reuse the shared helper.",
            )

    async def scenario() -> None:
        workflow = DeepPlanWorkflow()
        app = build_app(tmp_path, workflow)
        async with app.run_test(size=(120, 40)) as pilot:
            await app.submit_text("/deep-plan auto location")
            for _ in range(100):
                if workflow.deep_context_ready.is_set():
                    break
                await pilot.pause(0.01)
            assert workflow.deep_context_ready.is_set()
            entries = list(app.query(SessionEntry))
            assert len(entries) == 1
            entry = entries[0]
            assert "⚪ Pending — frontend/geo.ts" in entry.markdown
            assert "waiting for final plan" in str(app.query_one("#activity").content)

            workflow.release.set()
            await wait_until_idle(app, pilot)
            assert list(app.query(SessionEntry)) == [entry]
            assert "🔵 Read — frontend/geo.ts" in entry.markdown
            assert "⚪ Pending" not in entry.markdown

    asyncio.run(scenario())


def test_failed_plan_keeps_retrieved_files_visible(tmp_path: Path) -> None:
    class FailedPlanWorkflow(FakeWorkflow):
        def execute(self, command, progress=None):
            self.calls.append(command)
            assert progress is not None
            progress(
                TuiPreview("# Retrieved plan context\n\n## ⚪ Pending — core/views.py")
            )
            raise RuntimeError("agent endpoint unavailable")

    async def scenario() -> None:
        app = build_app(tmp_path, FailedPlanWorkflow())
        async with app.run_test(size=(120, 40)) as pilot:
            await app.submit_text("/plan add account activation")
            await wait_until_idle(app, pilot)

            entries = list(app.query(SessionEntry))
            assert len(entries) == 1
            assert entries[0].error
            assert "⚪ Pending — core/views.py" in entries[0].markdown
            assert "Agent request failed" in entries[0].markdown
            assert "agent endpoint unavailable" in entries[0].markdown

    asyncio.run(scenario())


def test_agent_setup_modal_saves_remembered_token_and_tests(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        workflow = FakeWorkflow()
        app = build_app(tmp_path, workflow)
        async with app.run_test(size=(120, 45)) as pilot:
            await app.submit_text("/agent configure")
            await pilot.pause()
            assert isinstance(app.screen, AgentSetupScreen)
            app.screen.query_one(
                "#agent-base-url", Input
            ).value = "http://localhost:9000/v1"
            app.screen.query_one("#agent-model", Input).value = "local-12b"
            app.screen.query_one("#agent-api-key", Input).value = "remember-me"
            app.screen.query_one("#agent-planning-max-tokens", Input).value = "7000"
            app.screen.query_one("#agent-planning-reasoning", Select).value = "auto"
            await pilot.click("#agent-save-test")
            await pilot.pause()
            await wait_until_idle(app, pilot)

            saved = load_config(tmp_path / "devstill.json")
            assert saved.llm.enabled
            assert saved.llm.base_url == "http://localhost:9000/v1"
            assert saved.llm.model == "local-12b"
            assert saved.llm.max_output_tokens == 2048
            assert saved.llm.planning_max_output_tokens == 7000
            assert saved.llm.planning_reasoning == "auto"
            assert "remember-me" not in (tmp_path / "devstill.json").read_text(
                encoding="utf-8"
            )
            assert saved_api_key(saved.llm) == "remember-me"
            assert workflow.calls[-1].kind is TuiCommandKind.AGENT_TEST
            entries = list(app.query(SessionEntry))
            assert any(entry.command == "/agent configure" for entry in entries)

    asyncio.run(scenario())


def test_agent_setup_modal_validates_and_forgets_saved_token(tmp_path: Path) -> None:
    async def scenario() -> None:
        config = DevstillConfig(
            llm={
                "enabled": True,
                "base_url": "http://localhost:8080/v1",
                "model": "local-model",
            }
        )
        config_path = tmp_path / "devstill.json"
        config_path.write_text(config.model_dump_json(), encoding="utf-8")
        store_api_key(config.llm, "saved-token")
        app = DevstillApp(config_path, config, workflow=FakeWorkflow())
        async with app.run_test(size=(120, 45)) as pilot:
            await app.submit_text("/agent configure")
            await pilot.pause()
            app.screen.query_one("#agent-api-key-env", Input).value = "invalid-name"
            await pilot.click("#agent-save")
            await pilot.pause()
            assert isinstance(app.screen, AgentSetupScreen)
            assert app.screen.query_one("#agent-form-error").content

            app.screen.query_one(
                "#agent-api-key-env", Input
            ).value = "DEVSTILL_AGENT_API_KEY"
            await pilot.click("#agent-forget")
            await pilot.pause()
            assert saved_api_key(config.llm) is None
            assert not isinstance(app.screen, AgentSetupScreen)

    asyncio.run(scenario())


def test_sidebar_selection_prefills_and_exact_command_executes(tmp_path: Path) -> None:
    async def scenario() -> None:
        workflow = FakeWorkflow()
        app = build_app(tmp_path, workflow)
        async with app.run_test(size=(120, 40)) as pilot:
            sidebar = app.query_one("#sidebar-commands", OptionList)
            sidebar.highlighted = sidebar.get_option_index("/files")
            sidebar.focus()
            await pilot.press("enter")
            await pilot.pause()
            prompt = app.query_one("#command-input", PromptTextArea)
            assert prompt.text == "/files"
            assert prompt.has_focus
            assert workflow.calls == []

            await pilot.press("enter")
            await wait_until_idle(app, pilot)
            assert workflow.calls[0].kind is TuiCommandKind.FILES

    asyncio.run(scenario())


def test_help_clear_and_command_errors_stay_in_session(tmp_path: Path) -> None:
    async def scenario() -> None:
        workflow = FakeWorkflow()
        app = build_app(tmp_path, workflow)
        async with app.run_test(size=(120, 40)) as pilot:
            await app.submit_text("/help")
            assert len(app.query(SessionEntry)) == 1
            assert workflow.calls == []
            await app.submit_text("/plan")
            entries = list(app.query(SessionEntry))
            assert len(entries) == 2
            assert entries[-1].error
            await pilot.press("ctrl+l")
            await pilot.pause()
            assert len(app.query(SessionEntry)) == 0

    asyncio.run(scenario())


def test_worker_failure_restores_prompt_and_reports_error(tmp_path: Path) -> None:
    async def scenario() -> None:
        workflow = FakeWorkflow(error=RuntimeError("endpoint unavailable"))
        app = build_app(tmp_path, workflow)
        async with app.run_test(size=(120, 40)) as pilot:
            await app.submit_text("/agent test")
            assert app._busy
            assert app.query_one("#command-input", PromptTextArea).disabled
            await wait_until_idle(app, pilot)
            entries = list(app.query(SessionEntry))
            assert len(entries) == 1
            assert entries[0].error
            assert not app.query_one("#command-input", PromptTextArea).disabled
            assert app.query_one("#command-input", PromptTextArea).has_focus

    asyncio.run(scenario())


def test_tui_prevents_overlapping_workers(tmp_path: Path) -> None:
    class BlockingWorkflow(FakeWorkflow):
        def __init__(self) -> None:
            super().__init__()
            self.started = threading.Event()
            self.release = threading.Event()

        def execute(self, command, progress=None):
            self.calls.append(command)
            self.started.set()
            self.release.wait(timeout=5)
            return TuiResult("Done", "# Done")

    async def scenario() -> None:
        workflow = BlockingWorkflow()
        app = build_app(tmp_path, workflow)
        async with app.run_test(size=(120, 40)) as pilot:
            await app.submit_text("first")
            for _ in range(100):
                if workflow.started.is_set():
                    break
                await pilot.pause(0.01)
            assert workflow.started.is_set()
            await app.submit_text("second")
            assert len(workflow.calls) == 1
            assert "Wait for the current command" in str(
                app.query_one("#activity").content
            )
            workflow.release.set()
            await wait_until_idle(app, pilot)

    asyncio.run(scenario())


def test_session_feed_retains_only_fifty_commands(tmp_path: Path) -> None:
    async def scenario() -> None:
        app = build_app(tmp_path)
        async with app.run_test(size=(120, 40)):
            for index in range(51):
                await app._append_entry(f"query {index}", f"# Result {index}")
            entries = list(app.query(SessionEntry))
            assert len(entries) == 50
            assert entries[0].command == "query 1"
            assert entries[-1].command == "query 50"

    asyncio.run(scenario())
