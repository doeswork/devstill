from __future__ import annotations

import asyncio
import threading

from textual.widgets import Button, Markdown, Static

from devstill.planning import PlanItem, PlanLabel, PlanOutput
from devstill.tui import PlanImpactRow, PlanReasonEditor, PlanSessionEntry
from devstill.tui_commands import TuiExecutionOffer
from devstill.tui_file_questions import FileQuestionInput, FileQuestionScreen
from tests.test_tui import FakeWorkflow, build_app


async def wait_for(pilot, condition):
    for _ in range(100):
        if condition():
            await pilot.pause()
            return
        await pilot.pause(0.02)
    raise AssertionError("File question request did not reach expected state")


class FakeSession:
    def __init__(self, *, blocked=False):
        self.calls = []
        self.release = threading.Event()
        self.finished = threading.Event()
        self.fail = False
        if not blocked:
            self.release.set()

    def respond(self, question=None):
        self.calls.append(question)
        self.release.wait(5)
        self.finished.set()
        if self.fail:
            raise RuntimeError("Agent timed out")
        return (
            "**Initial review:** The explanation fits the goal."
            if question is None
            else f"Answer: {question}"
        )


def test_opens_immediately_and_answers_queued_and_followup_questions(tmp_path):
    async def scenario():
        app = build_app(tmp_path)
        session = FakeSession(blocked=True)
        async with app.run_test(size=(100, 36)) as pilot:
            screen = FileQuestionScreen("player.js", lambda: session)
            await app.push_screen(screen)
            await wait_for(pilot, lambda: session.calls == [None])
            editor = screen.query_one(FileQuestionInput)
            assert editor.has_focus
            assert not editor.disabled
            editor.text = "Does collision detection change?"
            await pilot.press("enter")
            assert session.calls == [None]
            assert "collision" in str(
                screen.query_one("#file-question-queued", Static).content
            )
            session.release.set()
            await wait_for(pilot, lambda: len(session.calls) == 2 and not screen._busy)
            assert session.calls == [None, "Does collision detection change?"]
            assert any(
                "Initial review:" in block.source for block in screen.query(Markdown)
            )
            assert any(
                "Answer: Does collision" in block.source
                for block in screen.query(Markdown)
            )

            editor.text = "Which symbol?"
            await pilot.press("shift+enter")
            assert "\n" in editor.text
            await pilot.press("enter")
            await wait_for(pilot, lambda: len(session.calls) == 3 and not screen._busy)
            await pilot.resize_terminal(64, 24)
            await pilot.pause()
            dialog = screen.query_one("#file-question-dialog")
            assert dialog.region.right <= screen.size.width
            assert (
                screen.query_one("#file-question-close").region.bottom
                <= screen.size.height
            )
            await pilot.press("escape")
            assert app.screen is not screen

    asyncio.run(scenario())


def test_failed_review_retries_without_losing_queued_question(tmp_path):
    async def scenario():
        app = build_app(tmp_path)
        session = FakeSession(blocked=True)
        session.fail = True
        async with app.run_test(size=(100, 36)) as pilot:
            screen = FileQuestionScreen("player.js", lambda: session)
            await app.push_screen(screen)
            await wait_for(pilot, lambda: bool(session.calls))
            screen.query_one(FileQuestionInput).text = "Why this file?"
            await pilot.press("enter")
            session.release.set()
            await wait_for(pilot, lambda: not screen._busy)
            assert "Agent timed out" in str(
                screen.query_one("#file-question-status", Static).content
            )
            assert screen.query_one("#file-question-retry").display
            session.fail = False
            screen.query_one("#file-question-retry", Button).press()
            await wait_for(pilot, lambda: len(session.calls) == 3 and not screen._busy)
            assert session.calls == [None, None, "Why this file?"]
            assert not screen.query_one("#file-question-retry").display

    asyncio.run(scenario())


