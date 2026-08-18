from __future__ import annotations

import os
from enum import Enum
from pathlib import Path
from typing import ClassVar

from rapidfuzz import fuzz, process
from rich.markup import escape
from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.widgets import (
    Button,
    Collapsible,
    Footer,
    Header,
    Input,
    LoadingIndicator,
    Markdown,
    OptionList,
    ProgressBar,
    Select,
    Static,
    TextArea,
)
from textual.widgets.option_list import Option
from textual.worker import Worker, WorkerState

from devstill.config import DevstillConfig, update_llm_config
from devstill.credentials import (
    api_key_source,
    credential_id,
    credentials_path,
    delete_api_key,
    store_api_key,
)
from devstill.executor import PreparedExecution
from devstill.models import IndexProgress
from devstill.pi_runtime import inspect_pi
from devstill.planning import PlanItem, PlanLabel, PlanOutput
from devstill.tui_agent import AgentSetupAction, AgentSetupResult, AgentSetupScreen
from devstill.tui_commands import (
    COMMAND_SPECS,
    TuiCommand,
    TuiCommandKind,
    TuiExecutionOffer,
    TuiPreview,
    TuiResult,
    TuiReviewResult,
    TuiWorkflow,
    help_markdown,
    matching_commands,
    parse_tui_command,
    safe_status,
)

MAX_SESSION_ENTRIES = 50
SIDEBAR_BREAKPOINT = 100


class SessionEntry(Vertical):
    def __init__(
        self,
        command: str,
        markdown: str,
        *,
        error: bool = False,
    ) -> None:
        super().__init__(classes="session-entry error" if error else "session-entry")
        self.command = command
        self.markdown = markdown
        self.error = error

    def compose(self) -> ComposeResult:
        marker = "ERROR" if self.error else "COMMAND"
        yield Static(
            f"[b]{marker}[/b]  [dim]{escape(self.command)}[/dim]",
            classes="entry-heading",
        )
        yield Markdown(self.markdown, classes="entry-body")

    def update_markdown(self, markdown: str, *, error: bool = False) -> None:
        self.markdown = markdown
        self.error = error
        self.set_class(error, "error")
        marker = "ERROR" if error else "COMMAND"
        self.query_one(".entry-heading", Static).update(
            f"[b]{marker}[/b]  [dim]{escape(self.command)}[/dim]"
        )
        self.query_one(".entry-body", Markdown).update(markdown)


class ExecutionSessionEntry(SessionEntry):
    def __init__(self, command: str) -> None:
        super().__init__(command, "")
        self.activity: list[str] = []

    def compose(self) -> ComposeResult:
        yield Static(
            f"[b]COMMAND[/b]  [dim]{escape(self.command)}[/dim]",
            classes="entry-heading",
        )
        yield Collapsible(
            Static("", classes="execution-activity-log"),
            title="Pi activity · running",
            collapsed=False,
            classes="execution-activity",
        )
        yield Markdown(self.markdown, classes="entry-body")

    def append_activity(self, message: str) -> None:
        self.activity.append(message)
        rendered = "\n".join(f"• {escape(item)}" for item in self.activity)
        self.query_one(".execution-activity-log", Static).update(rendered)

    def complete_activity(
        self,
        *,
        tool_calls: int | None = None,
        elapsed_seconds: float | None = None,
    ) -> None:
        title = "Pi activity · complete"
        if tool_calls is not None and elapsed_seconds is not None:
            title += f" · {tool_calls} tool calls · {elapsed_seconds:.1f}s"
        panel = self.query_one(Collapsible)
        panel.title = title
        panel.collapsed = True

    def fail_activity(self, message: str) -> None:
        self.append_activity(message)
        panel = self.query_one(Collapsible)
        panel.title = "Pi activity · failed"
        panel.collapsed = False

    def cancel_activity(self) -> None:
        self.append_activity("Pi execution cancelled")
        panel = self.query_one(Collapsible)
        panel.title = "Pi activity · cancelled"
        panel.collapsed = False


class PromptTextArea(TextArea):
    """Compact multiline prompt where Enter submits and Shift+Enter adds a line."""

    BINDINGS: ClassVar = [
        Binding("enter", "submit", show=False, priority=True),
        Binding("shift+enter", "newline", show=False, priority=True),
    ]

    class Submitted(Message):
        def __init__(self, prompt: PromptTextArea) -> None:
            super().__init__()
            self.prompt = prompt

    def action_submit(self) -> None:
        self.post_message(self.Submitted(self))

    def action_newline(self) -> None:
        start, end = self.selection
        self.replace("\n", start, end, maintain_selection_offset=False)


class ComposerMode(str, Enum):
    NORMAL = "normal"
    PI_HANDOFF = "pi_handoff"
    SEARCH_PATH = "search_path"
    PATCH_APPROVAL = "patch_approval"


PLAN_LABEL_OPTIONS = (
    ("🔵 Read", PlanLabel.READ.value),
    ("🟢 Add", PlanLabel.ADD.value),
    ("🟠 Remove", PlanLabel.REMOVE.value),
    ("🟣 Modify", PlanLabel.MODIFY.value),
    ("⚫ Delete", PlanLabel.DELETE.value),
)
SEARCH_LABEL_OPTIONS = (
    ("⚪ Search", PlanLabel.SEARCH.value),
    *PLAN_LABEL_OPTIONS,
)

PLAN_FILE_MATCH_LIMIT = 8
DEFAULT_MANUAL_PLAN_REASON = "Included manually during plan review."


def matching_repository_paths(
    query: str,
    paths: tuple[str, ...] | list[str],
    *,
    limit: int = PLAN_FILE_MATCH_LIMIT,
) -> list[str]:
    """Return path-first substring and fuzzy matches for the Plan file picker."""
    normalized = query.strip().replace("\\", "/").casefold()
    if not normalized or limit < 1:
        return []

    choices = sorted(set(paths))
    substring_matches = [
        path for path in choices if normalized in path.casefold()
    ]
    substring_matches.sort(
        key=lambda path: (
            not Path(path).name.casefold().startswith(normalized),
            not path.casefold().startswith(normalized),
            path.casefold().find(normalized),
            len(path),
            path.casefold(),
        )
    )
    matches = substring_matches[:limit]
    if len(matches) >= limit:
        return matches

    matched = set(matches)
    fuzzy_matches = process.extract(
        normalized,
        [path for path in choices if path not in matched],
        scorer=fuzz.WRatio,
        processor=lambda value: value.replace("\\", "/").casefold(),
        score_cutoff=35,
        limit=limit - len(matches),
    )
    matches.extend(path for path, _score, _index in fuzzy_matches)
    return matches


class PlanImpactRow(Vertical):
    class LabelSelected(Message):
        def __init__(self, row: PlanImpactRow, label: PlanLabel) -> None:
            super().__init__()
            self.row = row
            self.index = row.index
            self.label = label

    class ReasonEdited(Message):
        def __init__(self, row: PlanImpactRow, reason: str) -> None:
            super().__init__()
            self.row = row
            self.index = row.index
            self.reason = reason

    class RemoveRequested(Message):
        def __init__(self, row: PlanImpactRow) -> None:
            super().__init__()
            self.row = row
            self.index = row.index

    # Kept for callers loading older TUI integrations.
    DiscardRequested = RemoveRequested

    class RefreshRequested(Message):
        def __init__(self, row: PlanImpactRow) -> None:
            super().__init__()
            self.row = row
            self.index = row.index

    class BanRequested(Message):
        def __init__(self, row: PlanImpactRow) -> None:
            super().__init__()
            self.row = row
            self.index = row.index

    def __init__(self, index: int, item: PlanItem) -> None:
        super().__init__(classes="plan-impact-row")
        self.index = index
        self.item = item

    @staticmethod
    def _target_text(item: PlanItem) -> str:
        if item.label is PlanLabel.SEARCH:
            return f"— unresolved search target: {escape(item.target)}"
        return f"— {escape(item.target)}"

    def compose(self) -> ComposeResult:
        with Horizontal(classes="plan-impact-heading"):
            if self.item.label is PlanLabel.NEW:
                yield Static("🩷 New", classes="plan-impact-fixed-label")
            else:
                options = (
                    SEARCH_LABEL_OPTIONS
                    if self.item.label is PlanLabel.SEARCH
                    else PLAN_LABEL_OPTIONS
                )
                yield Select(
                    options,
                    allow_blank=False,
                    value=self.item.label.value,
                    classes="plan-impact-label",
                    compact=True,
                )
            yield Static(
                self._target_text(self.item),
                classes="plan-impact-target",
            )
            remove = Button("×", classes="plan-impact-remove", compact=True, flat=True)
            remove.tooltip = "Remove from this handoff without a replacement"
            yield remove
            refresh = Button(
                "↻", classes="plan-impact-refresh", compact=True, flat=True
            )
            refresh.tooltip = "Replace with the next-best eligible file"
            yield refresh
            ban = Button("!", classes="plan-impact-ban", compact=True, flat=True)
            ban.tooltip = "Permanently exclude this file from retrieval"
            yield ban
            yield Button("Ban?", classes="plan-impact-ban-confirm", compact=True)
            yield Button(
                "No", classes="plan-impact-ban-cancel", compact=True, flat=True
            )
        yield Input(
            value=self.item.reason,
            placeholder="Plan reason",
            max_length=240,
            classes="plan-impact-reason",
            compact=True,
        )

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select not in self.query(Select) or not isinstance(event.value, str):
            return
        event.stop()
        self.post_message(self.LabelSelected(self, PlanLabel(event.value)))

    def _post_reason(self, value: str) -> None:
        normalized = value.strip()
        if normalized != self.item.reason:
            self.post_message(self.ReasonEdited(self, normalized))

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input not in self.query(Input):
            return
        event.stop()
        self._post_reason(event.value)

    def on_input_blurred(self, event: Input.Blurred) -> None:
        if event.input not in self.query(Input):
            return
        self._post_reason(event.value)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.has_class("plan-impact-remove"):
            event.stop()
            self.post_message(self.RemoveRequested(self))
        elif event.button.has_class("plan-impact-refresh"):
            event.stop()
            self.post_message(self.RefreshRequested(self))
        elif event.button.has_class("plan-impact-ban"):
            event.stop()
            event.button.display = False
            self.query_one(".plan-impact-ban-confirm", Button).display = True
            self.query_one(".plan-impact-ban-cancel", Button).display = True
        elif event.button.has_class("plan-impact-ban-confirm"):
            event.stop()
            self.post_message(self.BanRequested(self))
        elif event.button.has_class("plan-impact-ban-cancel"):
            event.stop()
            self.query_one(".plan-impact-ban", Button).display = True
            self.query_one(".plan-impact-ban-confirm", Button).display = False
            event.button.display = False

    def update_item(self, item: PlanItem) -> None:
        reason = self.query_one_optional(".plan-impact-reason", Input)
        if reason is None:
            # A mounted row is queryable just before Textual has composed its
            # children. Keep the source item current and let compose use it.
            self.item = item
            return
        if reason.value != item.reason:
            reason.value = item.reason
        if item == self.item:
            select = self.query_one_optional(Select)
            if select is not None and select.value != item.label.value:
                select.value = item.label.value
            self._sync_actions()
            return
        previous = self.item
        self.item = item
        self.query_one(".plan-impact-target", Static).update(self._target_text(item))
        select = self.query_one_optional(Select)
        if select is not None:
            search_mode_changed = (previous.label is PlanLabel.SEARCH) != (
                item.label is PlanLabel.SEARCH
            )
            if search_mode_changed:
                select.set_options(
                    SEARCH_LABEL_OPTIONS
                    if item.label is PlanLabel.SEARCH
                    else PLAN_LABEL_OPTIONS
                )
            if select.value != item.label.value:
                select.value = item.label.value
        self._sync_actions()

    def set_editable(self, editable: bool) -> None:
        select = self.query_one_optional(Select)
        if select is not None:
            select.disabled = not editable
        self.query_one(".plan-impact-reason", Input).disabled = not editable
        for button in self.query(Button):
            button.disabled = not editable

    def on_mount(self) -> None:
        for selector in (".plan-impact-ban-confirm", ".plan-impact-ban-cancel"):
            button = self.query_one_optional(selector, Button)
            if button is not None:
                button.display = False
        self._sync_actions()

    def _sync_actions(self) -> None:
        concrete = self.item.label not in {PlanLabel.NEW, PlanLabel.SEARCH}
        refresh = self.query_one_optional(".plan-impact-refresh", Button)
        ban = self.query_one_optional(".plan-impact-ban", Button)
        if refresh is not None:
            refresh.display = concrete
        if ban is not None:
            ban.display = concrete


