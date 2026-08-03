from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from rich.console import Console

from devstill import __version__
from devstill.agent import AgentClient
from devstill.config import (
    CONFIG_FILENAME,
    DevstillConfig,
    find_config,
    load_config,
    resolve_index_path,
    resolve_model_path,
    resolve_repo_root,
    update_llm_config,
    write_default_config,
)
from devstill.context import (
    DEEP_PLAN_MAX_CHUNKS,
    DEEP_PLAN_MIN_CHUNKS,
    PLAN_MAX_CHUNKS,
    PLAN_MIN_CHUNKS,
    planning_retrieval_limit,
    render_context_json,
    render_context_markdown,
    select_adaptive_context_results,
    select_context_results,
    write_context_artifacts,
)
from devstill.credentials import api_key_source
from devstill.deep_planning import run_deep_plan
from devstill.executor import (
    RepositoryExecutor,
    render_execution_markdown,
)
from devstill.git_files import discover_repository_inventory
from devstill.indexer import RepositoryIndexer
from devstill.models import ChunkPolicy
from devstill.parsers import parse_source_file
from devstill.paths import relative_repo_path
from devstill.pi_runtime import inspect_pi
from devstill.planning import (
    LABEL_STYLES,
    generate_plan,
    render_plan_json,
    render_plan_markdown,
    write_deep_plan_artifacts,
    write_plan_artifacts,
)
from devstill.removal import build_removal_plan, execute_removal
from devstill.repository import ensure_devstill_gitignore
from devstill.search import HybridSearch
from devstill.tui import DevstillApp

