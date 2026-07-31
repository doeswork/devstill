from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from rich.console import Console

from devstill import __version__
from devstill.config import (
    CONFIG_FILENAME,
    DevstillConfig,
    find_config,
    load_config,
    resolve_index_path,
    resolve_model_path,
    resolve_repo_root,
    write_default_config,
)
from devstill.context import (
    render_context_json,
    render_context_markdown,
    select_context_results,
    write_context_artifacts,
)
from devstill.git_files import discover_repository_files
from devstill.indexer import RepositoryIndexer
from devstill.parsers import parse_source_file
from devstill.paths import relative_repo_path
from devstill.search import HybridSearch
from devstill.tui import DevstillApp

console = Console()


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

    doctor = sub.add_parser(
        "doctor", help="Check the installation and current repository."
    )
    doctor.add_argument("--load-model", action="store_true")

    files = sub.add_parser("files", help="List files eligible for indexing.")
    files_output = files.add_mutually_exclusive_group()
    files_output.add_argument("--json", action="store_true")
    files_output.add_argument("--count", action="store_true")

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
    return command


def init_repo(force: bool) -> int:
    try:
        path = write_default_config(Path.cwd() / CONFIG_FILENAME, force)
    except FileExistsError as exc:
        console.print(f"[red]{exc}[/red]")
        return 1
    ignore = Path.cwd() / ".gitignore"
    lines = ignore.read_text(encoding="utf-8").splitlines() if ignore.exists() else []
    if ".devstill/" not in lines:
        with ignore.open("a", encoding="utf-8") as handle:
            if lines:
                handle.write("\n")
            handle.write("# devstill local index\n.devstill/\n")
    (path.parent / ".devstill").mkdir(exist_ok=True)
    console.print(f"[green]Created {path}[/green]")
    console.print("Run [bold]devstill index[/bold] to build the repository index.")
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


def files_command(as_json: bool, count: bool) -> int:
    config_path, config = _configured()
    repo_root = resolve_repo_root(config_path, config)
    paths = discover_repository_files(repo_root, config)
    relative = [relative_repo_path(repo_root, path) for path in paths]
    if as_json:
        print(
            json.dumps(
                {
                    "repository": str(repo_root),
                    "count": len(relative),
                    "files": relative,
                }
            )
        )
    elif count:
        print(len(relative))
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
    result = parse_source_file(path, repo_root)
    if as_json:
        print(
            json.dumps(
                {
                    "path": relative_repo_path(repo_root, path),
                    "chunks": [chunk.to_dict() for chunk in result.chunks],
                    "errors": [error.to_dict() for error in result.errors],
                    "skipped": result.skipped,
                },
                indent=2,
            )
        )
    else:
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
            ("Parse errors", summary.parse_errors),
            ("Index path", summary.index_path),
        )
        for label, value in labels:
            console.print(f"{label}: [bold]{value}[/bold]")
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


def main() -> None:
    args = parser().parse_args()
    try:
        if args.command == "init":
            raise SystemExit(init_repo(args.force))
        if args.command == "doctor":
            raise SystemExit(doctor(args.load_model))
        if args.command == "files":
            raise SystemExit(files_command(args.json, args.count))
        if args.command == "parse":
            raise SystemExit(parse_command(args.path, args.json))
        if args.command == "index":
            raise SystemExit(index_command(args.rebuild, args.verbose, args.json))
        if args.command == "search":
            raise SystemExit(search_command(args))
        if args.command == "context":
            raise SystemExit(context_command(args))
        config_path, config = _configured()
        DevstillApp(config_path, config).run()
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