class PlanNewFileForm(Vertical):
    class Submitted(Message):
        def __init__(
            self,
            form: PlanNewFileForm,
            target: str,
            reason: str,
            label: PlanLabel = PlanLabel.READ,
        ) -> None:
            super().__init__()
            self.form = form
            self.target = target
            self.reason = reason
            self.label = label

    def __init__(
        self,
        repository_paths: tuple[str, ...] = (),
        *,
        plan_targets: tuple[str, ...] = (),
        classes: str | None = None,
    ) -> None:
        super().__init__(classes=classes)
        self.repository_paths = repository_paths
        self.plan_targets = set(plan_targets)
        self.matches: list[str] = []
        self._selected_value: str | None = None

    def compose(self) -> ComposeResult:
        yield Static(
            "[b]Add another file[/b]  [dim]Type a partial path or press Ctrl+P[/dim]",
            classes="plan-new-file-heading",
        )
        yield Input(
            placeholder="Search repository files (for example: HeaderPanel)",
            max_length=240,
            classes="plan-new-file-path",
            compact=True,
        )
        yield OptionList(classes="plan-file-matches", compact=True)
        yield Select(
            PLAN_LABEL_OPTIONS,
            allow_blank=False,
            value=PlanLabel.READ.value,
            classes="plan-manual-label",
            compact=True,
        )
        yield Input(
            placeholder="Why this file is needed (optional)",
            max_length=240,
            classes="plan-new-file-reason",
            compact=True,
        )
        with Horizontal(classes="plan-new-file-actions"):
            yield Button(
                "Add to plan",
                variant="success",
                classes="plan-new-file-save",
                compact=True,
            )

    def on_mount(self) -> None:
        self.display = False
        self.query_one(".plan-file-matches", OptionList).display = False

    def open(self, *, focus: bool = True) -> None:
        self.display = True
        if focus:
            self.query_one(".plan-new-file-path", Input).focus()

    def close(self) -> None:
        self.reset()
        self.display = False

    def reset(self, *, focus: bool = False) -> None:
        self._selected_value = None
        self.query_one(".plan-new-file-path", Input).value = ""
        self.query_one(".plan-new-file-reason", Input).value = ""
        self.query_one(".plan-manual-label", Select).value = PlanLabel.READ.value
        self._replace_matches([])
        if focus and not self.query_one(".plan-new-file-path", Input).disabled:
            self.query_one(".plan-new-file-path", Input).focus()

    def focus_picker(self) -> None:
        self.open()

    def submit(self) -> None:
        target = self.query_one(".plan-new-file-path", Input).value.strip()
        reason = self.query_one(".plan-new-file-reason", Input).value.strip()
        label = PlanLabel(self.query_one(".plan-manual-label", Select).value)
        self.post_message(self.Submitted(self, target, reason, label))

    def set_editable(self, editable: bool) -> None:
        for control in self.query("Input, Select, Button, OptionList"):
            control.disabled = not editable

    def on_input_changed(self, event: Input.Changed) -> None:
        if not event.input.has_class("plan-new-file-path"):
            return
        if event.value == self._selected_value:
            self._selected_value = None
            self._replace_matches([])
            return
        self._replace_matches(
            matching_repository_paths(
                event.value,
                [
                    path
                    for path in self.repository_paths
                    if path not in self.plan_targets
                ],
            )
        )

    def set_plan_targets(self, targets: list[str]) -> None:
        self.plan_targets = set(targets)
        path_input = self.query_one_optional(".plan-new-file-path", Input)
        if path_input is not None:
            self._replace_matches(
                matching_repository_paths(
                    path_input.value,
                    [
                        path
                        for path in self.repository_paths
                        if path not in self.plan_targets
                    ],
                )
            )

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        if event.input.has_class("plan-new-file-path"):
            matches = self.query_one(".plan-file-matches", OptionList)
            highlighted = matches.highlighted
            if matches.display and highlighted is not None:
                self._select_match(highlighted)
            else:
                self.query_one(".plan-new-file-reason", Input).focus()
        else:
            self.submit()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.has_class("plan-new-file-save"):
            event.stop()
            self.submit()

    def on_option_list_option_selected(
        self,
        event: OptionList.OptionSelected,
    ) -> None:
        if not event.option_list.has_class("plan-file-matches"):
            return
        event.stop()
        highlighted = event.option_list.highlighted
        if highlighted is not None:
            self._select_match(highlighted)

    def on_key(self, event: events.Key) -> None:
        path_input = self.query_one(".plan-new-file-path", Input)
        matches = self.query_one(".plan-file-matches", OptionList)
        if path_input.has_focus and matches.display and event.key in {"up", "down"}:
            matches.focus()
            if event.key == "up":
                matches.action_cursor_up()
            else:
                matches.action_cursor_down()
            event.stop().prevent_default()
        elif matches.has_focus and event.key == "escape":
            path_input.focus()
            event.stop().prevent_default()

    def _replace_matches(self, matches: list[str]) -> None:
        option_list = self.query_one(".plan-file-matches", OptionList)
        self.matches = matches
        option_list.clear_options()
        option_list.add_options(
            Option(escape(path), id=path) for path in self.matches
        )
        option_list.display = bool(self.matches)
        option_list.highlighted = 0 if self.matches else None

    def _select_match(self, index: int) -> None:
        if index < 0 or index >= len(self.matches):
            return
        path_input = self.query_one(".plan-new-file-path", Input)
        self._selected_value = self.matches[index]
        path_input.value = self._selected_value
        self._replace_matches([])
        self.query_one(".plan-new-file-reason", Input).focus()