console = Console()
diagnostic_console = Console(stderr=True)


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser(
        prog="devstill",
        description="Local AI-first repository intelligence.",
    )
    command.add_argument(
        "--version", action="version", version=f"devstill {__version__}"
    )
    sub = command.add_subparsers(dest="command")

    init = sub.add_parser(
        "init", help=f"Create {CONFIG_FILENAME} in the current repository."
    )
    init.add_argument("--force", action="store_true")

    remove = sub.add_parser(
        "remove", help="Remove Devstill data or configuration from this repository."
    )
    remove.add_argument("--index-only", action="store_true")
    remove.add_argument("--dry-run", action="store_true")
    remove.add_argument("--yes", action="store_true")

    doctor = sub.add_parser(
        "doctor", help="Check the installation and current repository."
    )
    doctor.add_argument("--load-model", action="store_true")

    files = sub.add_parser("files", help="List files eligible for indexing.")
    files_output = files.add_mutually_exclusive_group()
    files_output.add_argument("--json", action="store_true")
    files_output.add_argument("--count", action="store_true")
    files.add_argument(
        "--ignored",
        action="store_true",
        help="Show paths excluded by Devstill's built-in content policy.",
    )

    parse = sub.add_parser("parse", help="Parse one configured repository file.")
    parse.add_argument("path")
    parse.add_argument("--json", action="store_true")

    index = sub.add_parser("index", help="Build or update the repository index.")
    index.add_argument("--rebuild", action="store_true")
    index.add_argument("--verbose", action="store_true")
    index.add_argument("--json", action="store_true")

    search = sub.add_parser("search", help="Search the repository index.")
    search.add_argument("query")
    search.add_argument("--limit", type=int, default=10)
    search.add_argument("--json", action="store_true")
    search.add_argument("--kind")
    search.add_argument("--path")
    search_mode = search.add_mutually_exclusive_group()
    search_mode.add_argument("--exact", action="store_true")
    search_mode.add_argument("--semantic-only", action="store_true")
    search_mode.add_argument("--lexical-only", action="store_true")
    search.add_argument("--show-score", action="store_true")

    context = sub.add_parser(
        "context",
        help="Assemble compact source context for a coding agent.",
    )
    context.add_argument("query")
    context.add_argument("--limit", type=int, default=5)
    context.add_argument("--json", action="store_true")
    context.add_argument("--kind")
    context.add_argument("--path")
    context.add_argument("--include-tests", action="store_true")
    context_mode = context.add_mutually_exclusive_group()
    context_mode.add_argument("--exact", action="store_true")
    context_mode.add_argument("--semantic-only", action="store_true")
    context_mode.add_argument("--lexical-only", action="store_true")

    agent = sub.add_parser(
        "agent",
        help="Configure and test the repository's OpenAI-compatible agent.",
    )
    agent_actions = agent.add_subparsers(dest="agent_action")
    configure = agent_actions.add_parser(
        "configure", help="Save agent endpoint settings and enable the agent."
    )
    configure.add_argument("--base-url")
    configure.add_argument("--model")
    configure.add_argument("--api-key-env")
    configure.add_argument("--timeout", type=float)
    configure.add_argument("--max-output-tokens", type=int)
    configure.add_argument("--planning-max-output-tokens", type=int)
    configure.add_argument(
        "--planning-reasoning",
        choices=("off", "auto"),
    )
    agent_actions.add_parser("test", help="Send a small structured agent request.")
    agent_actions.add_parser("enable", help="Enable the configured agent.")
    agent_actions.add_parser("disable", help="Disable the configured agent.")

    plan = sub.add_parser(
        "plan",
        help="Classify retrieved files for an advisory implementation plan.",
    )
    plan.add_argument("query")
    plan.add_argument(
        "--limit",
        type=int,
        default=PLAN_MAX_CHUNKS,
        help="Maximum chunks to send; relevance may stop after 3 (default: 5).",
    )
    plan.add_argument("--json", action="store_true")
    plan.add_argument("--kind")
    plan.add_argument("--path")
    plan.add_argument("--include-tests", action="store_true")
    plan_mode = plan.add_mutually_exclusive_group()
    plan_mode.add_argument("--exact", action="store_true")
    plan_mode.add_argument("--semantic-only", action="store_true")
    plan_mode.add_argument("--lexical-only", action="store_true")

    deep_plan = sub.add_parser(
        "deep-plan",
        help="Refine an advisory file-impact plan through a second retrieval pass.",
    )
    deep_plan.add_argument("query")
    deep_plan.add_argument(
        "--initial-limit",
        type=int,
        default=PLAN_MAX_CHUNKS,
        help="Maximum initial chunks; relevance may stop after 3 (default: 5).",
    )
    deep_plan.add_argument(
        "--limit",
        type=int,
        default=DEEP_PLAN_MAX_CHUNKS,
        help="Maximum expanded chunks; at least 5 are preferred (default: 8).",
    )
    deep_plan.add_argument("--json", action="store_true")
    deep_plan.add_argument("--kind")
    deep_plan.add_argument("--path")
    deep_plan.add_argument("--include-tests", action="store_true")
    deep_plan_mode = deep_plan.add_mutually_exclusive_group()
    deep_plan_mode.add_argument("--exact", action="store_true")
    deep_plan_mode.add_argument("--semantic-only", action="store_true")
    deep_plan_mode.add_argument("--lexical-only", action="store_true")

    execute = sub.add_parser(
        "execute",
        help="Generate, review, and apply an approved Plan through Pi.",
    )
    execute.add_argument(
        "--from",
        dest="source",
        choices=("plan", "deep-plan"),
        help="Select the Plan artifact; defaults to the newest successful plan.",
    )
    execute.add_argument("--dry-run", action="store_true")
    execute.add_argument("--yes", action="store_true")
    execute.add_argument("--json", action="store_true")
    return command


def init_repo(force: bool) -> int:
    try:
        path = write_default_config(Path.cwd() / CONFIG_FILENAME, force)
    except FileExistsError as exc:
        console.print(f"[red]{exc}[/red]")
        return 1
    ensure_devstill_gitignore(Path.cwd() / ".gitignore")
    (path.parent / ".devstill").mkdir(exist_ok=True)
    console.print(f"[green]Created {path}[/green]")
    console.print("Run [bold]devstill index[/bold] to build the repository index.")
    return 0


