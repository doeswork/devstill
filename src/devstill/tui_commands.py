from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from threading import Lock
from typing import Any

from devstill.agent import AgentClient
from devstill.config import (
    DevstillConfig,
    resolve_index_path,
    resolve_model_path,
    resolve_repo_root,
)
from devstill.context import (
    render_context_json,
    render_context_markdown,
    select_context_results,
    write_context_artifacts,
)
from devstill.credentials import api_key_source
from devstill.embeddings import EmbeddingBackend
from devstill.git_files import discover_repository_inventory
from devstill.indexer import RepositoryIndexer
from devstill.models import FileQuality, IndexProgress, IndexSummary, SearchResult
from devstill.planning import (
    generate_plan,
    render_plan_json,
    render_plan_markdown,
    write_plan_artifacts,
)
from devstill.search import HybridSearch
from devstill.storage import ChromaStore


class TuiCommandKind(str, Enum):
    SEARCH = "search"
    CONTEXT = "context"
    PLAN = "plan"
    INDEX = "index"
    FILES = "files"
    AGENT = "agent"
    AGENT_CONFIGURE = "agent_configure"
    AGENT_TEST = "agent_test"
    HELP = "help"
    CLEAR = "clear"
    QUIT = "quit"


@dataclass(frozen=True, slots=True)
class CommandSpec:
    template: str
    title: str
    description: str
    kind: TuiCommandKind


COMMAND_SPECS = (
    CommandSpec(
        "/search ", "Search", "Find relevant code locally", TuiCommandKind.SEARCH
    ),
    CommandSpec(
        "/context ",
        "Context",
        "Build five AI-ready source chunks",
        TuiCommandKind.CONTEXT,
    ),
    CommandSpec(
        "/plan ",
        "Plan",
        "Classify file impact with the configured agent",
        TuiCommandKind.PLAN,
    ),
    CommandSpec("/index", "Index", "Update the repository index", TuiCommandKind.INDEX),
    CommandSpec("/files", "Files", "Inspect repository coverage", TuiCommandKind.FILES),
    CommandSpec("/agent", "Agent", "Show safe agent status", TuiCommandKind.AGENT),
    CommandSpec(
        "/agent configure",
        "Configure agent",
        "Set endpoint, model, and remembered token",
        TuiCommandKind.AGENT_CONFIGURE,
    ),
    CommandSpec(
        "/agent test",
        "Agent test",
        "Test the configured endpoint",
        TuiCommandKind.AGENT_TEST,
    ),
    CommandSpec("/help", "Help", "Show interactive commands", TuiCommandKind.HELP),
    CommandSpec("/clear", "Clear", "Clear this session feed", TuiCommandKind.CLEAR),
    CommandSpec("/quit", "Quit", "Exit Devstill", TuiCommandKind.QUIT),
)


@dataclass(frozen=True, slots=True)
class TuiCommand:
    kind: TuiCommandKind
    raw: str
    query: str = ""


@dataclass(frozen=True, slots=True)
class TuiResult:
    title: str
    markdown: str


@dataclass(frozen=True, slots=True)
class TuiPreview:
    markdown: str


ProgressUpdate = str | IndexProgress | TuiPreview
ProgressCallback = Callable[[ProgressUpdate], None]


class TuiRetrievalRuntime:
    """Process-local semantic dependencies retained for one TUI session."""

    def __init__(
        self,
        config_path: Path,
        config: DevstillConfig,
        *,
        embedder: Any | None = None,
        chroma_store: Any | None = None,
    ) -> None:
        self.config_path = config_path
        self.config = config
        self.embedder = embedder or EmbeddingBackend(
            resolve_model_path(config),
            max_sequence_length=config.embedding.max_sequence_length,
            batch_size=config.embedding.batch_size,
            normalize=config.embedding.normalize_embeddings,
        )
        self._chroma = chroma_store
        self._lock = Lock()

    @property
    def state(self) -> str:
        return str(getattr(self.embedder, "state", "cold"))

    def dependencies(
        self,
        report: ProgressCallback,
        *,
        warm_encoder: bool,
    ) -> tuple[Any, Any]:
        with self._lock:
            if self._chroma is None:
                report("Opening the persistent vector index")
                self._chroma = ChromaStore(
                    resolve_index_path(self.config_path, self.config),
                    self.config.index.collection,
                )
            if warm_encoder and self.state != "warm":
                self.embedder.warm(
                    lambda: report(
                        "Loading the query encoder once for this TUI session "
                        "(about 1.1 GiB RAM)"
                    )
                )
                report("Query encoder is warm")
            elif warm_encoder:
                report("Using the warm query encoder")
            return self.embedder, self._chroma

    def close(self) -> None:
        with self._lock:
            close = getattr(self.embedder, "close", None)
            if callable(close):
                close()
            self._chroma = None