class PlanSessionEntry(Vertical):
    def __init__(
        self,
        command: str,
        plan: PlanOutput,
        source: str,
        repository_paths: tuple[str, ...] = (),
    ) -> None:
        super().__init__(classes="session-entry plan-session-entry")
        self.command = command
        self.plan = plan
        self.source = source
        self.repository_paths = repository_paths

    @property
    def title(self) -> str:
        return (
            "Devstill Deep Plan"
            if self.source == "deep-plan"
            else "Devstill File-Impact Plan"
        )

    def compose(self) -> ComposeResult:
        yield Static(
            f"[b]COMMAND[/b]  [dim]{escape(self.command)}[/dim]",
            classes="entry-heading",
        )
        with Vertical(classes="plan-impact-body"):
            yield Static(f"[b]{self.title}[/b]", classes="plan-impact-title")
            yield Static(
                f"Query: {escape(self.plan.query)} · "
                f"Repository: {escape(self.plan.repository)} · "
                f"Model: {escape(self.plan.model)}",
                classes="plan-impact-provenance",
            )
            yield Static(
                "  ".join(f"↳ {escape(value)}" for value in self.plan.refinements),
                classes="plan-refinement-history",
            )
            for index, item in enumerate(self.plan.items):
                yield PlanImpactRow(index, item)
            yield PlanNewFileForm(
                self.repository_paths,
                plan_targets=tuple(item.target for item in self.plan.items),
                classes="plan-new-file-form",
            )
            yield Static(
                "[red]×[/red] remove  [blue]↻[/blue] replace  "
                "[red]![/red] permanently exclude",
                classes="plan-impact-legend",
            )
            yield Static("", classes="plan-impact-status")

    def _active_rows(self) -> list[PlanImpactRow]:
        return [row for row in self.query(PlanImpactRow) if row.display]

    def update_plan(self, plan: PlanOutput) -> None:
        self.plan = plan
        self.query_one(PlanNewFileForm).set_plan_targets(
            [item.target for item in plan.items]
        )
        self.query_one(".plan-refinement-history", Static).update(
            "  ".join(f"↳ {escape(value)}" for value in plan.refinements)
        )
        for row, item in zip(self._active_rows(), plan.items, strict=True):
            row.update_item(item)

    async def rebuild_plan(self, plan: PlanOutput) -> None:
        for row in list(self.query(PlanImpactRow)):
            await row.remove()
        body = self.query_one(".plan-impact-body", Vertical)
        anchor = self.query_one(PlanNewFileForm)
        for index, item in enumerate(plan.items):
            row = PlanImpactRow(index, item)
            await body.mount(row, before=anchor)
            row.set_editable(True)
        self.plan = plan
        self.query_one(PlanNewFileForm).set_plan_targets(
            [item.target for item in plan.items]
        )
        self.query_one(".plan-refinement-history", Static).update(
            "  ".join(f"↳ {escape(value)}" for value in plan.refinements)
        )

    async def append_plan_item(self, item: PlanItem, plan: PlanOutput) -> None:
        row = PlanImpactRow(len(plan.items) - 1, item)
        await self.query_one(".plan-impact-body", Vertical).mount(
            row,
            before=self.query_one(PlanNewFileForm),
        )
        self.plan = plan
        self.query_one(PlanNewFileForm).set_plan_targets(
            [item.target for item in plan.items]
        )
        row.set_editable(True)

    def remove_plan_item(self, index: int, plan: PlanOutput) -> None:
        rows = self._active_rows()
        removed = rows[index]
        removed.set_editable(False)
        removed.display = False
        remaining = [row for position, row in enumerate(rows) if position != index]
        self.plan = plan
        self.query_one(PlanNewFileForm).set_plan_targets(
            [item.target for item in plan.items]
        )
        for new_index, (row, item) in enumerate(
            zip(remaining, plan.items, strict=True)
        ):
            row.index = new_index
            row.update_item(item)

    def set_editable(self, editable: bool, *, close_form: bool = True) -> None:
        for row in self._active_rows():
            row.set_editable(editable)
        form = self.query_one(PlanNewFileForm)
        form.set_editable(editable)
        if editable:
            form.open(focus=False)
        elif close_form:
            form.close()

    def close_new_file_form(self) -> None:
        self.query_one(PlanNewFileForm).reset(focus=True)

    def focus_file_picker(self) -> None:
        self.query_one(PlanNewFileForm).focus_picker()

    def set_status(self, message: str, *, error: bool = False) -> None:
        status = self.query_one(".plan-impact-status", Static)
        status.set_class(error, "error")
        status.update(escape(message))