def remove_repo(index_only: bool, dry_run: bool, assume_yes: bool) -> int:
    config_path, config = _configured()
    plan = build_removal_plan(config_path, config, index_only=index_only)
    scope = "index data only" if index_only else "repository integration"
    console.print(f"Remove Devstill {scope} from [bold]{plan.repo_root}[/bold]")
    console.print("Index targets:")
    for target in plan.index_targets:
        console.print(f"  [cyan]{target}[/cyan]")
    if not index_only:
        console.print(f"Configuration: [cyan]{plan.config_path}[/cyan]")
        if plan.managed_gitignore:
            console.print(
                f"Managed .gitignore block: [cyan]{plan.gitignore_path}[/cyan]"
            )
        else:
            console.print("Managed .gitignore block: [dim]not present[/dim]")
    console.print(
        "Global Devstill/Pi installations and the shared embedding model will be "
        "preserved."
    )
    if dry_run:
        console.print("[yellow]Dry run: no files were removed.[/yellow]")
        return 0
    if not assume_yes:
        if not sys.stdin.isatty():
            raise ValueError(
                "Removal requires confirmation. Re-run with --yes in a "
                "noninteractive terminal."
            )
        try:
            confirmed = input("Continue? [y/N] ").strip().casefold() in {"y", "yes"}
        except EOFError:
            confirmed = False
        if not confirmed:
            console.print("Removal cancelled.")
            return 0

    result = execute_removal(plan)
    if result.errors:
        console.print("[red]Devstill removal was incomplete.[/red]")
        for path, message in result.errors:
            console.print(f"  [red]{path}: {message}[/red]")
        console.print(
            f"Configuration was preserved at [cyan]{plan.config_path}[/cyan] "
            "when possible so removal can be retried."
        )
        return 1
    for path in result.preserved:
        console.print(f"[yellow]Preserved unrelated content under {path}.[/yellow]")
    if index_only:
        console.print("[green]Removed Devstill index data.[/green]")
        console.print("Run [bold]devstill index[/bold] to build a fresh index.")
    else:
        console.print("[green]Removed Devstill from this repository.[/green]")
    return 0


def doctor(load_model: bool) -> int:
    failures = 0
    console.print(f"devstill: [bold]{__version__}[/bold]")
    console.print(f"Python: {sys.version.split()[0]}")
    try:
        import torch

        console.print(
            f"PyTorch: {torch.__version__}; CUDA available: {torch.cuda.is_available()}"
        )
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]PyTorch unavailable: {exc}[/red]")
        failures += 1
    model_path = (
        Path(
            os.environ.get(
                "DEVSTILL_MODEL_PATH",
                "~/.local/share/devstill/models/jina-embeddings-v2-base-code",
            )
        )
        .expanduser()
        .resolve()
    )
    try:
        cfg_path = find_config()
        cfg = load_config(cfg_path)
        model_path = resolve_model_path(cfg)
        console.print(f"Config: [green]{cfg_path}[/green]")
        console.print(f"Repository: {resolve_repo_root(cfg_path, cfg)}")
        console.print(f"Index: {resolve_index_path(cfg_path, cfg)}")
    except FileNotFoundError:
        console.print(
            f"Config: [yellow]No {CONFIG_FILENAME} found from this directory[/yellow]"
        )
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        failures += 1
    if model_path.is_dir():
        console.print(f"Model: [green]{model_path}[/green]")
    else:
        console.print(f"[red]Model missing: {model_path}[/red]")
        failures += 1
    try:
        import chromadb

        console.print(f"Chroma heartbeat: {chromadb.EphemeralClient().heartbeat()}")
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]Chroma unavailable: {exc}[/red]")
        failures += 1
    pi = inspect_pi()
    if pi.ready:
        console.print(
            f"Pi executor: [green]{pi.pi_version}[/green] at {pi.pi_path}; "
            f"Node {pi.node_version}"
        )
    else:
        console.print(
            f"Pi executor: [yellow]unavailable[/yellow] ({pi.reason}). "
            "Re-run setup_wsl_cpu.sh to enable code execution."
        )
    if load_model and model_path.is_dir():
        try:
            from sentence_transformers import SentenceTransformer

            model = SentenceTransformer(
                str(model_path),
                trust_remote_code=True,
                local_files_only=True,
                device="cpu",
            )
            model.max_seq_length = 2048
            vector = model.encode(
                ["devstill repository intelligence"],
                normalize_embeddings=True,
                convert_to_numpy=True,
                show_progress_bar=False,
            )
            console.print(f"Embedding shape: {tuple(vector.shape)}")
        except Exception as exc:  # noqa: BLE001
            console.print(f"[red]Model load failed: {exc}[/red]")
            failures += 1
    return 1 if failures else 0