def parse_tui_command(value: str) -> TuiCommand:
    raw = value.strip()
    if not raw:
        raise ValueError("Enter a search query or type /help.")
    if not raw.startswith("/"):
        return TuiCommand(TuiCommandKind.SEARCH, raw, raw)

    command, separator, remainder = raw.partition(" ")
    name = command.casefold()
    query = remainder.strip() if separator else ""
    aliases = {
        "/s": "/search",
        "/c": "/context",
        "/p": "/plan",
        "/?": "/help",
    }
    name = aliases.get(name, name)
    if name in {"/search", "/context", "/plan"}:
        if not query:
            raise ValueError(f"{name} requires a query.")
        kind = {
            "/search": TuiCommandKind.SEARCH,
            "/context": TuiCommandKind.CONTEXT,
            "/plan": TuiCommandKind.PLAN,
        }[name]
        return TuiCommand(kind, raw, query)
    if name == "/agent":
        if not query:
            return TuiCommand(TuiCommandKind.AGENT, raw)
        if query.casefold() == "configure":
            return TuiCommand(TuiCommandKind.AGENT_CONFIGURE, raw)
        if query.casefold() == "test":
            return TuiCommand(TuiCommandKind.AGENT_TEST, raw)
        raise ValueError("/agent accepts `configure` or `test`.")
    simple = {
        "/index": TuiCommandKind.INDEX,
        "/files": TuiCommandKind.FILES,
        "/help": TuiCommandKind.HELP,
        "/clear": TuiCommandKind.CLEAR,
        "/quit": TuiCommandKind.QUIT,
    }
    if name not in simple:
        raise ValueError(
            f"Unknown command: {command}. Type /help for available commands."
        )
    if query:
        raise ValueError(f"{name} does not accept arguments in the TUI.")
    return TuiCommand(simple[name], raw)


def matching_commands(value: str) -> list[CommandSpec]:
    normalized = value.casefold()
    if not normalized.startswith("/"):
        return []
    return [
        spec
        for spec in COMMAND_SPECS
        if spec.template.casefold().startswith(normalized)
    ]


def help_markdown() -> str:
    lines = ["# Devstill commands", ""]
    for spec in COMMAND_SPECS:
        lines.append(f"- `{spec.template.rstrip()}` — {spec.description}")
    lines.extend(
        [
            "",
            "Plain text runs local hybrid search. Only `/plan` and `/agent test` use the network.",
            "Advanced flags, rebuilds, configuration, and removal remain available in the CLI.",
            "",
            "## Clipboard",
            "",
            "Select text and press `Ctrl+C` to copy it. With no selection, `Ctrl+C` exits.",
            "Use your terminal's `Ctrl+Shift+V` or right-click Paste for external clipboard text.",
        ]
    )
    return "\n".join(lines)


def _safe_fence(code: str) -> str:
    longest = max(
        (len(match.group(0)) for match in re.finditer(r"`+", code)),
        default=0,
    )
    return "`" * max(3, longest + 1)