class DevstillApp(App[None]):
    TITLE = "devstill"
    SUB_TITLE = "local repository intelligence"
    CSS = """
    Screen {
        background: $background;
    }

    #workspace-status {
        height: 3;
        padding: 1 2;
        background: $surface;
        color: $text-muted;
        border-bottom: solid $primary 35%;
    }

    #workspace {
        height: 1fr;
    }

    #content {
        width: 1fr;
        height: 1fr;
    }

    #session-feed {
        width: 1fr;
        height: 1fr;
        padding: 1 2;
        scrollbar-gutter: stable;
    }

    #welcome {
        height: auto;
        margin-bottom: 1;
        padding: 1 2;
        border: round $primary 45%;
        background: $surface;
    }

    .session-entry {
        height: auto;
        margin-bottom: 0;
        border-left: thick $accent;
        background: $surface;
    }

    .session-entry.error {
        border-left: thick $error;
    }

    .entry-heading {
        height: 1;
        padding: 0 1;
        color: $accent;
        background: $boost;
    }

    .error .entry-heading {
        color: $error;
    }

    .entry-body {
        height: auto;
        padding: 0 1;
    }

    .execution-activity {
        height: auto;
        padding: 0 1;
        border-top: none;
        background: $surface;
    }

    .execution-activity-log {
        height: auto;
        color: $text-muted;
    }

    .plan-impact-body {
        height: auto;
        padding: 0 1;
    }

    .plan-impact-title {
        height: 1;
        text-style: bold;
    }

    .plan-impact-provenance {
        height: auto;
        color: $text-muted;
    }

    .plan-refinement-history {
        height: auto;
        color: $text-muted;
        padding: 0 1;
    }

    .plan-impact-row {
        height: auto;
    }

    .plan-impact-heading {
        height: auto;
        min-height: 1;
        align-vertical: middle;
    }

    .plan-impact-label {
        width: 18;
    }

    .plan-impact-fixed-label {
        width: 18;
        height: 1;
        padding-left: 1;
        color: $secondary;
    }

    .plan-impact-target {
        width: 1fr;
        height: auto;
        padding-left: 1;
    }

    .plan-impact-reason {
        height: 1;
        padding: 0 1;
        color: $text-muted;
        background: transparent;
    }

    .plan-impact-reason:focus {
        color: black;
        background: white;
        border: none;
        text-style: none;
    }

    .plan-impact-reason:focus > .input--cursor {
        background: black;
        color: white;
    }

    .plan-impact-reason:focus > .input--selection {
        background: $primary;
        color: white;
    }

    .plan-impact-remove, .plan-impact-refresh, .plan-impact-ban,
    .plan-impact-ban-confirm, .plan-impact-ban-cancel {
        width: auto;
        min-width: 3;
        margin-left: 0;
        padding: 0 1;
    }

    .plan-impact-remove, .plan-impact-ban, .plan-impact-ban-confirm {
        color: $error;
    }

    .plan-impact-refresh {
        color: $primary;
    }

    .plan-impact-legend {
        height: 1;
        color: $text-muted;
        padding: 0 1;
    }

    .plan-new-file-form {
        height: auto;
        margin: 0 1;
        padding: 0 1;
        border-left: solid $secondary 60%;
    }

    .plan-new-file-heading {
        height: 1;
        color: $secondary;
    }

    .plan-new-file-path, .plan-new-file-reason, .plan-manual-label {
        height: 1;
        margin-bottom: 1;
    }

    .plan-file-matches {
        display: none;
        height: auto;
        max-height: 8;
        margin-bottom: 1;
        border: round $secondary 60%;
    }

    .plan-new-file-actions {
        height: 1;
        align-horizontal: right;
    }

    .plan-new-file-actions Button {
        width: auto;
        min-width: 8;
        margin-left: 1;
    }

    .plan-impact-status {
        height: auto;
        min-height: 1;
        padding: 0 1;
        color: $warning;
    }

    .plan-impact-status.error {
        color: $error;
    }

    #composer {
        height: auto;
        padding: 0 1 1 1;
        background: $surface;
        border-top: solid $primary 35%;
    }

    #command-palette {
        display: none;
        height: auto;
        max-height: 10;
        margin: 0 1;
        border: round $accent;
    }

    #command-palette.visible {
        display: block;
    }

    #prompt-row {
        height: auto;
        min-height: 1;
        max-height: 6;
        align-vertical: top;
    }

    #prompt-marker {
        width: 3;
        height: 1;
        padding: 0 0 0 1;
        color: $accent;
        text-style: bold;
    }

    #command-input {
        width: 1fr;
        height: 1;
        min-height: 1;
        max-height: 6;
        border: none;
        padding: 0;
        background: transparent;
    }

    #busy {
        display: none;
        width: 5;
        height: 1;
    }

    #activity {
        height: 1;
        padding: 0 1;
        color: $text-muted;
    }

    #composer-actions {
        display: none;
        height: 3;
        padding: 0 1;
        align-horizontal: right;
    }

    #composer-actions Button {
        display: none;
        min-width: 12;
        margin-left: 1;
    }

    #file-progress {
        display: none;
        height: 1;
        padding: 0 1;
        color: $text-muted;
    }

    #command-progress {
        display: none;
        height: 1;
        margin: 0 1;
    }

    #sidebar {
        width: 34;
        height: 1fr;
        padding: 1;
        border-left: solid $primary 35%;
        background: $surface;
    }

    #sidebar-title {
        height: 2;
        padding: 0 1;
        text-style: bold;
        color: $accent;
    }

    #sidebar-commands {
        height: 1fr;
        border: none;
        background: transparent;
    }

    #sidebar-info {
        height: auto;
        min-height: 9;
        padding: 1;
        border: round $primary 40%;
        color: $text-muted;
    }
    """
    BINDINGS: ClassVar = [
        Binding("ctrl+c", "copy_or_quit", "Copy / quit", priority=True),
        Binding("ctrl+q", "quit", "Quit"),
        Binding("ctrl+l", "clear_feed", "Clear"),
        Binding("ctrl+b", "toggle_sidebar", "Sidebar"),
        Binding("ctrl+p", "focus_plan_file_picker", show=False, priority=True),
        Binding("ctrl+r", "refresh", "Refresh"),
    ]

    def __init__(
        self,
        config_path: Path,
        config: DevstillConfig,
        *,
        workflow: TuiWorkflow | None = None,
    ) -> None:
        super().__init__()
        self.config_path = config_path
        self.config = config
        self._workflow_injected = workflow is not None
        self.workflow = workflow or TuiWorkflow(config_path, config)
        self._active_worker: Worker[TuiResult | None] | None = None
        self._review_worker: Worker[TuiReviewResult] | None = None
        self._review_draft_to_clear: str | None = None
        self._active_command: TuiCommand | None = None
        self._busy = False
        self._live_entry: SessionEntry | None = None
        self._history: list[str] = []
        self._history_index: int | None = None
        self._palette_specs = []
        self._sidebar_manual: bool | None = None
        self._composer_mode = ComposerMode.NORMAL
        self._pending_execution: PreparedExecution | None = None
        self._pending_entry: SessionEntry | None = None
        self._handoff_offer: TuiExecutionOffer | None = None
        self._handoff_entry: PlanSessionEntry | SessionEntry | None = None
        self._handoff_instruction_draft = ""
        self._resolving_search_index: int | None = None
        self._resolving_search_label: PlanLabel | None = None
        self._pi_status = inspect_pi()

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Static(id="workspace-status")
        with Horizontal(id="workspace"):
            with Vertical(id="content"):
                with VerticalScroll(id="session-feed"):
                    yield Static(
                        "[b]Devstill is ready.[/b]\n"
                        "Type a question for local hybrid search, or type [b]/[/b] "
                        "for commands. [b]/plan[/b], [b]/deep-plan[/b], "
                        "[b]/execute[/b], and [b]/agent test[/b] use the configured model.",
                        id="welcome",
                    )
                with Vertical(id="composer"):
                    yield OptionList(id="command-palette", compact=True)
                    with Horizontal(id="prompt-row"):
                        yield Static("❯", id="prompt-marker")
                        yield PromptTextArea(
                            placeholder="Search the repository or type / for commands",
                            id="command-input",
                            soft_wrap=True,
                            compact=True,
                        )
                        yield LoadingIndicator(id="busy")
                    with Horizontal(id="composer-actions"):
                        yield Button("Not now", id="cancel-pi-handoff")
                        yield Button(
                            "Preview with Pi",
                            id="start-pi-handoff",
                            variant="success",
                        )
                        yield Button("Cancel", id="cancel-search-path")
                        yield Button(
                            "Use file",
                            id="use-search-path",
                            variant="primary",
                        )
                        yield Button("Cancel", id="cancel-execution")
                        yield Button(
                            "Apply patch",
                            id="apply-execution",
                            variant="warning",
                        )
                    yield Static("", id="file-progress")
                    yield Static("Ready", id="activity")
                    yield ProgressBar(
                        total=100,
                        show_eta=True,
                        id="command-progress",
                    )
            with Vertical(id="sidebar"):
                yield Static("COMMANDS", id="sidebar-title")
                yield OptionList(
                    *[
                        Option(
                            f"[b]{spec.template.rstrip()}[/b]\n[dim]{spec.description}[/dim]",
                            id=spec.template,
                        )
                        for spec in COMMAND_SPECS
                    ],
                    id="sidebar-commands",
                    compact=True,
                )
                yield Static(id="sidebar-info")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#busy", LoadingIndicator).display = False
        self.refresh_repository_status()
        self._apply_sidebar_visibility(self.size.width)
        self.query_one("#command-input", PromptTextArea).focus()
        warm = getattr(self.workflow, "warm", None)
        if callable(warm):
            self._start_warmup(warm)

    def on_unmount(self) -> None:
        if self._pending_execution is not None:
            self._pending_execution.close()
            self._pending_execution = None
        if self._busy:
            return
        close = getattr(self.workflow, "close", None)
        if callable(close):
            close()

    def _show_composer_actions(self, *button_ids: str) -> None:
        actions = self.query_one("#composer-actions", Horizontal)
        visible = set(button_ids)
        actions.display = bool(visible)
        for button in actions.query(Button):
            button.display = button.id in visible

    def _set_composer_mode(self, mode: ComposerMode) -> None:
        self._composer_mode = mode
        prompt = self.query_one("#command-input", PromptTextArea)
        sidebar = self.query_one("#sidebar-commands", OptionList)
        sidebar.disabled = mode is not ComposerMode.NORMAL
        self._hide_palette()
        if mode is ComposerMode.NORMAL:
            prompt.placeholder = "Search the repository or type / for commands"
            prompt.disabled = self._busy
            self._show_composer_actions()
        elif mode is ComposerMode.PI_HANDOFF:
            prompt.placeholder = "Refine this plan, then press Enter"
            prompt.disabled = self._busy
            self._show_composer_actions("cancel-pi-handoff", "start-pi-handoff")
            self._sync_preview_button()
        elif mode is ComposerMode.SEARCH_PATH:
            prompt.placeholder = "Exact repository-relative file path"
            prompt.disabled = self._busy
            self._show_composer_actions("cancel-search-path", "use-search-path")
        else:
            prompt.placeholder = "Review the patch above before applying it"
            prompt.disabled = True
            self._show_composer_actions("cancel-execution", "apply-execution")

    def _refresh_handoff(self) -> None:
        offer = self._handoff_offer
        entry = self._handoff_entry
        if offer is None or entry is None:
            return
        if isinstance(entry, PlanSessionEntry):
            entry.set_editable(True)
            if offer.plan is None or not offer.plan.items:
                entry.set_status(
                    "Pi is blocked because the reviewed plan is empty. Manually add "
                    "a file or submit a refinement."
                )
            elif offer.unresolved_searches:
                count = offer.unresolved_searches
                entry.set_status(
                    f"Pi is blocked by {count} unresolved Search item"
                    f"{'s' if count != 1 else ''}. Click a white Search label "
                    "and choose its concrete impact."
                )
            else:
                entry.set_status(
                    "Review the files, submit follow-up refinements with Enter, "
                    "then explicitly choose Preview with Pi."
                )
        prompt = self.query_one("#command-input", PromptTextArea)
        prompt.text = self._handoff_instruction_draft
        self._set_composer_mode(ComposerMode.PI_HANDOFF)
        self._sync_preview_button()
        if not offer.unresolved_searches:
            prompt.focus()
        elif isinstance(entry, PlanSessionEntry):
            for row in entry.query(PlanImpactRow):
                if not row.display:
                    continue
                if row.item.label is PlanLabel.SEARCH:
                    select = row.query_one_optional(Select)
                    if select is not None:
                        select.focus()
                    break

    def _enter_handoff(
        self,
        offer: TuiExecutionOffer,
        entry: PlanSessionEntry | SessionEntry,
    ) -> None:
        self._handoff_offer = offer
        self._handoff_entry = entry
        self._handoff_instruction_draft = ""
        self._resolving_search_index = None
        self._resolving_search_label = None
        self._refresh_handoff()
        self._scroll_feed_after_layout()

    def _leave_handoff(self, *, activity: str) -> None:
        entry = self._handoff_entry
        if isinstance(entry, PlanSessionEntry):
            entry.set_editable(False)
            entry.set_status("")
        self._handoff_offer = None
        self._handoff_entry = None
        self._handoff_instruction_draft = ""
        self._resolving_search_index = None
        self._resolving_search_label = None
        prompt = self.query_one("#command-input", PromptTextArea)
        prompt.text = ""
        self._set_composer_mode(ComposerMode.NORMAL)
        self.query_one("#activity", Static).update(activity)
        prompt.focus()

    def _begin_search_resolution(self, index: int, label: PlanLabel) -> None:
        offer = self._handoff_offer
        entry = self._handoff_entry
        if (
            offer is None
            or offer.plan is None
            or not isinstance(entry, PlanSessionEntry)
        ):
            return
        self._handoff_instruction_draft = self.query_one(
            "#command-input", PromptTextArea
        ).text
        self._resolving_search_index = index
        self._resolving_search_label = label
        entry.set_status(
            f"Enter the exact existing repository file for Search item {index + 1} "
            f"and label it {label.value}."
        )
        prompt = self.query_one("#command-input", PromptTextArea)
        prompt.text = ""
        self._set_composer_mode(ComposerMode.SEARCH_PATH)
        prompt.focus()

    def _cancel_search_resolution(self) -> None:
        entry = self._handoff_entry
        offer = self._handoff_offer
        if isinstance(entry, PlanSessionEntry) and offer and offer.plan:
            entry.update_plan(offer.plan)
        self._resolving_search_index = None
        self._resolving_search_label = None
        self._refresh_handoff()

    def _persist_reviewed_plan(
        self,
        plan: PlanOutput,
        *,
        update_entry: bool = True,
        pin_target: str | None = None,
        restore_target: str | None = None,
    ) -> bool:
        offer = self._handoff_offer
        entry = self._handoff_entry
        if offer is None or not isinstance(entry, PlanSessionEntry):
            return False
        save_manual_plan = getattr(self.workflow, "save_manual_plan", None)
        if not callable(save_manual_plan):
            entry.set_status(
                "This TUI workflow cannot persist manual Plan labels.", error=True
            )
            return False
        entry.set_editable(False, close_form=False)
        try:
            suppressed = set(offer.suppressed_paths)
            if restore_target is not None:
                suppressed.discard(restore_target)
            pinned = set(offer.pinned_targets)
            if pin_target is not None:
                pinned.add(pin_target)
            try:
                updated_offer, _markdown = save_manual_plan(
                    offer.source,
                    plan,
                    allow_unresolved=True,
                    suppressed_paths=frozenset(suppressed),
                    pinned_targets=frozenset(pinned),
                )
            except TypeError as exc:
                if "unexpected keyword argument" not in str(exc):
                    raise
                updated_offer, _markdown = save_manual_plan(
                    offer.source,
                    plan,
                    allow_unresolved=True,
                )
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            if offer.plan is not None:
                entry.update_plan(offer.plan)
            entry.set_editable(True)
            entry.set_status(str(exc), error=True)
            return False
        self._handoff_offer = updated_offer
        if update_entry:
            entry.update_plan(plan)
        entry.set_editable(True)
        self.query_one("#activity", Static).update("Plan label saved")
        self._sync_preview_button()
        return True

    def _sync_preview_button(self) -> None:
        button = self.query_one_optional("#start-pi-handoff", Button)
        offer = self._handoff_offer
        prompt = self.query_one_optional("#command-input", PromptTextArea)
        if button is None:
            return
        draft = prompt.text.strip() if prompt is not None else ""
        button.disabled = bool(
            self._busy
            or offer is None
            or offer.plan is None
            or not offer.plan.items
            or offer.unresolved_searches
            or draft
        )

    def _start_review_worker(
        self,
        operation: object,
        *,
        activity: str,
        clear_draft: str | None = None,
    ) -> None:
        if self._busy or not callable(operation):
            return
        self._busy = True
        self._review_draft_to_clear = clear_draft
        if isinstance(self._handoff_entry, PlanSessionEntry):
            self._handoff_entry.set_editable(False, close_form=False)
            self._handoff_entry.set_status(activity)
        prompt = self.query_one("#command-input", PromptTextArea)
        prompt.disabled = True
        self.query_one("#busy", LoadingIndicator).display = True
        self.query_one("#cancel-pi-handoff", Button).disabled = True
        self.query_one("#activity", Static).update(activity)
        self._sync_preview_button()
        self._review_worker = self.run_worker(
            operation,
            name="plan-review",
            group="plan-review",
            exit_on_error=False,
            thread=True,
        )

    def _use_search_path(self) -> None:
        offer = self._handoff_offer
        index = self._resolving_search_index
        label = self._resolving_search_label
        if offer is None or offer.plan is None or index is None or label is None:
            return
        target = (
            self.query_one("#command-input", PromptTextArea)
            .text.strip()
            .replace("\\", "/")
        )
        if not target:
            if isinstance(self._handoff_entry, PlanSessionEntry):
                self._handoff_entry.set_status(
                    "Enter an exact repository-relative file path.", error=True
                )
            self.query_one("#command-input", PromptTextArea).focus()
            return
        old_item = offer.plan.items[index]
        items = list(offer.plan.items)
        duplicate = next(
            (
                (position, item)
                for position, item in enumerate(items)
                if position != index
                and item.label is not PlanLabel.SEARCH
                and item.target == target
            ),
            None,
        )
        if duplicate is not None:
            _duplicate_index, existing = duplicate
            del items[index]
            reviewed = offer.plan.model_copy(update={"items": items})
            if not self._persist_reviewed_plan(
                reviewed,
                update_entry=False,
                pin_target=existing.target,
                restore_target=existing.target,
            ):
                self.query_one("#command-input", PromptTextArea).focus()
                return
            if isinstance(self._handoff_entry, PlanSessionEntry):
                self._handoff_entry.remove_plan_item(index, reviewed)
            self._resolving_search_index = None
            self._resolving_search_label = None
            self._refresh_handoff()
            if isinstance(self._handoff_entry, PlanSessionEntry):
                remaining = (
                    self._handoff_offer.unresolved_searches
                    if self._handoff_offer is not None
                    else 0
                )
                suffix = (
                    f" {remaining} Search item"
                    f"{'s remain' if remaining != 1 else ' remains'}."
                    if remaining
                    else " The Plan is ready for Pi."
                )
                self._handoff_entry.set_status(
                    f"Resolved the redundant Search with {existing.target}; "
                    f"the existing {existing.label.value} label was kept.{suffix}"
                )
            return
        items[index] = PlanItem(label=label, target=target, reason=old_item.reason)
        reviewed = offer.plan.model_copy(update={"items": items})
        if not self._persist_reviewed_plan(
            reviewed,
            pin_target=target,
            restore_target=target,
        ):
            self.query_one("#command-input", PromptTextArea).focus()
            return
        self._resolving_search_index = None
        self._resolving_search_label = None
        self._refresh_handoff()

    def on_resize(self, event: events.Resize) -> None:
        self._apply_sidebar_visibility(event.size.width)

    def _apply_sidebar_visibility(self, width: int) -> None:
        sidebar = self.query_one("#sidebar", Vertical)
        if self._sidebar_manual is None:
            sidebar.display = width >= SIDEBAR_BREAKPOINT
        else:
            sidebar.display = self._sidebar_manual

    def refresh_repository_status(self) -> None:
        status = safe_status(self.config_path, self.config)
        if status["index_dirty"]:
            index = "[red]recovery required[/red]"
        elif status["index_ready"]:
            index = "[green]ready[/green]"
        else:
            index = "[yellow]missing[/yellow]"
        model = (
            f"[green]{escape(str(status['model_name']))}[/green]"
            if status["model_ready"]
            else "[yellow]missing[/yellow]"
        )
        agent = (
            f"[green]{escape(str(status['agent_model']))}[/green]"
            if status["agent_enabled"]
            else "[dim]disabled[/dim]"
        )
        pi = (
            f"[green]{escape(str(self._pi_status.pi_version))}[/green]"
            if self._pi_status.ready
            else "[yellow]missing[/yellow]"
        )
        self.query_one("#workspace-status", Static).update(
            f"[b]{escape(str(status['repository']))}[/b]  •  Index {index}  •  "
            f"Embeddings {model}  •  Device "
            f"[cyan]{escape(str(getattr(self.workflow, 'runtime_device', self.config.embedding.device)))}[/cyan]  •  "
            f"Agent {agent}  •  Pi {pi}"
        )
        key_state = str(status["key_source"])
        runtime_state = str(getattr(self.workflow, "runtime_state", "external"))
        runtime_device = str(
            getattr(self.workflow, "runtime_device", self.config.embedding.device)
        )
        runtime_color = {
            "cold": "yellow",
            "loading": "cyan",
            "warm": "green",
        }.get(runtime_state, "dim")
        self.query_one("#sidebar-info", Static).update(
            "[b]DEFAULTS[/b]\n"
            "Search: hybrid × 10\n"
            "Context/plan: 5\n"
            "Deep Plan: 5 → 10\n"
            "Execute: preview → approve\n"
            "Tests: excluded\n\n"
            f"[b]INDEX[/b]\n{escape(str(status['quality']))}\n\n"
            f"[b]ENCODER[/b]\n[{runtime_color}]"
            f"{escape(runtime_state)}[/{runtime_color}] · "
            f"[cyan]{escape(runtime_device)}[/cyan]\n"
            "[dim]Startup warm · ~1.1 GiB[/dim]\n\n"
            f"[b]AGENT KEY[/b]\n{key_state}\n\n"
            "[b]CLIPBOARD[/b]\n"
            "Select + Ctrl+C/right-click: copy\n"
            "Ctrl+Shift+V: paste\n\n"
            "[b]COMPOSER[/b]\n"
            "Enter: send\n"
            "Shift+Enter: new line\n\n"
            "[dim]Ctrl+B sidebar\nCtrl+L clear[/dim]"
        )

    def _copy_selected_text(self) -> bool:
        """Copy the active widget or screen selection when one exists."""
        focused = self.focused
        if isinstance(focused, (Input, TextArea)) and focused.selected_text:
            self.copy_to_clipboard(focused.selected_text)
            self.query_one("#activity", Static).update("Selected text copied")
            return True
        selected = self.screen.get_selected_text()
        if selected:
            self.copy_to_clipboard(selected)
            self.query_one("#activity", Static).update("Selected text copied")
            return True
        return False

    def action_copy_or_quit(self) -> None:
        """Copy selected TUI text, or retain Ctrl+C's interrupt-to-exit behavior."""
        if self._copy_selected_text():
            return
        self.exit()

    def on_mouse_down(self, event: events.MouseDown) -> None:
        """Copy an existing selection when it is right-clicked."""
        if event.button == 3:
            self._copy_selected_text()

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        if event.text_area.id != "command-input":
            return
        prompt = event.text_area
        prompt.styles.height = max(1, min(6, prompt.text.count("\n") + 1))
        if self._composer_mode is ComposerMode.NORMAL:
            self._update_palette(prompt.text)
        elif self._composer_mode is ComposerMode.PI_HANDOFF:
            self._handoff_instruction_draft = prompt.text
            self._hide_palette()
            self._sync_preview_button()
        else:
            self._hide_palette()

    def _update_palette(self, value: str) -> None:
        palette = self.query_one("#command-palette", OptionList)
        self._palette_specs = matching_commands(value)
        palette.clear_options()
        for spec in self._palette_specs:
            palette.add_option(
                Option(
                    f"[b]{spec.template.rstrip()}[/b]  [dim]{spec.description}[/dim]",
                    id=spec.template,
                )
            )
        visible = bool(self._palette_specs)
        palette.set_class(visible, "visible")
        palette.highlighted = 0 if visible else None

    def _hide_palette(self) -> None:
        palette = self.query_one("#command-palette", OptionList)
        palette.remove_class("visible")
        palette.highlighted = None
        self._palette_specs = []

    async def on_prompt_text_area_submitted(
        self,
        event: PromptTextArea.Submitted,
    ) -> None:
        if event.prompt.id != "command-input" or self._busy:
            return
        if self._composer_mode is ComposerMode.PI_HANDOFF:
            draft = event.prompt.text.strip()
            if draft:
                offer = self._handoff_offer
                refine = getattr(self.workflow, "refine_review_plan", None)
                if offer is not None and callable(refine):
                    self._start_review_worker(
                        lambda: refine(offer, draft, self._thread_progress),
                        activity="Refining the live Plan",
                        clear_draft=draft,
                    )
            else:
                self.query_one("#activity", Static).update(
                    "Enter a refinement or click Preview with Pi"
                )
            return
        if self._composer_mode is ComposerMode.SEARCH_PATH:
            self._use_search_path()
            return
        if self._composer_mode is ComposerMode.PATCH_APPROVAL:
            self.query_one("#activity", Static).update(
                "Click Apply patch to approve this change"
            )
            return
        value = event.prompt.text.strip()
        palette = self.query_one("#command-palette", OptionList)
        exact_immediate = any(
            not spec.template.endswith(" ") and spec.template == value
            for spec in self._palette_specs
        )
        if palette.has_class("visible") and not exact_immediate:
            highlighted = palette.highlighted
            if highlighted is not None and highlighted < len(self._palette_specs):
                self._prefill(self._palette_specs[highlighted].template)
                return
        await self.submit_text(value)

    async def submit_text(self, value: str) -> None:
        prompt = self.query_one("#command-input", PromptTextArea)
        if self._busy:
            self.query_one("#activity", Static).update(
                "Wait for the current command to finish"
            )
            return
        if self._composer_mode is not ComposerMode.NORMAL:
            self.query_one("#activity", Static).update(
                "Finish or cancel the active Pi step first"
            )
            return
        self._hide_palette()
        if not value.strip():
            return
        try:
            command = parse_tui_command(value)
        except ValueError as exc:
            await self._append_entry(value, str(exc), error=True)
            prompt.text = ""
            prompt.focus()
            return

        self._remember(value)
        prompt.text = ""
        if command.kind is TuiCommandKind.HELP:
            await self._append_entry(value, help_markdown())
            prompt.focus()
            return
        if command.kind is TuiCommandKind.AGENT_CONFIGURE:
            self.push_screen(
                AgentSetupScreen(
                    self.config.llm,
                    api_key_source(self.config.llm),
                ),
                self._agent_setup_complete,
            )
            return
        if command.kind is TuiCommandKind.CLEAR:
            await self.clear_feed()
            prompt.focus()
            return
        if command.kind is TuiCommandKind.QUIT:
            self.exit()
            return
        self._start_worker(command)

    async def on_plan_impact_row_label_selected(
        self,
        event: PlanImpactRow.LabelSelected,
    ) -> None:
        if (
            self._composer_mode is not ComposerMode.PI_HANDOFF
            or self._handoff_offer is None
            or self._handoff_offer.plan is None
            or self._handoff_entry is None
            or event.row not in self._handoff_entry.query(PlanImpactRow)
        ):
            return
        plan = self._handoff_offer.plan
        select = event.row.query_one_optional(Select)
        if select is None or select.value != event.label.value:
            return
        if event.index < 0 or event.index >= len(plan.items):
            return
        old_item = plan.items[event.index]
        if event.label is old_item.label:
            return
        if old_item.label is PlanLabel.SEARCH:
            self._begin_search_resolution(event.index, event.label)
            return
        if event.label is PlanLabel.SEARCH or old_item.label is PlanLabel.NEW:
            if isinstance(self._handoff_entry, PlanSessionEntry):
                self._handoff_entry.update_plan(plan)
            return
        items = list(plan.items)
        items[event.index] = PlanItem(
            label=event.label,
            target=old_item.target,
            reason=old_item.reason,
        )
        if self._persist_reviewed_plan(
            plan.model_copy(update={"items": items}),
            pin_target=old_item.target,
        ):
            self._refresh_handoff()

    async def on_plan_impact_row_reason_edited(
        self,
        event: PlanImpactRow.ReasonEdited,
    ) -> None:
        if (
            self._composer_mode is not ComposerMode.PI_HANDOFF
            or self._handoff_offer is None
            or self._handoff_offer.plan is None
            or not isinstance(self._handoff_entry, PlanSessionEntry)
            or event.row not in self._handoff_entry.query(PlanImpactRow)
        ):
            return
        plan = self._handoff_offer.plan
        old_item = plan.items[event.index]
        try:
            edited = PlanItem(
                label=old_item.label,
                target=old_item.target,
                reason=event.reason,
            )
        except ValueError as exc:
            self._handoff_entry.update_plan(plan)
            self._handoff_entry.set_status(str(exc), error=True)
            return
        if edited == old_item:
            event.row.update_item(old_item)
            return
        items = list(plan.items)
        items[event.index] = edited
        if self._persist_reviewed_plan(
            plan.model_copy(update={"items": items}),
            pin_target=old_item.target,
        ):
            self._refresh_handoff()

    async def on_plan_new_file_form_submitted(
        self,
        event: PlanNewFileForm.Submitted,
    ) -> None:
        if (
            self._composer_mode is not ComposerMode.PI_HANDOFF
            or self._handoff_offer is None
            or self._handoff_offer.plan is None
            or not isinstance(self._handoff_entry, PlanSessionEntry)
            or event.form not in self._handoff_entry.query(PlanNewFileForm)
        ):
            return
        target = event.target.strip().replace("\\", "/")
        reason = event.reason.strip() or DEFAULT_MANUAL_PLAN_REASON
        repo_root = getattr(self.workflow, "repo_root", self.config_path.parent)
        candidate = repo_root.joinpath(*target.split("/"))
        label = event.label if candidate.is_file() else PlanLabel.NEW
        try:
            item = PlanItem(label=label, target=target, reason=reason)
        except ValueError as exc:
            self._handoff_entry.set_status(str(exc), error=True)
            return
        plan = self._handoff_offer.plan
        reviewed = plan.model_copy(update={"items": [*plan.items, item]})
        if not self._persist_reviewed_plan(
            reviewed,
            update_entry=False,
            pin_target=item.target,
            restore_target=item.target if label is not PlanLabel.NEW else None,
        ):
            return
        await self._handoff_entry.append_plan_item(item, reviewed)
        self._refresh_handoff()
        self._handoff_entry.close_new_file_form()
        self._scroll_feed_after_layout()

    async def on_plan_impact_row_remove_requested(
        self,
        event: PlanImpactRow.RemoveRequested,
    ) -> None:
        if (
            self._composer_mode is not ComposerMode.PI_HANDOFF
            or self._handoff_offer is None
            or self._handoff_offer.plan is None
            or not isinstance(self._handoff_entry, PlanSessionEntry)
            or event.row not in self._handoff_entry.query(PlanImpactRow)
        ):
            return
        remove = getattr(self.workflow, "remove_review_item", None)
        offer = self._handoff_offer
        if callable(remove):
            self._start_review_worker(
                lambda: remove(offer, event.index),
                activity=f"Removing {offer.plan.items[event.index].target}",
            )
            return
        plan = offer.plan
        items = list(plan.items)
        del items[event.index]
        reviewed = plan.model_copy(update={"items": items})
        if self._persist_reviewed_plan(reviewed, update_entry=False):
            self._handoff_entry.remove_plan_item(event.index, reviewed)
            self._refresh_handoff()

    async def on_plan_impact_row_discard_requested(
        self,
        event: PlanImpactRow.DiscardRequested,
    ) -> None:
        await self.on_plan_impact_row_remove_requested(event)

    async def on_plan_impact_row_refresh_requested(
        self,
        event: PlanImpactRow.RefreshRequested,
    ) -> None:
        offer = self._handoff_offer
        replace = getattr(self.workflow, "replace_review_item", None)
        if (
            self._composer_mode is ComposerMode.PI_HANDOFF
            and offer is not None
            and offer.plan is not None
            and callable(replace)
            and isinstance(self._handoff_entry, PlanSessionEntry)
            and event.row in self._handoff_entry.query(PlanImpactRow)
        ):
            self._start_review_worker(
                lambda: replace(offer, event.index, self._thread_progress),
                activity=f"Finding a replacement for {offer.plan.items[event.index].target}",
            )

    async def on_plan_impact_row_ban_requested(
        self,
        event: PlanImpactRow.BanRequested,
    ) -> None:
        offer = self._handoff_offer
        ban = getattr(self.workflow, "permanently_exclude_review_item", None)
        if (
            self._composer_mode is ComposerMode.PI_HANDOFF
            and offer is not None
            and offer.plan is not None
            and callable(ban)
            and isinstance(self._handoff_entry, PlanSessionEntry)
            and event.row in self._handoff_entry.query(PlanImpactRow)
        ):
            self._start_review_worker(
                lambda: ban(offer, event.index, self._thread_progress),
                activity=f"Permanently excluding {offer.plan.items[event.index].target}",
            )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id
        if button_id == "cancel-pi-handoff":
            self._leave_handoff(activity="Plan ready; Pi not started")
        elif button_id == "start-pi-handoff":
            self._start_pi_preview()
        elif button_id == "cancel-search-path":
            self._cancel_search_resolution()
        elif button_id == "use-search-path":
            self._use_search_path()
        elif button_id == "cancel-execution":
            self._cancel_execution_approval()
        elif button_id == "apply-execution":
            self._apply_execution_approval()

    def _start_pi_preview(self) -> None:
        offer = self._handoff_offer
        if (
            offer is None
            or offer.plan is None
            or not offer.plan.items
            or offer.unresolved_searches
            or self._busy
            or self.query_one("#command-input", PromptTextArea).text.strip()
        ):
            return
        source = offer.source
        entry = self._handoff_entry
        if isinstance(entry, PlanSessionEntry):
            entry.set_editable(False)
            entry.set_status("Pi is preparing a patch from this approved Plan.")
        self._handoff_offer = None
        self._handoff_entry = None
        self._handoff_instruction_draft = ""
        self._resolving_search_index = None
        self._resolving_search_label = None
        self.query_one("#command-input", PromptTextArea).text = ""
        self._set_composer_mode(ComposerMode.NORMAL)
        self._start_worker(
            TuiCommand(
                TuiCommandKind.EXECUTE,
                f"/execute {source}",
                source,
                "",
            )
        )

    def _cancel_execution_approval(self) -> None:
        prepared = self._pending_execution
        entry = self._pending_entry
        self._pending_execution = None
        self._pending_entry = None
        if prepared is not None:
            prepared.close()
        if entry is not None:
            entry.update_markdown(
                f"{entry.markdown.rstrip()}\n## Cancelled\n"
                "The patch was not applied; working files are unchanged."
            )
        self.query_one("#command-input", PromptTextArea).text = ""
        self._set_composer_mode(ComposerMode.NORMAL)
        self.query_one("#activity", Static).update("Execution cancelled")
        self.query_one("#command-input", PromptTextArea).focus()

    def _apply_execution_approval(self) -> None:
        prepared = self._pending_execution
        entry = self._pending_entry
        self._pending_execution = None
        self._pending_entry = None
        if prepared is None:
            return
        self._set_composer_mode(ComposerMode.NORMAL)
        self._busy = True
        self._active_command = TuiCommand(
            TuiCommandKind.EXECUTE,
            "/execute apply",
        )
        self._live_entry = entry
        prompt = self.query_one("#command-input", PromptTextArea)
        prompt.disabled = True
        self.query_one("#busy", LoadingIndicator).display = True
        self.query_one("#activity", Static).update("Applying reviewed Pi patch")
        self._active_worker = self.run_worker(
            lambda: self.workflow.apply_execution(
                prepared,
                self._thread_progress,
            ),
            name="execute-apply",
            group="commands",
            exit_on_error=False,
            thread=True,
        )

    async def _agent_setup_complete(
        self,
        result: AgentSetupResult | None,
    ) -> None:
        prompt = self.query_one("#command-input", PromptTextArea)
        if result is None:
            prompt.focus()
            return
        if result.action is AgentSetupAction.FORGET:
            try:
                removed = delete_api_key(self.config.llm)
            except RuntimeError as exc:
                await self._append_entry("/agent configure", str(exc), error=True)
            else:
                message = (
                    "# Saved token removed\n\n"
                    "The environment variable may still provide a token for this session."
                    if removed
                    else "# No saved token\n\nThere was no remembered token to remove."
                )
                await self._append_entry("/agent configure", message)
            self.refresh_repository_status()
            prompt.focus()
            return

        if result.config is None:
            await self._append_entry(
                "/agent configure",
                "Agent setup did not return a configuration.",
                error=True,
            )
            prompt.focus()
            return

        previous = self.config.llm
        try:
            updated = update_llm_config(
                self.config_path,
                self.config,
                **result.config.model_dump(mode="json"),
            )
        except (OSError, ValueError) as exc:
            await self._append_entry("/agent configure", str(exc), error=True)
            prompt.focus()
            return

        self.config = updated
        if not self._workflow_injected:
            self.workflow.update_config(self.config)
        try:
            if result.api_key:
                if result.remember:
                    store_api_key(self.config.llm, result.api_key)
                else:
                    delete_api_key(self.config.llm)
                os.environ[self.config.llm.api_key_env] = result.api_key
            if credential_id(previous) != credential_id(self.config.llm):
                delete_api_key(previous)
        except (OSError, RuntimeError, ValueError) as exc:
            await self._append_entry(
                "/agent configure",
                f"Configuration was saved, but the token could not be updated: {exc}",
                error=True,
            )
            self.refresh_repository_status()
            prompt.focus()
            return

        source = api_key_source(self.config.llm)
        remembered = (
            f" The remembered token file is `{credentials_path()}`."
            if result.api_key and result.remember
            else ""
        )
        await self._append_entry(
            "/agent configure",
            "# Agent configuration saved\n\n"
            f"- Endpoint: `{self.config.llm.base_url}`\n"
            f"- Model: `{self.config.llm.model}`\n"
            f"- Token source: **{source}**\n\n"
            f"The token value is never displayed.{remembered}",
        )
        self.refresh_repository_status()
        prompt.focus()
        if result.action is AgentSetupAction.SAVE_AND_TEST:
            await self.submit_text("/agent test")

    def _start_worker(self, command: TuiCommand) -> None:
        self._busy = True
        self._active_command = command
        self._live_entry = None
        prompt = self.query_one("#command-input", PromptTextArea)
        prompt.disabled = True
        self.query_one("#busy", LoadingIndicator).display = True
        file_progress = self.query_one("#file-progress", Static)
        file_progress.display = command.kind is TuiCommandKind.INDEX
        file_progress.update("FILES · discovering repository inventory")
        progress_bar = self.query_one("#command-progress", ProgressBar)
        progress_bar.display = command.kind is TuiCommandKind.INDEX
        progress_bar.update(total=100, progress=0)
        self.query_one("#activity", Static).update(f"Running {escape(command.raw)}")
        self._active_worker = self.run_worker(
            lambda: self.workflow.execute(command, self._thread_progress),
            name=command.kind.value,
            group="commands",
            exit_on_error=False,
            thread=True,
        )

    def _start_warmup(self, warm: object) -> None:
        self._busy = True
        self._active_command = None
        prompt = self.query_one("#command-input", PromptTextArea)
        prompt.disabled = True
        self.query_one("#busy", LoadingIndicator).display = True
        self.query_one("#activity", Static).update(
            "Opening vector index and warming query encoder (~1.1 GiB RAM)"
        )
        self._active_worker = self.run_worker(
            lambda: warm(self._thread_progress),
            name="startup-warmup",
            group="commands",
            exit_on_error=False,
            thread=True,
        )

    def _thread_progress(self, update: str | IndexProgress | TuiPreview) -> None:
        try:
            if isinstance(update, TuiPreview):
                self.call_from_thread(self._show_preview, update)
            elif (
                isinstance(update, str)
                and self._active_command is not None
                and self._active_command.kind is TuiCommandKind.EXECUTE
                and self._active_command.raw != "/execute apply"
            ):
                self.call_from_thread(self._show_execution_progress, update)
            else:
                self.call_from_thread(self._set_progress, update)
        except RuntimeError:
            # The terminal may close while a non-cancellable CPU worker is finishing.
            pass

    async def _show_execution_progress(self, message: str) -> None:
        command = self._active_command
        if (
            command is None
            or command.kind is not TuiCommandKind.EXECUTE
            or command.raw == "/execute apply"
        ):
            self._set_progress(message)
            return
        if self._live_entry is None:
            self._live_entry = await self._append_execution_entry(command.raw)
        if isinstance(self._live_entry, ExecutionSessionEntry):
            self._live_entry.append_activity(message)
            self._scroll_feed_after_layout()
        self._set_progress(message)

    async def _show_preview(self, preview: TuiPreview) -> None:
        if self._active_command is None or self._active_command.kind not in {
            TuiCommandKind.PLAN,
            TuiCommandKind.DEEP_PLAN,
        }:
            return
        if self._live_entry is None:
            self._live_entry = await self._append_entry(
                self._active_command.raw,
                preview.markdown,
            )
        else:
            self._live_entry.update_markdown(preview.markdown)
        self.query_one("#activity", Static).update(preview.status)
        self._scroll_feed_after_layout()

    def _set_progress(self, update: str | IndexProgress) -> None:
        if isinstance(update, str):
            self.query_one("#activity", Static).update(escape(update))
        else:
            if update.files is not None:
                files = update.files
                file_progress = self.query_one("#file-progress", Static)
                file_progress.display = True
                file_progress.update(
                    "FILES · "
                    f"{files.total} total · "
                    f"{files.checked} checked · "
                    f"{files.changed} changed · "
                    f"{files.unchanged} unchanged · "
                    f"{files.parsed} parsed"
                )
            suffix = ""
            if update.current is not None and update.total is not None:
                suffix = f" ({update.current}/{update.total})"
            message = f"{update.stage.upper()} · {update.message}{suffix}"
            self.query_one("#activity", Static).update(escape(message))

            progress_bar = self.query_one("#command-progress", ProgressBar)
            progress_bar.display = True
            if update.total is not None and update.total > 0:
                progress_bar.update(
                    total=update.total,
                    progress=max(0, min(update.current or 0, update.total)),
                )
            elif update.stage == "complete":
                progress_bar.update(total=1, progress=1)
            else:
                progress_bar.update(total=100, progress=0)
        self.refresh_repository_status()

    async def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        if event.worker is self._review_worker:
            entry = self._handoff_entry
            if event.state is WorkerState.SUCCESS:
                result = event.worker.result
                workflow_config = getattr(self.workflow, "config", None)
                if isinstance(workflow_config, DevstillConfig):
                    self.config = workflow_config
                self._handoff_offer = result.offer
                if isinstance(entry, PlanSessionEntry) and result.offer.plan is not None:
                    await entry.rebuild_plan(result.offer.plan)
                prompt = self.query_one("#command-input", PromptTextArea)
                if (
                    self._review_draft_to_clear is not None
                    and prompt.text.strip() == self._review_draft_to_clear
                ):
                    prompt.text = ""
                    self._handoff_instruction_draft = ""
                self.query_one("#activity", Static).update(result.message)
                self._finish_review_worker()
                self._refresh_handoff()
                if isinstance(entry, PlanSessionEntry):
                    entry.set_status(result.message)
                self._scroll_feed_after_layout()
            elif event.state is WorkerState.ERROR:
                error = event.worker.error or RuntimeError("Plan review failed")
                if isinstance(entry, PlanSessionEntry):
                    entry.set_status(str(error), error=True)
                self.query_one("#activity", Static).update("Plan review failed")
                if "rollback was incomplete" in str(error):
                    self._busy = False
                    self._review_worker = None
                    self._review_draft_to_clear = None
                    self.query_one("#busy", LoadingIndicator).display = False
                    self.query_one("#command-input", PromptTextArea).disabled = True
                    self.query_one("#cancel-pi-handoff", Button).disabled = False
                    if isinstance(entry, PlanSessionEntry):
                        entry.set_editable(False)
                    self._sync_preview_button()
                    self.query_one("#start-pi-handoff", Button).disabled = True
                else:
                    self._finish_review_worker()
            elif event.state is WorkerState.CANCELLED:
                if isinstance(entry, PlanSessionEntry):
                    entry.set_status("Plan review cancelled", error=True)
                self._finish_review_worker()
            return
        if event.worker is not self._active_worker:
            return
        if event.state is WorkerState.SUCCESS:
            result = event.worker.result
            is_pi_preview = bool(
                self._active_command is not None
                and self._active_command.kind is TuiCommandKind.EXECUTE
                and self._active_command.raw != "/execute apply"
            )
            rendered_entry: PlanSessionEntry | SessionEntry | None = None
            if result is not None and self._active_command is not None:
                offer = result.execution_offer
                if offer is not None and offer.plan is not None:
                    if self._live_entry is not None:
                        await self._live_entry.remove()
                    rendered_entry = await self._append_plan_entry(
                        self._active_command.raw,
                        offer,
                    )
                elif self._live_entry is not None:
                    self._live_entry.update_markdown(result.markdown)
                    rendered_entry = self._live_entry
                else:
                    rendered_entry = await self._append_entry(
                        self._active_command.raw,
                        result.markdown,
                    )
            pending = result.prepared_execution if result is not None else None
            offer = result.execution_offer if result is not None else None
            if is_pi_preview and isinstance(rendered_entry, ExecutionSessionEntry):
                if pending is None:
                    rendered_entry.complete_activity()
                else:
                    rendered_entry.complete_activity(
                        tool_calls=pending.result.tool_calls,
                        elapsed_seconds=pending.result.elapsed_seconds,
                    )
            self.refresh_repository_status()
            activity = (
                "Ready · vector index open · query encoder warm"
                if self._active_command is None
                and str(getattr(self.workflow, "runtime_state", "")) == "warm"
                else "Ready"
            )
            self._finish_worker(activity)
            if pending is not None:
                self._pending_execution = pending
                self._pending_entry = (
                    rendered_entry if isinstance(rendered_entry, SessionEntry) else None
                )
                count = len(pending.result.changed_files)
                if self._pending_entry is not None:
                    self._pending_entry.update_markdown(
                        f"{self._pending_entry.markdown.rstrip()}\n"
                        "## Awaiting approval\n"
                        f"The preview changes {count} approved file"
                        f"{'s' if count != 1 else ''}. Devstill will recheck every "
                        "source before applying it."
                    )
                    self._scroll_feed_after_layout()
                self._set_composer_mode(ComposerMode.PATCH_APPROVAL)
                self.query_one("#cancel-execution", Button).focus()
            elif offer is not None:
                if rendered_entry is not None:
                    self._enter_handoff(offer, rendered_entry)
        elif event.state is WorkerState.ERROR:
            error = event.worker.error or RuntimeError("Command failed")
            raw = (
                self._active_command.raw if self._active_command else "startup warm-up"
            )
            if self._live_entry is not None:
                if (
                    isinstance(self._live_entry, ExecutionSessionEntry)
                    and self._active_command is not None
                    and self._active_command.raw != "/execute apply"
                ):
                    self._live_entry.fail_activity(f"Pi execution failed: {error}")
                self._live_entry.update_markdown(
                    f"{self._live_entry.markdown.rstrip()}\n\n"
                    f"## Agent request failed\n\n{error}",
                    error=True,
                )
                self._scroll_feed_after_layout()
            else:
                await self._append_entry(raw, str(error), error=True)
            self.refresh_repository_status()
            activity = (
                "Warm-up failed; the first semantic command will retry"
                if self._active_command is None
                else "Command failed"
            )
            self._finish_worker(activity)
        elif event.state is WorkerState.CANCELLED:
            if self._live_entry is not None:
                if (
                    isinstance(self._live_entry, ExecutionSessionEntry)
                    and self._active_command is not None
                    and self._active_command.raw != "/execute apply"
                ):
                    self._live_entry.cancel_activity()
                self._live_entry.update_markdown(
                    f"{self._live_entry.markdown.rstrip()}\n\n"
                    "## Agent request cancelled",
                    error=True,
                )
            self._finish_worker("Command cancelled")

    def _finish_review_worker(self) -> None:
        self._busy = False
        self._review_worker = None
        self._review_draft_to_clear = None
        self.query_one("#busy", LoadingIndicator).display = False
        self.query_one("#cancel-pi-handoff", Button).disabled = False
        prompt = self.query_one("#command-input", PromptTextArea)
        prompt.disabled = False
        if isinstance(self._handoff_entry, PlanSessionEntry):
            self._handoff_entry.set_editable(True)
        self._sync_preview_button()
        prompt.focus()

    def _finish_worker(self, activity: str) -> None:
        self._busy = False
        self._active_worker = None
        self._active_command = None
        self._live_entry = None
        prompt = self.query_one("#command-input", PromptTextArea)
        self._set_composer_mode(ComposerMode.NORMAL)
        self.query_one("#busy", LoadingIndicator).display = False
        file_progress = self.query_one("#file-progress", Static)
        file_progress.display = False
        file_progress.update("")
        self.query_one("#command-progress", ProgressBar).display = False
        self.query_one("#activity", Static).update(activity)
        prompt.focus()

    def _scroll_feed_after_layout(self) -> None:
        """Keep new or expanding entries visible after Textual recalculates layout."""
        feed = self.query_one("#session-feed", VerticalScroll)
        feed.scroll_end(animate=False, force=True)
        self.call_after_refresh(
            feed.scroll_end,
            animate=False,
            force=True,
        )

    async def _append_entry(
        self,
        command: str,
        markdown: str,
        *,
        error: bool = False,
    ) -> SessionEntry:
        feed = self.query_one("#session-feed", VerticalScroll)
        entries = list(feed.query(".session-entry"))
        while len(entries) >= MAX_SESSION_ENTRIES:
            await entries.pop(0).remove()
        entry = SessionEntry(command, markdown, error=error)
        await feed.mount(entry)
        self._scroll_feed_after_layout()
        return entry

    async def _append_execution_entry(self, command: str) -> ExecutionSessionEntry:
        feed = self.query_one("#session-feed", VerticalScroll)
        entries = list(feed.query(".session-entry"))
        while len(entries) >= MAX_SESSION_ENTRIES:
            await entries.pop(0).remove()
        entry = ExecutionSessionEntry(command)
        await feed.mount(entry)
        self._scroll_feed_after_layout()
        return entry

    async def _append_plan_entry(
        self,
        command: str,
        offer: TuiExecutionOffer,
    ) -> PlanSessionEntry:
        if offer.plan is None:
            raise ValueError("Interactive Plan output requires the validated Plan")
        feed = self.query_one("#session-feed", VerticalScroll)
        entries = list(feed.query(".session-entry"))
        while len(entries) >= MAX_SESSION_ENTRIES:
            await entries.pop(0).remove()
        entry = PlanSessionEntry(
            command,
            offer.plan,
            offer.source,
            offer.repository_paths,
        )
        await feed.mount(entry)
        self._scroll_feed_after_layout()
        return entry

    async def clear_feed(self) -> None:
        if self._composer_mode is not ComposerMode.NORMAL:
            self.query_one("#activity", Static).update(
                "Finish or cancel the active Pi step before clearing the feed"
            )
            return
        feed = self.query_one("#session-feed", VerticalScroll)
        for entry in list(feed.query(".session-entry")):
            await entry.remove()
        self.query_one("#activity", Static).update("Session cleared")

    def _remember(self, value: str) -> None:
        if not self._history or self._history[-1] != value:
            self._history.append(value)
            self._history = self._history[-MAX_SESSION_ENTRIES:]
        self._history_index = None

    def _history_move(self, direction: int) -> None:
        if not self._history:
            return
        if self._history_index is None:
            self._history_index = len(self._history)
        self._history_index = max(
            0,
            min(len(self._history), self._history_index + direction),
        )
        value = (
            ""
            if self._history_index == len(self._history)
            else self._history[self._history_index]
        )
        prompt = self.query_one("#command-input", PromptTextArea)
        prompt.text = value
        prompt.cursor_location = prompt.document.end

    def on_key(self, event: events.Key) -> None:
        prompt = self.query_one("#command-input", PromptTextArea)
        if (
            not self._busy
            and self._composer_mode is not ComposerMode.NORMAL
            and event.key == "escape"
            and not isinstance(self.focused, Select)
        ):
            if self._composer_mode is ComposerMode.SEARCH_PATH:
                self._cancel_search_resolution()
            elif self._composer_mode is ComposerMode.PI_HANDOFF:
                self._leave_handoff(activity="Plan ready; Pi not started")
            elif self._composer_mode is ComposerMode.PATCH_APPROVAL:
                self._cancel_execution_approval()
            event.stop().prevent_default()
            return
        if not prompt.has_focus or self._busy:
            return
        if self._composer_mode is not ComposerMode.NORMAL:
            return
        palette = self.query_one("#command-palette", OptionList)
        if event.key == "escape" and palette.has_class("visible"):
            self._hide_palette()
            event.stop().prevent_default()
            return
        if event.key == "tab" and palette.has_class("visible"):
            highlighted = palette.highlighted
            if highlighted is not None and highlighted < len(self._palette_specs):
                self._prefill(self._palette_specs[highlighted].template)
            event.stop().prevent_default()
            return
        if event.key in {"up", "down"} and palette.has_class("visible"):
            if event.key == "up":
                palette.action_cursor_up()
            else:
                palette.action_cursor_down()
            event.stop().prevent_default()
            return
        single_line = "\n" not in prompt.text
        if event.key == "up" and not palette.has_class("visible") and single_line:
            self._history_move(-1)
            event.stop().prevent_default()
        elif event.key == "down" and not palette.has_class("visible") and single_line:
            self._history_move(1)
            event.stop().prevent_default()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option_id is None:
            return
        if event.option_list.id in {"command-palette", "sidebar-commands"}:
            if self._composer_mode is not ComposerMode.NORMAL:
                self.query_one("#activity", Static).update(
                    "Finish or cancel the active Pi step first"
                )
                return
            self._prefill(event.option_id)

    def _prefill(self, value: str) -> None:
        if self._composer_mode is not ComposerMode.NORMAL:
            return
        prompt = self.query_one("#command-input", PromptTextArea)
        prompt.text = value
        prompt.cursor_location = prompt.document.end
        self._hide_palette()
        prompt.focus()

    async def action_clear_feed(self) -> None:
        await self.clear_feed()
        self.query_one("#command-input", PromptTextArea).focus()

    def action_focus_plan_file_picker(self) -> None:
        if (
            self._composer_mode is ComposerMode.PI_HANDOFF
            and isinstance(self._handoff_entry, PlanSessionEntry)
            and not self._busy
        ):
            self._handoff_entry.focus_file_picker()
            return
        self.query_one("#activity", Static).update(
            "Ctrl+P file search is available while reviewing a Plan"
        )

    def action_toggle_sidebar(self) -> None:
        sidebar = self.query_one("#sidebar", Vertical)
        self._sidebar_manual = not sidebar.display
        sidebar.display = self._sidebar_manual

    def action_refresh(self) -> None:
        self._pi_status = inspect_pi()
        self.refresh_repository_status()
        self.query_one("#activity", Static).update("Repository status refreshed")