def _configured() -> tuple[Path, DevstillConfig]:
    config_path = find_config()
    return config_path, load_config(config_path)


def files_command(as_json: bool, count: bool, ignored: bool = False) -> int:
    config_path, config = _configured()
    repo_root = resolve_repo_root(config_path, config)
    inventory = discover_repository_inventory(repo_root, config)
    relative = [item.relative for item in inventory.eligible]
    ignored_records = [
        {
            "path": item.relative,
            "category": item.decision.category,
            "reason": item.decision.reason,
        }
        for item in inventory.ignored
    ]
    skipped_records = [
        {
            "path": item.relative,
            "category": item.decision.category,
            "reason": item.decision.reason,
        }
        for item in inventory.skipped
    ]
    if as_json:
        print(
            json.dumps(
                {
                    "repository": str(repo_root),
                    "count": len(relative),
                    "files": relative,
                    "ignored_count": len(ignored_records),
                    "ignored_files": ignored_records,
                    "skipped_count": len(skipped_records),
                    "skipped_files": skipped_records,
                }
            )
        )
    elif count:
        print(len(ignored_records) if ignored else len(relative))
    elif ignored:
        print(
            "\n".join(
                f"{item['path']}\t{item['category']}\t{item['reason']}"
                for item in ignored_records
            )
        )
    else:
        print("\n".join(relative))
    return 0


def parse_command(value: str, as_json: bool) -> int:
    config_path, config = _configured()
    repo_root = resolve_repo_root(config_path, config)
    path = Path(value).expanduser()
    path = (Path.cwd() / path).resolve() if not path.is_absolute() else path.resolve()
    if not path.is_file() or not path.is_relative_to(repo_root):
        raise ValueError(
            f"{path} is not a file inside configured repository {repo_root}"
        )
    result = parse_source_file(
        path,
        repo_root,
        policy=ChunkPolicy(
            max_chars=config.index.max_chunk_chars,
            max_lines=config.index.max_chunk_lines,
            overlap_lines=config.index.fallback_overlap_lines,
        ),
        index_noisy_files=config.files.index_noisy_files,
        include_ignored=config.files.include_ignored,
    )
    if as_json:
        print(
            json.dumps(
                {
                    "path": relative_repo_path(repo_root, path),
                    "chunks": [chunk.to_dict() for chunk in result.chunks],
                    "errors": [error.to_dict() for error in result.errors],
                    "skipped": result.skipped,
                    "binary": result.binary,
                    "parser": result.parser,
                    "quality": result.quality.to_dict() if result.quality else None,
                },
                indent=2,
            )
        )
    else:
        if result.binary:
            console.print("[yellow]BINARY[/yellow] [dim]skipped[/dim]")
        elif result.quality:
            color = {
                "GOOD": "green",
                "MEH": "yellow",
                "BAD": "red",
                "SKIPPED": "dim",
            }[result.quality.status]
            console.print(
                f"[{color}]{result.quality.status}[/{color}] "
                f"[bold]{result.quality.parser}[/bold] "
                f"[cyan]{result.quality.category}[/cyan] "
                f"[dim]{result.quality.disposition}[/dim]"
            )
            for reason in result.quality.reasons:
                console.print(f"  [dim]{reason}[/dim]")
        for chunk in result.chunks:
            console.print(
                f"[bold]{chunk.kind}[/bold] {chunk.qualname or chunk.name or ''} "
                f"[dim]{chunk.path}:{chunk.start_line}-{chunk.end_line}[/dim]"
            )
        for error in result.errors:
            console.print(
                f"[red]{error.path}:{error.line or '?'}: {error.message}[/red]"
            )
    return 0 if not result.errors else 1


