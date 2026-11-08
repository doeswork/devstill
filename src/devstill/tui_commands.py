from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from threading import Event, Lock
from typing import Any

from devstill.agent import AgentClient
from devstill.config import (
    DevstillConfig,
    resolve_index_path,
    resolve_model_path,
    resolve_repo_root,
    update_retrieval_config,
)
from devstill.context import (
    DEEP_PLAN_MAX_CHUNKS,
    DEEP_PLAN_MIN_CHUNKS,
    PLAN_MAX_CHUNKS,
    PLAN_MIN_CHUNKS,
    planning_retrieval_limit,
    query_requests_tests,
    render_context_json,
    render_context_markdown,
    select_adaptive_context_results,
    select_context_results,
    write_context_artifacts,
)
from devstill.credentials import api_key_source
from devstill.deep_planning import run_deep_plan
from devstill.embeddings import EmbeddingBackend
from devstill.executor import (
    PlanSource,
    PreparedExecution,
    RepositoryExecutor,
    render_execution_markdown,
    validate_execution_plan,
    validate_reviewed_plan,
)
from devstill.feature_planning import (
    FeatureBudget,
    FeatureClient,
    FeatureDraft,
    load_feature,
    prepare_feature_draft,
    render_feature_review,
    save_feature,
)
from devstill.feature_runs import render_feature_run, run_feature
from devstill.file_questions import FileQuestionSession, prepare_file_questions
from devstill.git_files import discover_repository_inventory
from devstill.indexer import RepositoryIndexer
from devstill.models import FileQuality, IndexProgress, IndexSummary, SearchResult
from devstill.pi_runtime import resolve_pi_limits
from devstill.planning import (
    PlanLabel,
    PlanOutput,
    classify_replacement,
    generate_plan,
    refine_plan,
    render_plan_json,
    render_plan_markdown,
    select_planning_context,
    write_deep_plan_artifacts,
    write_plan_artifacts,
)
from devstill.quick_tasks import create_quick_draft
from devstill.retrieval_policy import matches_retrieval_exclude
from devstill.search import HybridSearch
from devstill.storage import ChromaStore
from devstill.task_runs import render_task_run, run_tasks
from devstill.tasks import (
    TaskDraft,
    load_task_draft,
    render_task_review,
    save_task_draft,
)


