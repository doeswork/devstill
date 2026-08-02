from __future__ import annotations

import asyncio
import threading
from pathlib import Path

from textual.widgets import Input, OptionList, ProgressBar, Static

from devstill.config import DevstillConfig, load_config
from devstill.credentials import saved_api_key, store_api_key
from devstill.models import IndexFileProgress, IndexProgress
from devstill.tui import DevstillApp, SessionEntry
from devstill.tui_agent import AgentSetupScreen
from devstill.tui_commands import TuiCommandKind, TuiPreview, TuiResult


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
            assert app.query_one("#command-input", Input).has_focus
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
            assert app.query_one("#command-input", Input).has_focus
            assert not app.query_one("#command-input", Input).disabled

    asyncio.run(scenario())


def test_ctrl_c_exits_even_when_prompt_is_focused(tmp_path: Path) -> None:
    async def scenario() -> None:
        workflow = FakeWorkflow()
        app = build_app(tmp_path, workflow)
        async with app.run_test(size=(120, 40)) as pilot:
            assert app.query_one("#command-input", Input).has_focus
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
            prompt = app.query_one("#command-input", Input)
            prompt.value = "copy this"
            prompt.select_all()

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
            assert app.query_one("#command-input", Input).has_focus

            await pilot.press(*list("register"), "enter")
            await wait_until_idle(app, pilot)
            await pilot.press("up")
            assert app.query_one("#command-input", Input).value == "register"
            await pilot.press("up")
            assert app.query_one("#command-input", Input).value == "login"
            await pilot.press("down")
            assert app.query_one("#command-input", Input).value == "register"

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
            assert app.query_one("#command-input", Input).value == "/context "
            assert workflow.calls == []

            app.query_one("#command-input", Input).value = "/a"
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
            await pilot.click("#agent-save-test")
            await pilot.pause()
            await wait_until_idle(app, pilot)

            saved = load_config(tmp_path / "devstill.json")
            assert saved.llm.enabled
            assert saved.llm.base_url == "http://localhost:9000/v1"
            assert saved.llm.model == "local-12b"
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
            prompt = app.query_one("#command-input", Input)
            assert prompt.value == "/files"
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
            assert app.query_one("#command-input", Input).disabled
            await wait_until_idle(app, pilot)
            entries = list(app.query(SessionEntry))
            assert len(entries) == 1
            assert entries[0].error
            assert not app.query_one("#command-input", Input).disabled
            assert app.query_one("#command-input", Input).has_focus

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