def render_search_markdown(results: list[SearchResult], query: str) -> str:
    lines = ["# Search", "", f"Query: {query}", ""]
    if not results:
        lines.extend(["No relevant source found.", ""])
        return "\n".join(lines)
    for position, result in enumerate(results, start=1):
        chunk = result.chunk
        symbol = chunk.qualname or chunk.name or chunk.path
        fence = _safe_fence(chunk.code)
        lines.extend(
            [
                (
                    f"## {position}. {chunk.path}:{chunk.start_line}-{chunk.end_line} "
                    f"— {symbol} ({chunk.kind})"
                ),
                "",
                f"Score: `{result.final_score:.4f}`",
                "",
                f"{fence}{chunk.language}",
                chunk.code,
                fence,
                "",
            ]
        )
    return "\n".join(lines)


def render_index_markdown(
    summary: IndexSummary,
    quality: list[FileQuality] | None = None,
) -> str:
    lines = [
        "# Index updated",
        "",
        f"- Discovered files: **{summary.discovered_files}**",
        f"- Unchanged files: **{summary.unchanged_files}**",
        f"- Parsed files: **{summary.parsed_files}**",
        f"- Search units embedded: **{summary.search_units_embedded}**",
        f"- Reconciled search units: **{summary.reconciled_search_units}**",
        f"- Quality: **{summary.good_files} GOOD**, **{summary.meh_files} MEH**, **{summary.bad_files} BAD**",
        f"- Parse errors: **{summary.parse_errors}**",
        f"- Index: `{summary.index_path}`",
    ]
    indexed_quality = [
        item
        for item in quality or []
        if item.disposition == "indexed" and item.status in {"MEH", "BAD"}
    ]
    for status, marker in (("MEH", "🟡"), ("BAD", "🔴")):
        items = [item for item in indexed_quality if item.status == status]
        if not items:
            continue
        lines.extend(["", f"## {marker} {status} files ({len(items)})", ""])
        for item in items:
            reasons = "; ".join(item.reasons) or "No reason recorded."
            lines.append(f"- `{item.path}` — {reasons}")
    return "\n".join(lines)


def render_plan_preview_markdown(
    results: list[SearchResult],
    query: str,
) -> str:
    grouped: dict[str, list[str]] = {}
    for result in results:
        chunk = result.chunk
        symbol = chunk.qualname or chunk.name
        symbols = grouped.setdefault(chunk.path, [])
        if symbol and symbol not in symbols:
            symbols.append(symbol)
    lines = [
        "# Retrieved plan context",
        "",
        f"Query: {query}",
        "",
        "These files were retrieved locally. Waiting for the agent to classify them.",
        "",
    ]
    if not grouped:
        lines.extend(["No source files were retrieved.", ""])
        return "\n".join(lines)
    for path, symbols in grouped.items():
        lines.extend([f"## ⚪ Pending — {path}", ""])
        if symbols:
            rendered = ", ".join(f"`{symbol}`" for symbol in symbols)
            lines.extend([f"Retrieved chunks: {rendered}", ""])
    return "\n".join(lines)