def index_command(rebuild: bool, verbose: bool, as_json: bool) -> int:
    config_path, config = _configured()
    indexer = RepositoryIndexer(config_path, config)
    try:
        summary = indexer.run(rebuild=rebuild, verbose=verbose)
    finally:
        indexer.close()
    if as_json:
        print(json.dumps(summary.to_dict()))
    else:
        labels = (
            ("Repository", summary.repository),
            ("Discovered files", summary.discovered_files),
            ("Unchanged files", summary.unchanged_files),
            ("Parsed files", summary.parsed_files),
            ("Deleted files", summary.deleted_files),
            ("Canonical chunks", summary.canonical_chunks),
            ("Search units embedded", summary.search_units_embedded),
            ("Reconciled search units", summary.reconciled_search_units),
            ("Parse errors", summary.parse_errors),
            ("Index path", summary.index_path),
        )
        for label, value in labels:
            console.print(f"{label}: [bold]{value}[/bold]")
        console.print("Index coverage:")
        console.print(f"  [green]Code: {summary.code_files}[/green]")
        console.print(f"  [green]Documentation: {summary.documentation_files}[/green]")
        console.print(f"  [green]Configuration: {summary.configuration_files}[/green]")
        unclassified = max(
            0,
            summary.eligible_files
            - summary.code_files
            - summary.documentation_files
            - summary.configuration_files,
        )
        console.print(f"  [yellow]Unknown text: {unclassified}[/yellow]")
        console.print("Index quality:")
        console.print(f"  [green]GOOD: {summary.good_files}[/green]")
        console.print(f"  [yellow]MEH:  {summary.meh_files}[/yellow]")
        console.print(f"  [red]BAD:   {summary.bad_files}[/red]")
        console.print("Skipped:")
        console.print(f"  [dim]Ignored: {summary.ignored_files}[/dim]")
        console.print(f"  [dim]Noisy/generated: {summary.noisy_files_skipped}[/dim]")
        console.print(f"  [dim]Empty: {summary.empty_files_skipped}[/dim]")
        console.print(f"  [dim]Binary: {summary.binary_files_skipped}[/dim]")
    return 0


def search_command(args: argparse.Namespace) -> int:
    if args.limit < 1:
        raise ValueError("--limit must be at least 1")
    config_path, config = _configured()
    engine = HybridSearch(config_path, config)
    try:
        results = engine.query(
            args.query,
            limit=args.limit,
            kind=args.kind,
            path=args.path,
            exact_only=args.exact,
            semantic_only=args.semantic_only,
            lexical_only=args.lexical_only,
        )
    finally:
        engine.close()
    if args.json:
        print(
            json.dumps(
                [result.to_dict(include_scores=True) for result in results], indent=2
            )
        )
    else:
        for result in results:
            chunk = result.chunk
            title = chunk.qualname or chunk.name or chunk.path
            score = f" score={result.final_score:.4f}" if args.show_score else ""
            console.print(
                f"[bold]{title}[/bold] [cyan]{chunk.kind}[/cyan] "
                f"[dim]{chunk.path}:{chunk.start_line}-{chunk.end_line}{score}[/dim]"
            )
            if args.show_score:
                console.print(
                    "  "
                    + ", ".join(
                        f"{name}={value:.4f}"
                        for name, value in sorted(result.scores.items())
                    )
                )
            console.print(chunk.code)
            console.print()
    return 0