class TuiCommandKind(str, Enum):
    TASK = "task"
    YOLO = "yolo"
    TASK_RUN = "task_run"
    FEATURE = "feature"
    FEATURE_YOLO = "feature_yolo"
    FEATURE_RUN = "feature_run"
    SEARCH = "search"
    CONTEXT = "context"
    PLAN = "plan"
    DEEP_PLAN = "deep_plan"
    EXECUTE = "execute"
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
    CommandSpec("/task ", "Task Review", "Review file impacts before running Quick", TuiCommandKind.TASK),
    CommandSpec("/yolo ", "YOLO", "Retrieve, edit and check without review", TuiCommandKind.YOLO),
    CommandSpec("/feature ", "Feature Review", "Quick first; coordinate when needed", TuiCommandKind.FEATURE),
    CommandSpec("/feature-yolo ", "Feature YOLO", "Quick first; run without review", TuiCommandKind.FEATURE_YOLO),
    CommandSpec(
        "/search ", "Search", "Find relevant code locally", TuiCommandKind.SEARCH
    ),
    CommandSpec(
        "/context ",
        "Context",
        "Build five AI-ready source chunks",
        TuiCommandKind.CONTEXT,
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
    instruction: str = ""


@dataclass(frozen=True, slots=True)
class TuiExecutionOffer:
    source: PlanSource
    query: str
    unresolved_searches: int = 0
    plan: PlanOutput | None = None
    suppressed_paths: frozenset[str] = frozenset()
    pinned_targets: frozenset[str] = frozenset()
    repository_paths: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class TuiReviewResult:
    offer: TuiExecutionOffer
    markdown: str
    message: str


@dataclass(frozen=True, slots=True)
class TuiResult:
    title: str
    markdown: str
    prepared_execution: PreparedExecution | None = None
    execution_offer: TuiExecutionOffer | None = None
    task_review: TaskDraft | FeatureDraft | None = None
    task_state: dict | None = None


@dataclass(frozen=True, slots=True)
class TuiPreview:
    markdown: str
    status: str = "Retrieved files are ready; waiting for the agent"


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
            device=config.embedding.device,
        )
        self._chroma = chroma_store
        self._lock = Lock()

    @property
    def state(self) -> str:
        return str(getattr(self.embedder, "state", "cold"))

    @property
    def device(self) -> str:
        try:
            return str(getattr(self.embedder, "device", self.config.embedding.device))
        except RuntimeError:
            return "unavailable"

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
    if name in {"/task", "/yolo", "/feature", "/feature-yolo", "/search", "/context", "/plan", "/deep-plan"}:
        if not query:
            raise ValueError(f"{name} requires a query.")
        kind = {
            "/task": TuiCommandKind.TASK,
            "/yolo": TuiCommandKind.YOLO,
            "/feature": TuiCommandKind.FEATURE,
            "/feature-yolo": TuiCommandKind.FEATURE_YOLO,
            "/search": TuiCommandKind.SEARCH,
            "/context": TuiCommandKind.CONTEXT,
            "/plan": TuiCommandKind.PLAN,
            "/deep-plan": TuiCommandKind.DEEP_PLAN,
        }[name]
        return TuiCommand(kind, raw, query)
    if name == "/execute":
        source = query.casefold()
        if source not in {"", "plan", "deep-plan"}:
            raise ValueError("/execute accepts `plan` or `deep-plan`.")
        return TuiCommand(TuiCommandKind.EXECUTE, raw, source)
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
            "Plain text uses the selected Task Review, YOLO or Search mode. Task Review is the startup default.",
            "`/task` reviews file impacts before one Run action; `/yolo` edits without review. Both use the configured model and the same scope checks.",
            "Both modes use compact file-impact review followed by the existing bounded Quick executor. No task decomposition is generated.",
            "Feature uses Quick directly when its review is ready; an unresolved review can coordinate multiple tasks. Review/YOLO is a separate choice. `/feature` reviews; `/feature-yolo` runs immediately.",
            "`/search` stays local. Legacy `/plan`, `/deep-plan`, `/execute`, and `/agent test` use the configured model.",
            "Advanced flags, rebuilds, configuration, and removal remain available in the CLI.",
            "",
            "## Clipboard",
            "",
            "Select text and press `Ctrl+C` or right-click to copy it. With no selection, `Ctrl+C` exits.",
            "Use your terminal's `Ctrl+Shift+V` or right-click Paste for external clipboard text.",
            "The TUI composer sends with `Enter` and inserts a line break with `Shift+Enter`.",
            "During Plan review, press `Ctrl+P` to focus the always-visible repository file picker.",
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
    lines = ["# Search", f"Query: {query}"]
    if not results:
        lines.append("No relevant source found.")
        return "\n".join(lines)
    for position, result in enumerate(results, start=1):
        chunk = result.chunk
        symbol = chunk.qualname or chunk.name or chunk.path
        fence = _safe_fence(chunk.code)
        lines.extend(
            [
                (
                    f"## {position}. {chunk.path}:{chunk.start_line}-{chunk.end_line} "
                    f"— {symbol} ({chunk.kind}) — score {result.final_score:.4f}"
                ),
                f"{fence}{chunk.language}",
                chunk.code,
                fence,
            ]
        )
    return "\n".join(lines)


