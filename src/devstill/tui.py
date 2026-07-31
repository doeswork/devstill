from __future__ import annotations

from pathlib import Path
from typing import ClassVar

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import Footer, Header, Label, Static

from devstill.config import (
    DevstillConfig,
    resolve_index_path,
    resolve_model_path,
    resolve_repo_root,
)


class DevstillApp(App[None]):
    TITLE = "devstill"
    SUB_TITLE = "local repository intelligence"
    CSS = """
    #status-row { height: auto; padding: 1 2; }
    .card { width: 1fr; min-height: 7; margin: 0 1; padding: 1 2; border: round $accent; }
    #main { height: 1fr; margin: 0 2 1 2; padding: 1 2; border: round $primary; }
    .good { color: $success; }
    .bad { color: $error; }
    .dim { color: $text-muted; }
    """
    BINDINGS: ClassVar = [
        Binding("q", "quit", "Quit"),
        Binding("r", "refresh", "Refresh"),
    ]

    def __init__(self, config_path: Path, config: DevstillConfig) -> None:
        super().__init__()
        self.config_path = config_path
        self.config = config

    def compose(self) -> ComposeResult:
        repo = resolve_repo_root(self.config_path, self.config)
        model = resolve_model_path(self.config)
        index = resolve_index_path(self.config_path, self.config)
        yield Header(show_clock=True)
        with Horizontal(id="status-row"):
            yield Static(self.card("Repository", repo, repo.is_dir()), classes="card")
            yield Static(
                self.card("Embedding model", model, model.is_dir()), classes="card"
            )
            yield Static(
                self.card("Chroma index", index, index.is_dir(), "Not built yet"),
                classes="card",
            )
        with VerticalScroll(id="main"):
            yield Label("[b]devstill is ready.[/b]")
            yield Label(f"Config: {self.config_path}")
            yield Label(f"Collection: {self.config.index.collection}")
            yield Label("")
            yield Label(
                "CLI indexing and hybrid search are available. Search screens and index "
                "controls will be added to this TUI in the next milestone."
            )
        yield Footer()

    @staticmethod
    def card(title: str, value: Path, ready: bool, missing: str = "Missing") -> str:
        status = "[good]Ready[/good]" if ready else f"[bad]{missing}[/bad]"
        return f"[b]{title}[/b]\n{status}\n[dim]{value}[/dim]"

    def action_refresh(self) -> None:
        self.notify("Restart devstill to reload config.")