def context_command(args: argparse.Namespace) -> int:
    if args.limit < 1:
        raise ValueError("--limit must be at least 1")
    config_path, config = _configured()
    engine = HybridSearch(config_path, config)
    try:
        candidates = engine.query(
            args.query,
            limit=max(args.limit * 4, 32),
            kind=args.kind,
            path=args.path,
            exact_only=args.exact,
            semantic_only=args.semantic_only,
            lexical_only=args.lexical_only,
        )
    finally:
        engine.close()
    results = select_context_results(candidates, args.limit, args.include_tests)
    repository = resolve_repo_root(config_path, config)
    json_output = render_context_json(results)
    markdown_output = render_context_markdown(results, args.query, str(repository))
    write_context_artifacts(
        resolve_index_path(config_path, config).parent,
        markdown_output,
        json_output,
    )
    if args.json:
        print(json_output)
    else:
        print(markdown_output, end="")
    return 0


def _print_agent(config: DevstillConfig) -> None:
    llm = config.llm
    key_state = api_key_source(llm)
    labels = (
        ("Enabled", llm.enabled),
        ("Base URL", llm.base_url),
        ("Model", llm.model or "not configured"),
        ("Timeout", f"{llm.timeout_seconds:g} seconds"),
        ("General max output tokens", llm.max_output_tokens),
        ("Planning max output tokens", llm.planning_max_output_tokens),
        ("Pi max output tokens", config.executor.max_output_tokens),
        ("Planning reasoning", llm.planning_reasoning),
        ("API key environment", f"{llm.api_key_env} ({key_state})"),
    )
    for label, value in labels:
        console.print(f"{label}: [bold]{value}[/bold]")


def agent_command(args: argparse.Namespace) -> int:
    config_path, config = _configured()
    action = args.agent_action or "show"
    if action == "configure":
        updates: dict[str, object] = {"enabled": True}
        values = {
            "base_url": args.base_url,
            "model": args.model,
            "api_key_env": args.api_key_env,
            "timeout_seconds": args.timeout,
            "max_output_tokens": args.max_output_tokens,
            "planning_max_output_tokens": args.planning_max_output_tokens,
            "planning_reasoning": args.planning_reasoning,
        }
        updates.update(
            {key: value for key, value in values.items() if value is not None}
        )
        config = update_llm_config(config_path, config, **updates)
        console.print(f"[green]Updated agent configuration in {config_path}[/green]")
        _print_agent(config)
        return 0
    if action == "enable":
        config = update_llm_config(config_path, config, enabled=True)
        console.print("[green]Agent enabled.[/green]")
        return 0
    if action == "disable":
        update_llm_config(config_path, config, enabled=False)
        console.print("[yellow]Agent disabled.[/yellow]")
        return 0
    if action == "test":
        if not config.llm.enabled:
            raise ValueError("Agent is disabled. Run `devstill agent enable`.")
        agent_log = resolve_index_path(config_path, config).parent / "logs/agent.jsonl"
        diagnostic_console.print(f"Agent response log: {agent_log}")
        with AgentClient(config.llm, log_path=agent_log) as client:
            result = client.test()
        console.print(
            f"[green]Agent OK[/green]: [bold]{result.model}[/bold] at "
            f"[cyan]{result.endpoint}[/cyan] ({result.elapsed_seconds:.3f}s)"
        )
        return 0
    _print_agent(config)
    return 0