def test_closing_modal_ignores_late_review_and_drops_queued_request(tmp_path):
    async def scenario():
        app = build_app(tmp_path)
        old = FakeSession(blocked=True)
        current = FakeSession()
        async with app.run_test(size=(100, 36)) as pilot:
            screen = FileQuestionScreen("old.js", lambda: old)
            await app.push_screen(screen)
            await wait_for(pilot, lambda: bool(old.calls))
            screen.query_one(FileQuestionInput).text = "Queued old question"
            await pilot.press("enter", "escape")
            replacement = FileQuestionScreen("current.js", lambda: current)
            await app.push_screen(replacement)
            old.release.set()
            await wait_for(
                pilot,
                lambda: (
                    old.finished.is_set()
                    and bool(current.calls)
                    and not replacement._busy
                ),
            )
            assert app.screen is replacement
            assert old.calls == [None]
            assert current.calls == [None]
            assert replacement.session is current

    asyncio.run(scenario())


def test_question_button_uses_current_plan_and_flushes_reason_draft(tmp_path):
    plan = PlanOutput(
        query="Toggle camera",
        repository=str(tmp_path),
        model="local",
        refinements=["Keep movement controls"],
        items=[
            PlanItem(
                label=PlanLabel.MODIFY, target="player.js", reason="Original reason."
            ),
            PlanItem(
                label=PlanLabel.MODIFY, target="game.js", reason="Original keyboard reason."
            ),
        ],
    )
    session = FakeSession()

    class Workflow(FakeWorkflow):
        snapshot = None
        saved = None

        def save_manual_plan(self, source, reviewed, **_kwargs):
            self.saved = reviewed
            return TuiExecutionOffer(source, reviewed.query, 0, reviewed), ""

        def file_question_session(self, reviewed, index):
            self.snapshot = reviewed
            assert index == 0
            return session

    async def scenario():
        workflow = Workflow()
        app = build_app(tmp_path, workflow)
        async with app.run_test(size=(130, 40)) as pilot:
            entry = PlanSessionEntry("/plan Toggle camera", plan, "plan")
            await app.query_one("#session-feed").mount(entry)
            app._enter_handoff(TuiExecutionOffer("plan", plan.query, 0, plan), entry)
            await pilot.pause()
            row = entry.query_one(PlanImpactRow)
            row.query_one(
                PlanReasonEditor
            ).text = "Use the camera state to preserve the controls."
            list(entry.query(PlanImpactRow))[1].query_one(PlanReasonEditor).text = (
                "Keep existing keyboard bindings while adding c."
            )
            row.query_one(".plan-impact-question", Button).press()
            await wait_for(pilot, lambda: bool(session.calls))
            assert isinstance(app.screen, FileQuestionScreen)
            assert (
                workflow.snapshot.items[0].reason
                == "Use the camera state to preserve the controls."
            )
            assert workflow.snapshot.refinements == ["Keep movement controls"]
            assert (
                workflow.snapshot.items[1].reason
                == "Keep existing keyboard bindings while adding c."
            )
            assert workflow.snapshot is not workflow.saved
            assert not workflow.calls  # No Pi command or plan refinement was triggered.
            await pilot.press("escape")
            assert not isinstance(app.screen, FileQuestionScreen)
            assert app._handoff_offer.plan == workflow.saved

    asyncio.run(scenario())


def test_question_button_unavailable_for_new_and_unresolved_files(tmp_path):
    async def scenario():
        app = build_app(tmp_path)
        async with app.run_test(size=(100, 35)) as pilot:
            for label in (PlanLabel.NEW, PlanLabel.SEARCH):
                row = PlanImpactRow(
                    0,
                    PlanItem(
                        label=label, target="missing.js", reason="Needs implementation."
                    ),
                )
                await app.query_one("#session-feed").mount(row)
                await pilot.pause()
                row.set_editable(True)
                assert row.query_one(".plan-impact-question", Button).disabled
                assert (
                    "existing file"
                    in row.query_one(".plan-impact-question", Button).tooltip
                )

    asyncio.run(scenario())
