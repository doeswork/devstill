from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Button, Markdown, Static, TextArea
from textual.worker import Worker, WorkerState

from devstill.file_questions import FileQuestionSession


class FileQuestionInput(TextArea):
    BINDINGS: ClassVar = [
        Binding("enter", "submit", show=False, priority=True),
        Binding("shift+enter", "newline", show=False, priority=True),
    ]

    class Submitted(Message):
        pass

    def action_submit(self) -> None:
        self.post_message(self.Submitted())

    def action_newline(self) -> None:
        self.replace("\n", *self.selection, maintain_selection_offset=False)


@dataclass
class FileQuestionReply:
    session: FileQuestionSession | None
    answer: str = ""
    error: str = ""


class FileQuestionScreen(ModalScreen[None]):
    BINDINGS: ClassVar = [Binding("escape", "close", "Close", priority=True)]
    CSS = """
    FileQuestionScreen {
        align: center middle;
        background: $background 70%;
    }
    #file-question-dialog {
        width: 90%;
        max-width: 120;
        height: 90%;
        padding: 1 2;
        border: round $accent;
        background: $surface;
    }
    #file-question-title { height: auto; text-style: bold; color: $accent; }
    #file-question-context { height: auto; color: $text-muted; }
    #file-question-conversation { height: 1fr; margin: 1 0; }
    .file-question-heading { height: auto; text-style: bold; margin-top: 1; }
    #file-question-status, #file-question-queued { height: auto; color: $text-muted; }
    #file-question-status.error { color: $error; }
    #file-question-input { height: 5; border: round $accent; }
    #file-question-actions { height: 3; align-horizontal: right; }
    #file-question-actions Button { min-width: 8; margin-left: 1; }
    """

    def __init__(
        self, target: str, create_session: Callable[[], FileQuestionSession]
    ) -> None:
        super().__init__()
        self.target = target
        self.create_session = create_session
        self.session: FileQuestionSession | None = None
        self._request_worker: Worker | None = None
        self._response: Markdown | None = None
        self._request_question: str | None = None
        self._queued_question: str | None = None
        self._busy = False
        self._questions_closed = False

    def compose(self) -> ComposeResult:
        with Vertical(id="file-question-dialog"):
            yield Static(
                f"Ask about {self.target}", markup=False, id="file-question-title"
            )
            yield Static(
                "Full file + current plan · Reopen to refresh context · Enter sends, Shift+Enter adds a line",
                id="file-question-context",
            )
            yield VerticalScroll(id="file-question-conversation")
            yield Static("", markup=False, id="file-question-status")
            yield Static("", markup=False, id="file-question-queued")
            yield FileQuestionInput(
                placeholder="Ask a question about this file or its role in the plan…",
                soft_wrap=True,
                show_line_numbers=False,
                tab_behavior="focus",
                id="file-question-input",
            )
            with Horizontal(id="file-question-actions"):
                yield Button("Retry", id="file-question-retry")
                yield Button("Send", variant="primary", id="file-question-send")
                yield Button("Close", id="file-question-close")

    async def on_mount(self) -> None:
        self.query_one("#file-question-retry", Button).display = False
        self.query_one(FileQuestionInput).focus()
        await self._start_request(None)

    async def _start_request(
        self, question: str | None, *, retry: bool = False
    ) -> None:
        if self._questions_closed:
            return
        self._busy = True
        self._request_question = question
        self.query_one("#file-question-retry", Button).display = False
        status = self.query_one("#file-question-status", Static)
        status.remove_class("error")
        status.update(
            "Reviewing the full file against your plan…"
            if question is None
            else "Answering your question…"
        )
        transcript = self.query_one("#file-question-conversation", VerticalScroll)
        if not retry:
            heading = "Initial review" if question is None else f"You: {question}"
            await transcript.mount(
                Static(heading, markup=False, classes="file-question-heading")
            )
            self._response = Markdown("Reviewing…" if question is None else "Thinking…")
            await transcript.mount(self._response)
        elif self._response is not None:
            await self._response.update("Retrying…")
        transcript.scroll_end(animate=False)
        self._request_worker = self.run_worker(
            lambda: self._request(question),
            thread=True,
            name="file-question",
            group="file-questions",
            exit_on_error=False,
        )

    def _request(self, question: str | None) -> FileQuestionReply:
        session = self.session
        try:
            if session is None:
                session = self.create_session()
            if self._questions_closed:
                return FileQuestionReply(session)
            return FileQuestionReply(session, answer=session.respond(question))
        except Exception as exc:  # noqa: BLE001 - show request/read failures inside this modal
            return FileQuestionReply(session, error=str(exc))

    async def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        if event.worker is not self._request_worker:
            return
        event.stop()
        if self._questions_closed or event.state not in {WorkerState.SUCCESS, WorkerState.ERROR}:
            return
        result = (
            event.worker.result
            if event.state is WorkerState.SUCCESS
            else FileQuestionReply(self.session, error=str(event.worker.error))
        )
        self.session = result.session
        self._busy = False
        status = self.query_one("#file-question-status", Static)
        if result.error:
            status.add_class("error")
            status.update(result.error)
            if self._response is not None:
                await self._response.update("The request failed. Retry to try again.")
            self.query_one("#file-question-retry", Button).display = True
            return
        if self._response is not None:
            await self._response.update(result.answer)
        status.update("Ready for your question.")
        self.query_one("#file-question-conversation", VerticalScroll).scroll_end(
            animate=False
        )
        if self._queued_question is not None:
            question = self._queued_question
            self._queued_question = None
            self.query_one("#file-question-queued", Static).update("")
            self.query_one("#file-question-send", Button).disabled = False
            await self._start_request(question)

    async def _submit_question(self) -> None:
        editor = self.query_one(FileQuestionInput)
        question = editor.text.strip()
        if not question or self._queued_question is not None:
            return
        editor.text = ""
        if self._busy:
            self._queued_question = question
            self.query_one("#file-question-queued", Static).update(
                f"Queued: {question}"
            )
            self.query_one("#file-question-send", Button).disabled = True
        else:
            await self._start_request(question)
        editor.focus()

    async def on_file_question_input_submitted(
        self, event: FileQuestionInput.Submitted
    ) -> None:
        event.stop()
        await self._submit_question()

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        event.stop()

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.id == "file-question-send":
            await self._submit_question()
        elif event.button.id == "file-question-retry" and not self._busy:
            await self._start_request(self._request_question, retry=True)
        elif event.button.id == "file-question-close":
            self.action_close()

    def _cancel_requests(self) -> None:
        self._questions_closed = True
        self._queued_question = None
        if self._request_worker is not None:
            self._request_worker.cancel()

    def action_close(self) -> None:
        self._cancel_requests()
        self.dismiss(None)

    def on_unmount(self) -> None:
        self._cancel_requests()