def plan_command(args: argparse.Namespace) -> int:
    if args.limit < 1:
        raise ValueError("--limit must be at least 1")
    config_path, config = _configured()
    if not config.llm.enabled:
        raise ValueError(
            "Agent is disabled. Run `devstill agent configure --base-url URL --model MODEL`."
        )
    repo_root = resolve_repo_root(config_path, config)
    engine = HybridSearch(config_path, config)
    try:
        candidates = engine.query(
            args.query,
            limit=planning_retrieval_limit(args.limit),
            kind=args.kind,
            path=args.path,
            exact_only=args.exact,
            semantic_only=args.semantic_only,
            lexical_only=args.lexical_only,
        )
    finally:
        engine.close()
    results = select_adaptive_context_results(
        candidates,
        min_limit=min(PLAN_MIN_CHUNKS, args.limit),
        max_limit=args.limit,
        include_tests=args.include_tests,
    )
    agent_log = resolve_index_path(config_path, config).parent / "logs/agent.jsonl"
    client = AgentClient(config.llm, log_path=agent_log)
    try:
        diagnostic_console.print(f"Agent response log: {agent_log}")
        diagnostic_console.print(
            f"Sending {len(results)} source chunks to {client.endpoint}"
        )
        plan = generate_plan(
            client,
            query=args.query,
            repository=str(repo_root),
            repo_root=repo_root,
            results=results,
        )
    finally:
        client.close()
    json_output = render_plan_json(plan)
    markdown_output = render_plan_markdown(plan)
    write_plan_artifacts(
        resolve_index_path(config_path, config).parent,
        markdown_output,
        json_output,
    )
    if args.json:
        print(json_output)
    else:
        for item in plan.items:
            style = LABEL_STYLES[item.label]
            console.print(
                f"[{style}]{item.label.value.upper()}[/{style}] "
                f"[bold]{item.target}[/bold]"
            )
            console.print(f"  {item.reason}")
    return 0


def deep_plan_command(args: argparse.Namespace) -> int:
    if args.initial_limit < 1:
        raise ValueError("--initial-limit must be at least 1")
    if args.limit < args.initial_limit:
        raise ValueError("--limit must be at least --initial-limit")
    config_path, config = _configured()
    if not config.llm.enabled:
        raise ValueError(
            "Agent is disabled. Run `devstill agent configure --base-url URL --model MODEL`."
        )
    repo_root = resolve_repo_root(config_path, config)
    artifact_root = resolve_index_path(config_path, config).parent
    engine = HybridSearch(config_path, config)
    agent_log = artifact_root / "logs/agent.jsonl"
    client = AgentClient(config.llm, log_path=agent_log)
    try:
        candidates = engine.query(
            args.query,
            limit=planning_retrieval_limit(args.initial_limit),
            kind=args.kind,
            path=args.path,
            exact_only=args.exact,
            semantic_only=args.semantic_only,
            lexical_only=args.lexical_only,
        )
        initial_results = select_adaptive_context_results(
            candidates,
            min_limit=min(PLAN_MIN_CHUNKS, args.initial_limit),
            max_limit=args.initial_limit,
            include_tests=args.include_tests,
        )
        diagnostic_console.print(f"Agent response log: {agent_log}")
        diagnostic_console.print(
            f"Sending {len(initial_results)} initial source chunks to {client.endpoint}"
        )

        def search_reasons(values: list[str]):
            options = {
                "limit": planning_retrieval_limit(args.limit),
                "kind": args.kind,
                "path": args.path,
                "exact_only": args.exact,
                "semantic_only": args.semantic_only,
                "lexical_only": args.lexical_only,
            }
            query_many = getattr(engine, "query_many", None)
            if callable(query_many):
                return query_many(values, **options)
            return [engine.query(value, **options) for value in values]

        def initial_ready(initial_plan):
            write_deep_plan_artifacts(
                artifact_root,
                render_plan_markdown(
                    initial_plan,
                    title="Devstill Deep Plan — Initial Pass",
                ),
                render_plan_json(initial_plan),
                initial=True,
            )
            diagnostic_console.print(
                "Initial Deep Plan complete; searching up to 3 focused reasons"
            )

        def context_ready(_initial_plan, expanded_results):
            paths = {result.chunk.path for result in expanded_results}
            diagnostic_console.print(
                f"Sending {len(expanded_results)} Deep Plan chunks across "
                f"{len(paths)} files to {client.endpoint}"
            )

        run = run_deep_plan(
            client,
            query=args.query,
            repository=str(repo_root),
            repo_root=repo_root,
            initial_results=initial_results,
            search=search_reasons,
            deep_limit=args.limit,
            deep_minimum=min(DEEP_PLAN_MIN_CHUNKS, args.limit),
            include_tests=args.include_tests,
            on_initial=initial_ready,
            on_context=context_ready,
        )
    finally:
        client.close()
        engine.close()
    json_output = render_plan_json(run.final_plan)
    markdown_output = render_plan_markdown(
        run.final_plan,
        title="Devstill Deep Plan",
    )
    write_deep_plan_artifacts(artifact_root, markdown_output, json_output)
    if args.json:
        print(json_output)
    else:
        for item in run.final_plan.items:
            style = LABEL_STYLES[item.label]
            console.print(
                f"[{style}]{item.label.value.upper()}[/{style}] "
                f"[bold]{item.target}[/bold]"
            )
            console.print(f"  {item.reason}")
    return 0