class TuiWorkflow:
    def __init__(
        self,
        config_path: Path,
        config: DevstillConfig,
        *,
        embedder: Any | None = None,
        chroma_store: Any | None = None,
    ) -> None:
        self.config_path = config_path
        self.config = config
        self.repo_root = resolve_repo_root(config_path, config)
        self.artifact_root = resolve_index_path(config_path, config).parent
        self._runtime = TuiRetrievalRuntime(
            config_path,
            config,
            embedder=embedder,
            chroma_store=chroma_store,
        )

    @property
    def runtime_state(self) -> str:
        return self._runtime.state

    def _retrieval_key(self, config: DevstillConfig) -> tuple[Any, ...]:
        return (
            resolve_index_path(self.config_path, config),
            config.index.collection,
            resolve_model_path(config),
            config.embedding.max_sequence_length,
            config.embedding.batch_size,
            config.embedding.normalize_embeddings,
        )

    def update_config(self, config: DevstillConfig) -> None:
        if self._retrieval_key(config) != self._retrieval_key(self.config):
            self._runtime.close()
            self._runtime = TuiRetrievalRuntime(self.config_path, config)
        self.config = config
        self.repo_root = resolve_repo_root(self.config_path, config)
        self.artifact_root = resolve_index_path(self.config_path, config).parent

    def close(self) -> None:
        self._runtime.close()

    def warm(self, report: ProgressCallback | None = None) -> None:
        """Open the persistent vector store and load the encoder for this session."""
        notify = report or (lambda _message: None)
        index_root = resolve_index_path(self.config_path, self.config).parent
        if not (index_root / "metadata.sqlite").is_file():
            notify("No repository index yet; run /index to build it")
            return
        if (index_root / ".indexing-dirty").exists():
            notify("Index recovery is required; warming the encoder for /index")
        else:
            notify("Warming retrieval while Devstill starts")
        self._runtime.dependencies(notify, warm_encoder=True)
        notify("Vector index open and query encoder warm")

    def _open_search(self, report: ProgressCallback) -> HybridSearch:
        index_root = resolve_index_path(self.config_path, self.config).parent
        if (index_root / ".indexing-dirty").exists():
            raise RuntimeError(
                f"Devstill index recovery is required at {index_root}. "
                "Run `/index` before searching."
            )
        if not (index_root / "metadata.sqlite").is_file():
            raise FileNotFoundError(
                f"No Devstill index found at {index_root}. Run `devstill index`."
            )
        embedder, chroma = self._runtime.dependencies(report, warm_encoder=True)
        return HybridSearch(
            self.config_path,
            self.config,
            embedder=embedder,
            chroma_store=chroma,
        )

    def execute(
        self,
        command: TuiCommand,
        progress: ProgressCallback | None = None,
    ) -> TuiResult:
        report = progress or (lambda _message: None)
        if command.kind is TuiCommandKind.SEARCH:
            report("Preparing local hybrid search")
            engine = self._open_search(report)
            try:
                results = engine.query(command.query, limit=10)
            finally:
                engine.close()
            return TuiResult("Search", render_search_markdown(results, command.query))

        if command.kind is TuiCommandKind.CONTEXT:
            report("Retrieving AI-ready context")
            engine = self._open_search(report)
            try:
                candidates = engine.query(command.query, limit=32)
            finally:
                engine.close()
            results = select_context_results(candidates, 5, include_tests=False)
            json_output = render_context_json(results)
            markdown = render_context_markdown(
                results,
                command.query,
                str(self.repo_root),
            )
            write_context_artifacts(self.artifact_root, markdown, json_output)
            return TuiResult("Context", markdown)

        if command.kind is TuiCommandKind.PLAN:
            if not self.config.llm.enabled:
                raise ValueError(
                    "Agent is disabled. Configure it with `devstill agent configure`."
                )
            report("Retrieving source before contacting the agent")
            engine = self._open_search(report)
            try:
                candidates = engine.query(command.query, limit=32)
            finally:
                engine.close()
            results = select_context_results(candidates, 5, include_tests=False)
            report(TuiPreview(render_plan_preview_markdown(results, command.query)))
            agent_log = self.artifact_root / "logs" / "agent.jsonl"
            client = AgentClient(self.config.llm, log_path=agent_log)
            try:
                report(f"Agent response log: {agent_log}")
                report(f"Sending {len(results)} source chunks to {client.endpoint}")
                plan = generate_plan(
                    client,
                    query=command.query,
                    repository=str(self.repo_root),
                    repo_root=self.repo_root,
                    results=results,
                )
            finally:
                client.close()
            json_output = render_plan_json(plan)
            markdown = render_plan_markdown(plan)
            write_plan_artifacts(self.artifact_root, markdown, json_output)
            return TuiResult("Plan", markdown)

        if command.kind is TuiCommandKind.INDEX:
            report("Starting incremental index")
            embedder, chroma = self._runtime.dependencies(
                report,
                warm_encoder=False,
            )
            indexer = RepositoryIndexer(
                self.config_path,
                self.config,
                embedder=embedder,
                chroma_store=chroma,
            )

            def index_progress(event: IndexProgress) -> None:
                report(event)

            try:
                summary = indexer.run(progress=index_progress)
                quality = indexer.list_quality()
            finally:
                indexer.close()
            return TuiResult("Index", render_index_markdown(summary, quality))

        if command.kind is TuiCommandKind.FILES:
            report("Inspecting repository files")
            inventory = discover_repository_inventory(self.repo_root, self.config)
            lines = [
                "# Repository files",
                "",
                f"- Eligible: **{len(inventory.eligible)}**",
                f"- Ignored: **{len(inventory.ignored)}**",
                f"- Skipped: **{len(inventory.skipped)}**",
                "",
                "## Eligible",
                "",
                *[f"- `{item.relative}`" for item in inventory.eligible],
            ]
            if inventory.ignored:
                lines.extend(["", "## Ignored", ""])
                lines.extend(
                    f"- `{item.relative}` — {item.decision.reason}"
                    for item in inventory.ignored
                )
            if inventory.skipped:
                lines.extend(["", "## Skipped", ""])
                lines.extend(
                    f"- `{item.relative}` — {item.decision.reason}"
                    for item in inventory.skipped
                )
            return TuiResult("Files", "\n".join(lines))

        if command.kind is TuiCommandKind.AGENT:
            llm = self.config.llm
            key_state = api_key_source(llm)
            markdown = "\n".join(
                (
                    "# Agent status",
                    "",
                    f"- Enabled: **{llm.enabled}**",
                    f"- Base URL: `{llm.base_url}`",
                    f"- Model: `{llm.model or 'not configured'}`",
                    f"- Timeout: **{llm.timeout_seconds:g} seconds**",
                    f"- Max output tokens: **{llm.max_output_tokens}**",
                    f"- API key environment: `{llm.api_key_env}` (**{key_state}**)",
                    f"- Response log: `{self.artifact_root / 'logs' / 'agent.jsonl'}`",
                    "",
                    "The API key value is never displayed.",
                )
            )
            return TuiResult("Agent", markdown)

        if command.kind is TuiCommandKind.AGENT_TEST:
            if not self.config.llm.enabled:
                raise ValueError(
                    "Agent is disabled. Configure it with `devstill agent configure`."
                )
            agent_log = self.artifact_root / "logs" / "agent.jsonl"
            client = AgentClient(self.config.llm, log_path=agent_log)
            try:
                report(f"Agent response log: {agent_log}")
                report(f"Testing {client.endpoint}")
                result = client.test()
            finally:
                client.close()
            markdown = "\n".join(
                (
                    "# Agent test passed",
                    "",
                    f"- Model: `{result.model}`",
                    f"- Endpoint: `{result.endpoint}`",
                    f"- Elapsed: **{result.elapsed_seconds:.3f} seconds**",
                )
            )
            return TuiResult("Agent test", markdown)

        raise ValueError(f"{command.kind.value} is handled by the interface")


