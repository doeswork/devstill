from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import ClassVar

from rich.markup import escape
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Select, Static, Switch

from devstill.config import ExecutorConfig, LlmConfig


class AgentSetupAction(str, Enum):
    SAVE = "save"
    SAVE_AND_TEST = "save_and_test"
    FORGET = "forget"


@dataclass(frozen=True, slots=True)
class AgentSetupResult:
    action: AgentSetupAction
    config: LlmConfig | None = None
    executor: ExecutorConfig | None = None
    api_key: str = ""
    remember: bool = True


class AgentSetupScreen(ModalScreen[AgentSetupResult | None]):
    BINDINGS: ClassVar = [Binding("escape", "cancel", "Cancel")]
    CSS = """
    AgentSetupScreen {
        align: center middle;
        background: $background 70%;
    }

    #agent-dialog {
        width: 78;
        height: 90%;
        min-height: 22;
        max-height: 42;
        padding: 1 2;
        border: round $accent;
        background: $surface;
    }

    #agent-dialog-title {
        height: 2;
        text-style: bold;
        color: $accent;
    }

    #agent-fields {
        height: 1fr;
        padding-right: 1;
        scrollbar-gutter: stable;
    }

    .agent-label {
        height: 1;
        margin-top: 1;
        color: $text-muted;
    }

    #agent-remember-row {
        height: 3;
        align-vertical: middle;
        margin-top: 1;
    }

    #agent-remember-row Label {
        width: 1fr;
    }

    #agent-form-error {
        height: auto;
        min-height: 1;
        margin-top: 1;
        color: $error;
    }

    #agent-buttons {
        height: 3;
        margin-top: 1;
        align-horizontal: right;
    }

    #agent-buttons Button {
        margin-left: 1;
    }
    """

    def __init__(
        self,
        config: LlmConfig,
        executor: ExecutorConfig,
        key_source: str,
    ) -> None:
        super().__init__()
        self.config = config
        self.executor = executor
        self.key_source = key_source

    def compose(self) -> ComposeResult:
        with Vertical(id="agent-dialog"):
            yield Static("Configure OpenAI-compatible agent", id="agent-dialog-title")
            yield Static(
                "The token is never stored in devstill.json. Remembered tokens use "
                "a user-only credential file; environment variables take precedence."
            )
            with VerticalScroll(id="agent-fields"):
                yield Label("Base URL", classes="agent-label")
                yield Input(self.config.base_url, id="agent-base-url")
                yield Label("Model", classes="agent-label")
                yield Input(
                    self.config.model,
                    placeholder="Model identifier",
                    id="agent-model",
                )
                yield Label("API token", classes="agent-label")
                yield Input(
                    "",
                    placeholder="Leave blank to keep the current credential",
                    password=True,
                    id="agent-api-key",
                )
                yield Label("API-key environment variable", classes="agent-label")
                yield Input(self.config.api_key_env, id="agent-api-key-env")
                yield Label("Timeout in seconds", classes="agent-label")
                yield Input(
                    f"{self.config.timeout_seconds:g}",
                    type="number",
                    id="agent-timeout",
                )
                yield Label(
                    "General maximum output tokens",
                    classes="agent-label",
                )
                yield Input(
                    str(self.config.max_output_tokens),
                    type="integer",
                    id="agent-max-tokens",
                )
                yield Label(
                    "Task Review maximum output tokens",
                    classes="agent-label",
                )
                yield Input(
                    str(self.config.planning_max_output_tokens),
                    type="integer",
                    id="agent-planning-max-tokens",
                )
                yield Label("Legacy planning reasoning (tasks always use Off)", classes="agent-label")
                yield Select(
                    (("Off (compatible fallback)", "off"), ("Automatic", "auto")),
                    allow_blank=False,
                    value=self.config.planning_reasoning,
                    id="agent-planning-reasoning",
                )
                yield Label("Legacy Pi execution context window", classes="agent-label")
                yield Input(
                    str(self.executor.context_window),
                    placeholder="auto or token count",
                    id="agent-execution-context",
                )
                yield Label("Task / Pi edit maximum output tokens", classes="agent-label")
                yield Input(
                    str(self.executor.max_output_tokens),
                    type="integer",
                    id="agent-execution-max-tokens",
                )
                yield Label("Legacy Pi execution reasoning", classes="agent-label")
                yield Select(
                    (("Off", "off"), ("Automatic", "auto")),
                    allow_blank=False,
                    value=self.executor.reasoning,
                    id="agent-execution-reasoning",
                )
                with Horizontal(id="agent-remember-row"):
                    yield Label(
                        f"Remember entered token for future sessions "
                        f"(current source: {self.key_source})"
                    )
                    yield Switch(value=True, id="agent-remember")
            yield Static("", id="agent-form-error")
            with Horizontal(id="agent-buttons"):
                yield Button("Forget saved token", id="agent-forget", variant="warning")
                yield Button("Cancel", id="agent-cancel")
                yield Button("Save", id="agent-save", variant="primary")
                yield Button("Save & Test", id="agent-save-test", variant="success")

    def on_mount(self) -> None:
        self.query_one("#agent-base-url", Input).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "agent-cancel":
            self.dismiss(None)
            return
        if event.button.id == "agent-forget":
            self.dismiss(AgentSetupResult(AgentSetupAction.FORGET))
            return
        if event.button.id not in {"agent-save", "agent-save-test"}:
            return
        try:
            config = LlmConfig(
                enabled=True,
                base_url=self.query_one("#agent-base-url", Input).value,
                model=self.query_one("#agent-model", Input).value,
                api_key_env=self.query_one("#agent-api-key-env", Input).value,
                timeout_seconds=float(self.query_one("#agent-timeout", Input).value),
                max_output_tokens=int(self.query_one("#agent-max-tokens", Input).value),
                planning_max_output_tokens=int(
                    self.query_one("#agent-planning-max-tokens", Input).value
                ),
                planning_reasoning=self.query_one(
                    "#agent-planning-reasoning", Select
                ).value,
                context_window=self.config.context_window,
                planning_input_tokens=self.config.planning_input_tokens,
            )
            context_value = self.query_one("#agent-execution-context", Input).value
            context_window: str | int = (
                "auto" if context_value.strip().casefold() == "auto" else int(context_value)
            )
            executor = self.executor.model_copy(
                update={
                    "context_window": context_window,
                    "max_output_tokens": int(
                        self.query_one("#agent-execution-max-tokens", Input).value
                    ),
                    "reasoning": self.query_one(
                        "#agent-execution-reasoning", Select
                    ).value,
                }
            )
            executor = ExecutorConfig.model_validate(executor.model_dump(mode="json"))
        except (TypeError, ValueError) as exc:
            self.query_one("#agent-form-error", Static).update(escape(str(exc)))
            return
        action = (
            AgentSetupAction.SAVE_AND_TEST
            if event.button.id == "agent-save-test"
            else AgentSetupAction.SAVE
        )
        self.dismiss(
            AgentSetupResult(
                action,
                config=config,
                executor=executor,
                api_key=self.query_one("#agent-api-key", Input).value.strip(),
                remember=self.query_one("#agent-remember", Switch).value,
            )
        )

    def action_cancel(self) -> None:
        self.dismiss(None)