def execute_command(args: argparse.Namespace) -> int:
    if args.dry_run and args.yes:
        raise ValueError("--dry-run and --yes cannot be used together")
    config_path, config = _configured()
    repo_root = resolve_repo_root(config_path, config)
    artifact_root = resolve_index_path(config_path, config).parent
    executor = RepositoryExecutor(repo_root, artifact_root, config)
    prepared = None
    try:
        prepared = executor.prepare(
            source=args.source,
            progress=lambda message: diagnostic_console.print(message),
        )
        if args.json:
            if args.dry_run:
                print(json.dumps(prepared.result.to_dict(), indent=2))
                return 0
            if not args.yes:
                diagnostic_console.print(
                    render_execution_markdown(prepared.result, prepared.patch)
                )
        else:
            print(render_execution_markdown(prepared.result, prepared.patch), end="")
        if args.dry_run:
            console.print(
                "[yellow]Dry run: the patch was generated but not applied.[/yellow]"
            )
            return 0

        confirmed = args.yes
        if not confirmed:
            if not sys.stdin.isatty():
                raise ValueError(
                    "Execution requires confirmation. Re-run with --yes or --dry-run "
                    "in a noninteractive terminal."
                )
            diagnostic_console.print(f"Apply this patch to {repo_root}? [y/N] ", end="")
            try:
                confirmed = sys.stdin.readline().strip().casefold() in {"y", "yes"}
            except EOFError:
                confirmed = False
        if not confirmed:
            diagnostic_console.print(
                "Execution cancelled; working files were unchanged."
            )
            if args.json:
                print(json.dumps(prepared.result.to_dict(), indent=2))
            return 0

        result = executor.apply(
            prepared,
            progress=lambda message: diagnostic_console.print(message),
        )
        if not config.executor.validation_commands:
            diagnostic_console.print(
                "No executor.validation_commands are configured; validation was skipped."
            )
        if args.json:
            print(json.dumps(result.to_dict(), indent=2))
        else:
            console.print(render_execution_markdown(result, ""), end="")
        return 1 if result.status == "validation_failed" else 0
    finally:
        if prepared is not None:
            prepared.close()


def main() -> None:
    args = parser().parse_args()
    try:
        if args.command == "init":
            raise SystemExit(init_repo(args.force))
        if args.command == "remove":
            raise SystemExit(remove_repo(args.index_only, args.dry_run, args.yes))
        if args.command == "doctor":
            raise SystemExit(doctor(args.load_model))
        if args.command == "files":
            raise SystemExit(files_command(args.json, args.count, args.ignored))
        if args.command == "parse":
            raise SystemExit(parse_command(args.path, args.json))
        if args.command == "index":
            raise SystemExit(index_command(args.rebuild, args.verbose, args.json))
        if args.command == "search":
            raise SystemExit(search_command(args))
        if args.command == "context":
            raise SystemExit(context_command(args))
        if args.command == "agent":
            raise SystemExit(agent_command(args))
        if args.command == "plan":
            raise SystemExit(plan_command(args))
        if args.command == "deep-plan":
            raise SystemExit(deep_plan_command(args))
        if args.command == "execute":
            raise SystemExit(execute_command(args))
        config_path, config = _configured()
        DevstillApp(config_path, config).run()
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