def safe_status(config_path: Path, config: DevstillConfig) -> dict[str, str | bool]:
    repo = resolve_repo_root(config_path, config)
    model = resolve_model_path(config)
    index_root = resolve_index_path(config_path, config).parent
    index_dirty = (index_root / ".indexing-dirty").exists()
    manifest_path = index_root / "manifests" / "index.json"
    indexed_at = "never"
    quality = "not indexed"
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if not isinstance(manifest, dict):
                raise TypeError("index manifest must be an object")
            indexed_at = str(manifest.get("indexed_at", "unknown"))
            quality = (
                f"{manifest.get('good_files', 0)} good / "
                f"{manifest.get('meh_files', 0)} meh / "
                f"{manifest.get('bad_files', 0)} bad"
            )
        except (OSError, TypeError, ValueError):
            indexed_at = "manifest unreadable"
    return {
        "repository": repo.name,
        "repository_path": str(repo),
        "index_ready": (index_root / "metadata.sqlite").is_file() and not index_dirty,
        "index_dirty": index_dirty,
        "model_ready": model.is_dir(),
        "model_name": model.name,
        "indexed_at": indexed_at,
        "quality": quality,
        "agent_enabled": config.llm.enabled,
        "agent_model": config.llm.model or "not configured",
        "key_source": api_key_source(config.llm),
    }