def render_index_markdown(
    summary: IndexSummary,
    quality: list[FileQuality] | None = None,
) -> str:
    lines = [
        "# Index updated",
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
        lines.append(f"## {marker} {status} files ({len(items)})")
        for item in items:
            reasons = "; ".join(item.reasons) or "No reason recorded."
            lines.append(f"- `{item.path}` — {reasons}")
    return "\n".join(lines)


def render_plan_preview_markdown(
    results: list[SearchResult],
    query: str,
    *,
    title: str = "Retrieved plan context",
) -> str:
    grouped: dict[str, list[str]] = {}
    for result in results:
        chunk = result.chunk
        symbol = chunk.qualname or chunk.name
        symbols = grouped.setdefault(chunk.path, [])
        if symbol and symbol not in symbols:
            symbols.append(symbol)
    lines = [
        f"# {title}",
        f"Query: {query}",
        "These files were retrieved locally. Waiting for the agent to classify them.",
    ]
    if not grouped:
        lines.append("No source files were retrieved.")
        return "\n".join(lines)
    for path, symbols in grouped.items():
        line = f"- ⚪ Pending — {path}"
        if symbols:
            rendered = ", ".join(f"`{symbol}`" for symbol in symbols)
            line += f" — Retrieved chunks: {rendered}"
        lines.append(line)
    return "\n".join(lines)


def render_deep_plan_context_markdown(
    initial_plan: PlanOutput,
    initial_results: list[SearchResult],
    expanded_results: list[SearchResult],
) -> str:
    lines = [
        render_plan_markdown(
            initial_plan,
            title="Devstill Deep Plan — Initial Pass",
        ).rstrip(),
        "# Deepdive context",
        (
            f"Expanded from {len(initial_results)} to {len(expanded_results)} "
            "source chunks. Waiting for the agent to refine the complete plan."
        ),
    ]
    initial_paths = {result.chunk.path for result in initial_results}
    grouped: dict[str, list[str]] = {}
    for result in expanded_results:
        if result.chunk.path in initial_paths:
            continue
        symbol = result.chunk.qualname or result.chunk.name
        symbols = grouped.setdefault(result.chunk.path, [])
        if symbol and symbol not in symbols:
            symbols.append(symbol)
    if not grouped:
        lines.append(
            "No additional repository files were discovered; existing evidence expanded."
        )
    for path, symbols in grouped.items():
        line = f"- ⚪ Pending — {path}"
        if symbols:
            line += " — Retrieved chunks: " + ", ".join(
                f"`{symbol}`" for symbol in symbols
            )
        lines.append(line)
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
        self._tasks_cancelled = Event()
        self._tasks_closed = Event()
        self._runtime = TuiRetrievalRuntime(
            config_path,
            config,
            embedder=embedder,
            chroma_store=chroma_store,
        )

    @property
    def runtime_state(self) -> str:
        return self._runtime.state

    @property
    def runtime_device(self) -> str:
        return self._runtime.device

    def _retrieval_key(self, config: DevstillConfig) -> tuple[Any, ...]:
        return (
            resolve_index_path(self.config_path, config),
            config.index.collection,
            resolve_model_path(config),
            config.embedding.max_sequence_length,
            config.embedding.batch_size,
            config.embedding.normalize_embeddings,
            config.embedding.device,
        )

    def update_config(self, config: DevstillConfig) -> None:
        if self._retrieval_key(config) != self._retrieval_key(self.config):
            self._runtime.close()
            self._runtime = TuiRetrievalRuntime(self.config_path, config)
        self.config = config
        self.repo_root = resolve_repo_root(self.config_path, config)
        self.artifact_root = resolve_index_path(self.config_path, config).parent

    def file_question_session(
        self, plan: PlanOutput, index: int
    ) -> FileQuestionSession:
        return prepare_file_questions(
            self.repo_root,
            self.config,
            self.artifact_root,
            plan,
            index,
        )

    def save_manual_plan(
        self,
        source: PlanSource,
        plan: PlanOutput,
        *,
        allow_unresolved: bool = False,
        suppressed_paths: frozenset[str] = frozenset(),
        pinned_targets: frozenset[str] = frozenset(),
    ) -> tuple[TuiExecutionOffer, str]:
        """Validate and persist user-reviewed labels for the selected final plan."""
        if allow_unresolved:
            validate_reviewed_plan(
                plan,
                self.repo_root,
                self.config.retrieval.exclude,
            )
        else:
            validate_execution_plan(
                plan,
                self.repo_root,
                self.config.retrieval.exclude,
            )
        title = (
            "Devstill Deep Plan"
            if source == "deep-plan"
            else "Devstill File-Impact Plan"
        )
        markdown = render_plan_markdown(plan, title=title)
        json_output = render_plan_json(plan)
        if source == "deep-plan":
            write_deep_plan_artifacts(
                self.artifact_root,
                markdown,
                json_output,
            )
        else:
            write_plan_artifacts(self.artifact_root, markdown, json_output)
        unresolved = sum(item.label is PlanLabel.SEARCH for item in plan.items)
        return (
            TuiExecutionOffer(
                source,
                plan.query,
                unresolved,
                plan,
                suppressed_paths,
                pinned_targets,
            ),
            markdown,
        )

    def remove_review_item(
        self,
        offer: TuiExecutionOffer,
        index: int,
    ) -> TuiReviewResult:
        plan = self._offer_plan(offer)
        item = plan.items[index]
        suppressed = set(offer.suppressed_paths)
        if item.label not in {PlanLabel.NEW, PlanLabel.SEARCH}:
            suppressed.add(item.target)
        updated = plan.model_copy(
            update={"items": [*plan.items[:index], *plan.items[index + 1 :]]}
        )
        next_offer, markdown = self.save_manual_plan(
            offer.source,
            updated,
            allow_unresolved=True,
            suppressed_paths=frozenset(suppressed),
            pinned_targets=offer.pinned_targets - {item.target},
        )
        return TuiReviewResult(
            next_offer, markdown, f"Removed {item.target} from this plan"
        )

    def replace_review_item(
        self,
        offer: TuiExecutionOffer,
        index: int,
        report: ProgressCallback | None = None,
    ) -> TuiReviewResult:
        item = self._offer_plan(offer).items[index]
        if item.label in {PlanLabel.NEW, PlanLabel.SEARCH}:
            raise ValueError("Only exact existing files can be replaced")
        removed = self.remove_review_item(offer, index)
        return self._replacement_for_removed(removed, index, report)

    def _replacement_for_removed(
        self,
        removed: TuiReviewResult,
        index: int,
        report: ProgressCallback | None = None,
    ) -> TuiReviewResult:
        notify = report or (lambda _message: None)
        plan = self._offer_plan(removed.offer)
        query = "\n\n".join((plan.query, *plan.refinements))
        notify("Retrieving the next eligible file")
        try:
            engine = self._open_search(notify)
            try:
                candidates = engine.query(query, limit=80)
            finally:
                engine.close()
        except Exception as error:  # noqa: BLE001
            return TuiReviewResult(
                removed.offer,
                removed.markdown,
                f"Removed the file; replacement retrieval failed: {error}",
            )
        blocked = {
            *(item.target for item in plan.items),
            *removed.offer.suppressed_paths,
        }
        eligible = [result for result in candidates if result.chunk.path not in blocked]
        if not eligible:
            return TuiReviewResult(
                removed.offer,
                removed.markdown,
                "Removed the file; no eligible backup was available",
            )
        target = eligible[0].chunk.path
        context = [result for result in eligible if result.chunk.path == target][:3]
        notify(f"Classifying replacement {target}")
        client = AgentClient(
            self.config.llm,
            log_path=self.artifact_root / "logs" / "agent.jsonl",
        )
        try:
            try:
                item = classify_replacement(
                    client,
                    query=query,
                    repository=str(self.repo_root),
                    repo_root=self.repo_root,
                    results=context,
                )
            except Exception as error:  # noqa: BLE001
                return TuiReviewResult(
                    removed.offer,
                    removed.markdown,
                    f"Removed the file; replacement classification failed: {error}",
                )
        finally:
            client.close()
        items = list(plan.items)
        items.insert(min(index, len(items)), item)
        updated = plan.model_copy(update={"items": items})
        next_offer, markdown = self.save_manual_plan(
            removed.offer.source,
            updated,
            allow_unresolved=True,
            suppressed_paths=removed.offer.suppressed_paths,
            pinned_targets=removed.offer.pinned_targets,
        )
        return TuiReviewResult(next_offer, markdown, f"Replaced it with {target}")

    def permanently_exclude_review_item(
        self,
        offer: TuiExecutionOffer,
        index: int,
        report: ProgressCallback | None = None,
    ) -> TuiReviewResult:
        plan = self._offer_plan(offer)
        target = plan.items[index].target
        if plan.items[index].label in {PlanLabel.NEW, PlanLabel.SEARCH}:
            raise ValueError("Only exact existing files can be permanently excluded")
        original_config = self.config
        removed_plan = plan.model_copy(
            update={"items": [*plan.items[:index], *plan.items[index + 1 :]]}
        )
        excluded = [*self.config.retrieval.exclude]
        if target not in excluded:
            excluded.append(target)
        config_updated = False
        plan_write_attempted = False
        try:
            updated_config = update_retrieval_config(
                self.config_path,
                self.config,
                exclude=excluded,
            )
            config_updated = True
            self.update_config(updated_config)
            plan_write_attempted = True
            removed_offer, removed_markdown = self.save_manual_plan(
                offer.source,
                removed_plan,
                allow_unresolved=True,
                suppressed_paths=offer.suppressed_paths | {target},
                pinned_targets=offer.pinned_targets - {target},
            )
        except Exception as error:
            rollback_errors: list[str] = []
            if config_updated:
                try:
                    restored = update_retrieval_config(
                        self.config_path,
                        self.config,
                        exclude=original_config.retrieval.exclude,
                    )
                    self.update_config(restored)
                except Exception as rollback_error:  # noqa: BLE001
                    rollback_errors.append(f"configuration: {rollback_error}")
            if plan_write_attempted:
                try:
                    self.save_manual_plan(
                        offer.source,
                        plan,
                        allow_unresolved=True,
                        suppressed_paths=offer.suppressed_paths,
                        pinned_targets=offer.pinned_targets,
                    )
                except Exception as rollback_error:  # noqa: BLE001
                    rollback_errors.append(f"plan artifact: {rollback_error}")
            if rollback_errors:
                raise RuntimeError(
                    "Permanent exclusion failed and rollback was incomplete. Disable "
                    "this handoff, inspect devstill.json and the plan artifacts, then "
                    f"create a fresh plan. Recovery errors: {'; '.join(rollback_errors)}"
                ) from error
            raise
        removed = TuiReviewResult(
            removed_offer,
            removed_markdown,
            f"Permanently excluded {target}",
        )
        try:
            replacement = self._replacement_for_removed(removed, index, report)
        except Exception:  # noqa: BLE001
            # The permanent removal is already committed; replacement is best-effort.
            return TuiReviewResult(
                removed.offer,
                removed.markdown,
                f"Permanently excluded {target}; no backup was available",
            )
        return replacement

    def refine_review_plan(
        self,
        offer: TuiExecutionOffer,
        refinement: str,
        report: ProgressCallback | None = None,
    ) -> TuiReviewResult:
        notify = report or (lambda _message: None)
        plan = self._offer_plan(offer)
        query = "\n\n".join((plan.query, *plan.refinements, refinement.strip()))
        notify("Retrieving context for the refinement")
        engine = self._open_search(notify)
        try:
            candidates = engine.query(
                query,
                limit=planning_retrieval_limit(
                    DEEP_PLAN_MAX_CHUNKS
                    if offer.source == "deep-plan"
                    else PLAN_MAX_CHUNKS
                ),
            )
        finally:
            engine.close()
        candidates = [
            result
            for result in candidates
            if result.chunk.path not in offer.suppressed_paths
        ]
        maximum = (
            DEEP_PLAN_MAX_CHUNKS if offer.source == "deep-plan" else PLAN_MAX_CHUNKS
        )
        minimum = (
            DEEP_PLAN_MIN_CHUNKS if offer.source == "deep-plan" else PLAN_MIN_CHUNKS
        )
        results = select_adaptive_context_results(
            candidates,
            min_limit=minimum,
            max_limit=maximum,
            include_tests=query_requests_tests(query),
        )
        if not results:
            raise ValueError(
                "No eligible retrieval context is available for refinement"
            )
        notify("Asking the agent to refine the live plan")
        client = AgentClient(
            self.config.llm,
            log_path=self.artifact_root / "logs" / "agent.jsonl",
        )
        try:
            updated = refine_plan(
                client,
                current=plan,
                refinement=refinement,
                repo_root=self.repo_root,
                results=results,
                pinned_targets=set(offer.pinned_targets),
                excluded_targets=set(offer.suppressed_paths),
                retrieval_exclude=self.config.retrieval.exclude,
            )
        finally:
            client.close()
        next_offer, markdown = self.save_manual_plan(
            offer.source,
            updated,
            allow_unresolved=True,
            suppressed_paths=offer.suppressed_paths,
            pinned_targets=offer.pinned_targets,
        )
        return TuiReviewResult(next_offer, markdown, "Plan refinement accepted")

    @staticmethod
    def _offer_plan(offer: TuiExecutionOffer) -> PlanOutput:
        if offer.plan is None:
            raise ValueError("The active handoff has no editable plan")
        return offer.plan

    def close(self) -> None:
        self._tasks_closed.set()
        self.cancel_tasks()
        self._runtime.close()

    def cancel_tasks(self) -> None:
        self._tasks_cancelled.set()

    def tasks_cancelled(self) -> bool:
        return self._tasks_cancelled.is_set() or self._tasks_closed.is_set()

    def reset_task_cancellation(self) -> None:
        if not self._tasks_closed.is_set():
            self._tasks_cancelled.clear()

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
        if (index_root / ".tasks-need-index").exists():
            report("Refreshing local embeddings after task edits")
            self.execute(TuiCommand(TuiCommandKind.INDEX, "/index"), report)
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

    def _plan_picker_paths(
        self,
        engine: HybridSearch,
        results: list[SearchResult],
    ) -> tuple[str, ...]:
        """Collect current indexed paths without rescanning or re-embedding files."""
        paths = {result.chunk.path for result in results}
        list_quality = getattr(getattr(engine, "store", None), "list_quality", None)
        if callable(list_quality):
            paths.update(
                item.path for item in list_quality() if item.disposition == "indexed"
            )
        return tuple(
            sorted(
                path
                for path in paths
                if (self.repo_root / path).is_file()
                and not matches_retrieval_exclude(
                    path,
                    self.config.retrieval.exclude,
                )
            )
        )

    def execute(
        self,
        command: TuiCommand,
        progress: ProgressCallback | None = None,
    ) -> TuiResult:
        report = progress or (lambda _message: None)
        if command.kind in {TuiCommandKind.TASK, TuiCommandKind.YOLO, TuiCommandKind.TASK_RUN,
                            TuiCommandKind.FEATURE, TuiCommandKind.FEATURE_YOLO, TuiCommandKind.FEATURE_RUN} and self._tasks_closed.is_set():
            raise ValueError("Workflow is closed")
        if command.kind in {TuiCommandKind.FEATURE, TuiCommandKind.FEATURE_YOLO}:
            if not self.config.llm.enabled:
                raise ValueError("Agent is disabled. Configure its endpoint and model first.")
            from uuid import uuid4
            budget = FeatureBudget(self.config.feature.timeout_seconds, cancelled=self.tasks_cancelled)
            report("Retrieving compact feature evidence")
            engine = self._open_search(report)
            try:
                candidates = engine.query(command.query, limit=planning_retrieval_limit(PLAN_MAX_CHUNKS))
            finally:
                engine.close()
            budget.remaining()
            log_path = self.artifact_root / "logs" / f"feature-discovery-{uuid4().hex}.jsonl"
            with FeatureClient(self.config.llm, budget=budget, log_path=log_path) as client:
                draft = prepare_feature_draft(client, query=command.query, repo=self.repo_root,
                    candidates=candidates, mode="yolo" if command.kind == TuiCommandKind.FEATURE_YOLO else "review", config=self.config)
            budget.remaining()
            if isinstance(draft, FeatureDraft):
                draft.discovery_seconds = budget.spent
            directory = save_feature(self.artifact_root, draft)
            (directory / "discovery-log.txt").write_text(str(log_path) + "\n")
            if command.kind == TuiCommandKind.FEATURE_YOLO:
                return self.run_feature(draft, report)
            return TuiResult("Feature Review" if isinstance(draft, FeatureDraft) else "Task Review", render_feature_review(draft), task_review=draft)

        if command.kind == TuiCommandKind.FEATURE_RUN:
            return self.run_feature(load_feature(self.artifact_root, command.query), report)

        if command.kind in {TuiCommandKind.TASK, TuiCommandKind.YOLO}:
            if not self.config.llm.enabled:
                raise ValueError("Agent is disabled. Configure its endpoint and model first.")
            report("Retrieving compact task evidence")
            engine = self._open_search(report)
            try:
                candidates = engine.query(command.query, limit=planning_retrieval_limit(PLAN_MAX_CHUNKS))
            finally:
                engine.close()
            from uuid import uuid4
            log_path = self.artifact_root / "logs" / f"task-discovery-{uuid4().hex}.jsonl"
            with AgentClient(self.config.llm, log_path=log_path) as client:
                draft = create_quick_draft(
                    client, query=command.query, repo=self.repo_root,
                    candidates=candidates,
                    mode="yolo" if command.kind is TuiCommandKind.YOLO else "review",
                    excluded=self.config.retrieval.exclude,
                )
            directory = save_task_draft(self.artifact_root, draft)
            (directory / "discovery-log.txt").write_text(str(log_path) + "\n")
            if command.kind is TuiCommandKind.YOLO:
                return self.run_task(draft, report)
            return TuiResult("Task Review", render_task_review(draft), task_review=draft)

        if command.kind is TuiCommandKind.TASK_RUN:
            return self.run_task(load_task_draft(self.artifact_root, command.query), report)

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
            results = select_context_results(
                candidates, 5, include_tests=query_requests_tests(command.query)
            )
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
                candidates = engine.query(
                    command.query,
                    limit=planning_retrieval_limit(PLAN_MAX_CHUNKS),
                )
                repository_paths = self._plan_picker_paths(engine, candidates)
            finally:
                engine.close()
            agent_log = self.artifact_root / "logs" / "agent.jsonl"
            client = AgentClient(self.config.llm, log_path=agent_log)
            try:
                results = select_planning_context(
                    candidates,
                    client=client,
                    query=command.query,
                    repository=str(self.repo_root),
                    min_limit=PLAN_MIN_CHUNKS,
                    max_limit=PLAN_MAX_CHUNKS,
                )
                report(TuiPreview(render_plan_preview_markdown(results, command.query)))
                report(f"Agent response log: {agent_log}")
                report(f"Sending {len(results)} source chunks to {client.endpoint}")
                plan = generate_plan(
                    client,
                    query=command.query,
                    repository=str(self.repo_root),
                    repo_root=self.repo_root,
                    results=results,
                    retrieval_exclude=self.config.retrieval.exclude,
                )
            finally:
                client.close()
            json_output = render_plan_json(plan)
            markdown = render_plan_markdown(plan)
            write_plan_artifacts(self.artifact_root, markdown, json_output)
            return TuiResult(
                "Plan",
                markdown,
                execution_offer=TuiExecutionOffer(
                    "plan",
                    plan.query,
                    sum(item.label is PlanLabel.SEARCH for item in plan.items),
                    plan,
                    repository_paths=repository_paths,
                ),
            )

        if command.kind is TuiCommandKind.DEEP_PLAN:
            if not self.config.llm.enabled:
                raise ValueError(
                    "Agent is disabled. Configure it with `devstill agent configure`."
                )
            report("Retrieving initial Deep Plan context")
            engine = self._open_search(report)
            agent_log = self.artifact_root / "logs" / "agent.jsonl"
            client = AgentClient(self.config.llm, log_path=agent_log)
            try:
                candidates = engine.query(
                    command.query,
                    limit=planning_retrieval_limit(PLAN_MAX_CHUNKS),
                )
                repository_paths = self._plan_picker_paths(engine, candidates)
                initial_results = select_planning_context(
                    candidates,
                    client=client,
                    query=command.query,
                    repository=str(self.repo_root),
                    min_limit=PLAN_MIN_CHUNKS,
                    max_limit=PLAN_MAX_CHUNKS,
                    include_tests=query_requests_tests(command.query),
                )
                report(
                    TuiPreview(
                        render_plan_preview_markdown(
                            initial_results,
                            command.query,
                            title="Retrieved Deep Plan context",
                        ),
                        status="Initial Deep Plan context ready; waiting for the agent",
                    )
                )
                report(f"Agent response log: {agent_log}")
                report(
                    f"Sending {len(initial_results)} initial source chunks to "
                    f"{client.endpoint}"
                )

                def search_reasons(values: list[str]) -> list[list[SearchResult]]:
                    for value in values:
                        report(f"Deepdive search: {value}")
                    options = {"limit": planning_retrieval_limit(DEEP_PLAN_MAX_CHUNKS)}
                    query_many = getattr(engine, "query_many", None)
                    if callable(query_many):
                        return query_many(values, **options)
                    return [engine.query(value, **options) for value in values]

                def initial_ready(initial_plan: PlanOutput) -> None:
                    initial_markdown = render_plan_markdown(
                        initial_plan,
                        title="Devstill Deep Plan — Initial Pass",
                    )
                    write_deep_plan_artifacts(
                        self.artifact_root,
                        initial_markdown,
                        render_plan_json(initial_plan),
                        initial=True,
                    )
                    report(
                        TuiPreview(
                            initial_markdown
                            + "\n\nDeepdive is searching up to three focused reasons for more context.",
                            status="Initial Deep Plan ready; searching its reasons",
                        )
                    )

                def context_ready(
                    initial_plan: PlanOutput,
                    expanded_results: list[SearchResult],
                ) -> None:
                    report(
                        TuiPreview(
                            render_deep_plan_context_markdown(
                                initial_plan,
                                initial_results,
                                expanded_results,
                            ),
                            status=(
                                f"Deepdive found {len(expanded_results)} chunks; "
                                "waiting for the final agent plan"
                            ),
                        )
                    )
                    report(
                        f"Sending {len(expanded_results)} Deep Plan chunks to "
                        f"{client.endpoint}"
                    )

                run = run_deep_plan(
                    client,
                    query=command.query,
                    repository=str(self.repo_root),
                    repo_root=self.repo_root,
                    initial_results=initial_results,
                    search=search_reasons,
                    deep_limit=DEEP_PLAN_MAX_CHUNKS,
                    deep_minimum=DEEP_PLAN_MIN_CHUNKS,
                    include_tests=query_requests_tests(command.query),
                    on_initial=initial_ready,
                    on_context=context_ready,
                    retrieval_exclude=self.config.retrieval.exclude,
                )
            finally:
                client.close()
                engine.close()
            json_output = render_plan_json(run.final_plan)
            markdown = render_plan_markdown(
                run.final_plan,
                title="Devstill Deep Plan",
            )
            write_deep_plan_artifacts(
                self.artifact_root,
                markdown,
                json_output,
            )
            return TuiResult(
                "Deep Plan",
                markdown,
                execution_offer=TuiExecutionOffer(
                    "deep-plan",
                    run.final_plan.query,
                    sum(
                        item.label is PlanLabel.SEARCH for item in run.final_plan.items
                    ),
                    run.final_plan,
                    repository_paths=repository_paths,
                ),
            )

        if command.kind is TuiCommandKind.EXECUTE:
            report("Preparing an isolated Pi execution workspace")
            executor = RepositoryExecutor(
                self.repo_root,
                self.artifact_root,
                self.config,
            )
            source = command.query or None
            prepared = executor.prepare(
                source=source,
                instruction=command.instruction or None,
                progress=report,
            )
            markdown = render_execution_markdown(
                prepared.result,
                prepared.patch,
            )
            return TuiResult("Execution preview", markdown, prepared)

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
                (self.artifact_root / ".tasks-need-index").unlink(missing_ok=True)
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
            limits = resolve_pi_limits(llm, self.config.executor)
            markdown = "\n".join(
                (
                    "# Agent status",
                    "",
                    f"- Enabled: **{llm.enabled}**",
                    f"- Base URL: `{llm.base_url}`",
                    f"- Model: `{llm.model or 'not configured'}`",
                    f"- Timeout: **{llm.timeout_seconds:g} seconds**",
                    f"- General max output tokens: **{llm.max_output_tokens}**",
                    (
                        "- Planning max output tokens: "
                        f"**{llm.planning_max_output_tokens}**"
                    ),
                    f"- Pi requested context: **{self.config.executor.context_window}**",
                    (
                        f"- Pi resolved context: **{limits.context_window}** "
                        f"({limits.source})"
                    ),
                    f"- Pi output cap: **{limits.max_output_tokens}**",
                    f"- Pi reasoning: **{self.config.executor.reasoning}**",
                    "- Pi tools: **strict Plan only**",
                    f"- Planning reasoning: **{llm.planning_reasoning}**",
                    f"- API key environment: `{llm.api_key_env}` (**{key_state}**)",
                    f"- Response log: `{self.artifact_root / 'logs' / 'agent.jsonl'}`",
                    "",
                    "The API key value is never displayed.",
                    *((f"\n> Warning: {limits.warning}",) if limits.warning else ()),
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

    def run_feature(self, draft: FeatureDraft | TaskDraft, progress: ProgressCallback | None = None) -> TuiResult:
        report = progress or (lambda message: None)
        def retrieve(query):
            engine = self._open_search(report)
            try:
                return engine.query(query, limit=planning_retrieval_limit(PLAN_MAX_CHUNKS))
            finally:
                engine.close()
        state = run_feature(draft, repo=self.repo_root, artifact_root=self.artifact_root,
            config=self.config, retrieve=retrieve, progress=report, cancelled=self.tasks_cancelled)
        return TuiResult("Feature result", render_feature_run(state), task_state=state)

    def run_task(self, draft: TaskDraft, progress: ProgressCallback | None = None) -> TuiResult:
        state = run_tasks(
            draft, repo=self.repo_root, artifact_root=self.artifact_root,
            config=self.config, progress=progress, cancelled=self.tasks_cancelled,
        )
        return TuiResult("Task result", render_task_run(state), task_state=state)

    def apply_execution(
        self,
        prepared: PreparedExecution,
        progress: ProgressCallback | None = None,
    ) -> TuiResult:
        report = progress or (lambda _message: None)
        executor = RepositoryExecutor(
            self.repo_root,
            self.artifact_root,
            self.config,
        )
        try:
            result = executor.apply(prepared, progress=report)
            if not self.config.executor.validation_commands:
                report("No validation commands configured; validation was skipped")
            return TuiResult(
                "Execution applied",
                render_execution_markdown(result, prepared.patch),
            )
        finally:
            prepared.close()


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
