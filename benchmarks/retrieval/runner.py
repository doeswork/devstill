"""Run with PYTHONPATH=src python -m benchmarks.retrieval.runner --help."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import importlib.metadata
import inspect
import json
import math
import platform
import random
import re
import statistics
import subprocess
import sys
import time
from collections import Counter
from contextlib import contextmanager
from pathlib import Path, PurePosixPath

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
CORPUS = json.loads((HERE / "corpus.json").read_text())
DEFAULT_WORKSPACE = Path("/tmp/devstill-retrieval-bench")
MODES = {
    "hybrid": {},
    "semantic": {"semantic_only": True},
    "no_vector": {"lexical_only": True},
}


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def fingerprint(value) -> str:
    return digest(json.dumps(value, sort_keys=True).encode())


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, indent=2) + "\n")


def git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()


def check_repo(repo: Path, commit: str) -> None:
    if git(repo, "rev-parse", "HEAD") != commit:
        raise ValueError("Corpus commit differs from the dataset pin")
    if git(repo, "status", "--porcelain", "--untracked-files=all"):
        raise ValueError(
            "Corpus checkout must be clean; keep configs/indexes outside it"
        )


def tree_digest(root: Path) -> str:
    """Hash model contents, not merely a mutable model directory name."""
    if not root.is_dir():
        raise ValueError(f"Missing local model directory: {root}")
    hashes = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if path.is_file() and not any(part.startswith(".") for part in relative.parts):
            h = hashlib.sha256()
            with path.open("rb") as handle:
                for block in iter(lambda: handle.read(1024 * 1024), b""):
                    h.update(block)
            hashes[relative.as_posix()] = h.hexdigest()
    if not hashes:
        raise ValueError("Model directory is empty")
    return fingerprint(hashes)


def code_hash(paths) -> str:
    return fingerprint(
        {str(p.relative_to(ROOT)): digest(p.read_bytes()) for p in sorted(paths)}
    )


def packages() -> dict:
    return {
        name: importlib.metadata.version(name)
        for name in (
            "torch",
            "chromadb",
            "sentence-transformers",
            "transformers",
            "tokenizers",
            "rapidfuzz",
            "tree-sitter",
            "tree-sitter-javascript",
            "tree-sitter-typescript",
        )
    }


def index_identity(config_path: Path):
    from devstill.config import (
        load_config,
        resolve_index_path,
        resolve_model_path,
        resolve_repo_root,
    )

    config = load_config(config_path)
    repo = resolve_repo_root(config_path, config)
    check_repo(repo, CORPUS["commit"])
    model = resolve_model_path(config)
    source = ROOT / "src/devstill"
    index_files = (
        [
            source / name
            for name in (
                "indexer.py",
                "embeddings.py",
                "context.py",
                "config.py",
                "models.py",
                "git_files.py",
                "content_policy.py",
                "hashing.py",
                "devices.py",
                "paths.py",
            )
        ]
        + list((source / "parsers").glob("*.py"))
        + list((source / "storage").glob("*.py"))
    )
    identity = {
        "corpus_commit": CORPUS["commit"],
        "repo": str(repo),
        "index_config": config.index.model_dump(),
        "files_config": config.files.model_dump(),
        "embedding_config": config.embedding.model_dump(),
        "resolved_model": str(model),
        "model_sha256": tree_digest(model),
        "index_code_sha256": code_hash(index_files),
        "packages": packages(),
        "python_version": platform.python_version(),
    }
    return config, repo, resolve_index_path(config_path, config).parent, identity


@contextmanager
def index_lock(root: Path):
    root.mkdir(parents=True, exist_ok=True)
    with (root / ".benchmark.lock").open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError("Another benchmark is using this index") from exc
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def validate_dataset(dataset: dict, repo: Path) -> None:
    if (
        dataset.get("schema_version") != 1
        or dataset.get("corpus_commit") != CORPUS["commit"]
    ):
        raise ValueError("Dataset schema or corpus pin is invalid")
    seen, families = set(), {}
    if not dataset.get("cases"):
        raise ValueError("Dataset is empty")
    for case in dataset["cases"]:
        if case["id"] in seen or not case["query"].strip():
            raise ValueError("Duplicate case ID or empty query")
        seen.add(case["id"])
        if case["split"] not in {"dev", "holdout"}:
            raise ValueError("Split must be dev or holdout")
        if families.setdefault(case["family"], case["split"]) != case["split"]:
            raise ValueError("Related query family leaks across splits")
        if not case["category"] or not case["rationale"]:
            raise ValueError("Cases require a category and labeling rationale")
        if case.get("label_status") not in {"seed", "reviewed"}:
            raise ValueError("Labels must be seed or reviewed")
        if case["label_status"] == "reviewed" and not case.get("reviewer"):
            raise ValueError("Reviewed cases must name their reviewer")
        if (
            type(case["answerable"]) is not bool
            or type(case["include_tests"]) is not bool
        ):
            raise ValueError("answerable and include_tests must be booleans")
        groups = case["evidence_groups"]
        if case["answerable"] != bool(groups):
            raise ValueError(
                "Positive cases need evidence; negative cases must have none"
            )
        if set(case.get("filters", {})) - {"path", "kind"}:
            raise ValueError("Only path and kind query filters are supported")
        for alternatives in groups:
            if not alternatives:
                raise ValueError("An evidence group needs at least one alternative")
            for target in alternatives:
                relative = PurePosixPath(target["path"])
                path = (repo / relative).resolve()
                if (
                    relative.is_absolute()
                    or ".." in relative.parts
                    or not path.is_relative_to(repo.resolve())
                ):
                    raise ValueError("Evidence path escapes corpus")
                lines = path.read_text().splitlines(keepends=True)
                start, end = target["lines"]
                if not 1 <= start <= end <= len(lines):
                    raise ValueError(f"Invalid evidence lines: {target}")
                if digest("".join(lines[start - 1 : end]).encode()) != target["sha256"]:
                    raise ValueError(
                        f"Evidence content changed: {target['path']}:{start}"
                    )


def covers(target: dict, hits: list[dict]) -> bool:
    """All gold lines must be covered; adjacent/overlapping chunks may combine."""
    cursor, end = target["lines"]
    intervals = sorted(h["lines"] for h in hits if h["path"] == target["path"])
    for left, right in intervals:
        if left > cursor:
            break
        if right >= cursor:
            cursor = right + 1
        if cursor > end:
            return True
    return False


def evidence_recall(case: dict, hits: list[dict]) -> float:
    groups = case["evidence_groups"]
    return sum(
        any(covers(t, hits) for t in alternatives) for alternatives in groups
    ) / len(groups)


def score(case: dict, hits: list[dict], context: list[dict]) -> dict:
    if not case["answerable"]:
        return {
            "negative_returned_any": bool(hits),
            "negative_context_nonempty": bool(context),
        }
    paths = {t["path"] for group in case["evidence_groups"] for t in group}
    unique = list(dict.fromkeys(h["path"] for h in hits))
    rank = next(
        (i for i in range(1, len(hits) + 1) if evidence_recall(case, hits[:i]) > 0),
        None,
    )
    return {
        "file_hit_at_5_chunks": bool(paths.intersection(h["path"] for h in hits[:5])),
        "file_hit_at_5_unique_files_within_pool": bool(paths.intersection(unique[:5])),
        "evidence_hit_at_5_chunks": evidence_recall(case, hits[:5]) > 0,
        "evidence_recall_at_5_chunks": evidence_recall(case, hits[:5]),
        "evidence_recall_at_pool": evidence_recall(case, hits),
        "evidence_mrr_at_pool": 1 / rank if rank else 0,
        "context_evidence_recall": evidence_recall(case, context),
        "context_complete": evidence_recall(case, context) == 1,
    }


def percentile(values: list[float], fraction: float) -> float:
    return sorted(values)[max(0, math.ceil(fraction * len(values)) - 1)]


def summarize(rows: list[dict]) -> dict:
    keys = {key for row in rows for key in row["metrics"]}
    return {
        "cases": len(rows),
        "metrics": {
            key: statistics.mean(r["metrics"][key] for r in rows if key in r["metrics"])
            for key in sorted(keys)
        },
        "query_latency_median_ms": statistics.median(
            r["latency_median_ms"] for r in rows
        ),
        "query_latency_p95_ms": percentile(
            [r["latency_median_ms"] for r in rows], 0.95
        ),
        "context_characters_median": statistics.median(
            r["context_characters"] for r in rows
        ),
    }


def prepare(args) -> None:
    from devstill.config import default_config_dict

    if not re.fullmatch(r"[a-zA-Z0-9_-]+", args.profile):
        raise ValueError(
            "Profile must contain only letters, digits, underscores or hyphens"
        )
    workspace = args.workspace.resolve()
    repo = workspace / "repos" / CORPUS["id"]
    if not repo.exists():
        repo.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [
                "git",
                "clone",
                "--depth",
                "1",
                "--branch",
                CORPUS["ref"],
                CORPUS["url"],
                str(repo),
            ],
            check=True,
        )
    check_repo(repo, CORPUS["commit"])
    config = default_config_dict()
    config["repo"]["root"] = str(repo)
    config["index"]["path"] = str(workspace / "indexes" / args.profile / "chroma")
    config["embedding"]["device"] = args.device
    if args.model:
        config["embedding"]["model"] = str(args.model.resolve())
    config["llm"]["enabled"] = False
    config["executor"]["enabled"] = False
    path = workspace / "configs" / f"{args.profile}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and json.loads(path.read_text()) != config:
        raise ValueError("Config exists with different settings; choose a new profile")
    write_json(path, config)
    print(path)


def validate(args) -> None:
    dataset = json.loads(args.dataset.read_text())
    check_repo(args.repo, dataset["corpus_commit"])
    validate_dataset(dataset, args.repo)
    print(
        json.dumps(
            {
                "cases": len(dataset["cases"]),
                "splits": dict(Counter(c["split"] for c in dataset["cases"])),
                "labels": dict(Counter(c["label_status"] for c in dataset["cases"])),
            }
        )
    )


def build_index(args) -> None:
    from devstill.indexer import RepositoryIndexer

    config, repo, root, identity = index_identity(args.config)
    with index_lock(root):
        if (root / "metadata.sqlite").exists():
            raise ValueError(
                "Index already exists; run it as-is or prepare a new profile"
            )
        started = time.perf_counter()
        indexer = RepositoryIndexer(args.config, config)
        try:
            summary = indexer.run()
            model = indexer.embedder.warm()
            units = indexer.store.all_search_units()
            lengths = []
            for start in range(0, len(units), 64):
                lengths.extend(
                    map(
                        len,
                        model.tokenizer(
                            [u.embedding_text for u in units[start : start + 64]],
                            truncation=False,
                        )["input_ids"],
                    )
                )
            check_repo(repo, identity["corpus_commit"])
            manifest = {
                "identity": identity,
                "identity_sha256": fingerprint(identity),
                "summary": summary.to_dict(),
                "index_and_token_audit_seconds": time.perf_counter() - started,
                "embedding_inputs": len(units),
                "truncated_embedding_inputs": sum(
                    n > config.embedding.max_sequence_length for n in lengths
                ),
                "resolved_device": indexer.embedder.device,
            }
            write_json(root / "benchmark-index.json", manifest)
        finally:
            indexer.close()
    print(json.dumps(manifest, indent=2))


def serialize(result) -> dict:
    return {
        "id": result.chunk.id,
        "path": result.chunk.path,
        "lines": [result.chunk.start_line, result.chunk.end_line],
        "symbol": result.chunk.qualname,
        "kind": result.chunk.kind,
        "score": result.final_score,
        "channels": result.scores,
        "code": result.chunk.code,
    }


def run(args) -> None:
    from devstill.context import select_adaptive_context_results
    from devstill.search import HybridSearch

    source_files = list((ROOT / "src/devstill").rglob("*.py"))
    source_sha256 = code_hash(source_files)
    config, repo, root, identity = index_identity(args.config)
    dataset = json.loads(args.dataset.read_text())
    validate_dataset(dataset, repo)
    cases = [c for c in dataset["cases"] if c["split"] == args.split]
    if not cases:
        raise ValueError(
            f"No cases in split {args.split}; author and review them first"
        )
    if not args.allow_seed and any(c["label_status"] != "reviewed" for c in cases):
        raise ValueError("Unreviewed labels; use --allow-seed only for harness pilots")
    if args.split == "holdout" and args.allow_seed:
        raise ValueError("Holdout requires reviewed labels")
    if args.limit < 5 or args.repeats < 1:
        raise ValueError("Require limit >= 5 and repeats >= 1")
    with index_lock(root):
        manifest = json.loads((root / "benchmark-index.json").read_text())
        if manifest["identity_sha256"] != fingerprint(identity):
            raise ValueError(
                "Index fingerprint changed; prepare and index a fresh profile"
            )
        args.output.mkdir(parents=True, exist_ok=False)
        started = time.perf_counter()
        search = HybridSearch(args.config, config)
        rows = []
        try:
            # Warm both sides of the comparison; startup is excluded from query timing.
            search.embedder.warm()
            warmup = time.perf_counter() - started
            ordered = list(cases)
            random.Random(args.order_seed).shuffle(ordered)
            for case in ordered:
                timings, results, observed = [], [], []
                for iteration in range(args.repeats + 1):
                    started = time.perf_counter()
                    results = search.query(
                        case["query"],
                        limit=args.limit,
                        **MODES[args.mode],
                        **case.get("filters", {}),
                    )
                    elapsed = (time.perf_counter() - started) * 1000
                    if iteration:
                        timings.append(elapsed)
                        observed.append([r.chunk.id for r in results])
                context = select_adaptive_context_results(
                    results,
                    min_limit=3,
                    max_limit=5,
                    include_tests=case["include_tests"],
                )
                hits, selected = (
                    list(map(serialize, results)),
                    list(map(serialize, context)),
                )
                rows.append(
                    {
                        "id": case["id"],
                        "family": case["family"],
                        "category": case["category"],
                        "answerable": case["answerable"],
                        "query": case["query"],
                        "filters": case.get("filters", {}),
                        "metrics": score(case, hits, selected),
                        "latencies_ms": timings,
                        "latency_median_ms": statistics.median(timings),
                        "ranking_stable": all(ids == observed[0] for ids in observed),
                        "context_characters": sum(len(r["code"]) for r in selected),
                        "context_tokens": None,
                        "hits": hits,
                        "context": selected,
                    }
                )
            check_repo(repo, CORPUS["commit"])
            if code_hash(source_files) != source_sha256:
                raise ValueError(
                    "Devstill source changed during the run; discard this run"
                )
        finally:
            search.close()
        rows.sort(key=lambda row: row["id"])
        protocol = {
            "protocol_version": 1,
            "scoring_sha256": digest(
                "\n".join(
                    inspect.getsource(fn) for fn in (covers, evidence_recall, score)
                ).encode()
            ),
            "dataset_sha256": fingerprint(dataset),
            "cases_sha256": fingerprint(cases),
            "split": args.split,
            "pool_limit": args.limit,
            "context_limits": [3, 5],
            "repeats": args.repeats,
            "order_seed": args.order_seed,
            "corpus_commit": CORPUS["commit"],
        }
        report = {
            "schema_version": 1,
            "status": "pilot" if args.allow_seed else "reviewed_dataset_run",
            "mode": args.mode,
            "protocol": protocol,
            "index_manifest": manifest,
            "retrieval_exclude": config.retrieval.exclude,
            "devstill_commit": git(ROOT, "rev-parse", "HEAD"),
            "source_sha256": source_sha256,
            "runner_sha256": digest(Path(__file__).read_bytes()),
            "python": sys.version,
            "platform": platform.platform(),
            "encoder_device": search.embedder.device,
            "encoder_warmup_seconds": warmup,
            "summary": summarize(rows),
            "by_category": {
                cat: summarize([r for r in rows if r["category"] == cat])
                for cat in sorted({r["category"] for r in rows})
            },
            "queries": rows,
        }
        write_json(args.output / "report.json", report)
        write_json(args.output / "config.json", config.model_dump())
        write_json(args.output / "dataset.json", dataset)
        (args.output / "runner.py").write_bytes(Path(__file__).read_bytes())
        (args.output / "source.diff").write_text(git(ROOT, "diff", "HEAD", "--", "src"))
        print(json.dumps(report["summary"], indent=2))


def paired_comparison(baseline: dict, candidate: dict) -> dict:
    if baseline["protocol"] != candidate["protocol"]:
        raise ValueError(
            "Protocol/dataset mismatch: do not compare different cases or budgets"
        )
    left = {r["id"]: r for r in baseline["queries"] if r["answerable"]}
    right = {r["id"]: r for r in candidate["queries"] if r["answerable"]}
    if not left or left.keys() != right.keys():
        raise ValueError("Paired comparison needs matching positive cases")
    metric = "evidence_hit_at_5_chunks"
    changes = {
        key: int(right[key]["metrics"][metric]) - int(row["metrics"][metric])
        for key, row in left.items()
    }
    families = sorted({row["family"] for row in left.values()})
    rng = random.Random(42)
    boot = []
    for _ in range(2000):
        sample = rng.choices(families, k=len(families))
        boot.append(
            statistics.mean(
                changes[key]
                for family in sample
                for key, row in left.items()
                if row["family"] == family
            )
        )
    return {
        "metric": metric,
        "paired_delta": statistics.mean(changes.values()),
        "family_bootstrap_95_percent_interval": [
            percentile(boot, 0.025),
            percentile(boot, 0.975),
        ],
        "wins": [k for k, v in changes.items() if v > 0],
        "regressions": [k for k, v in changes.items() if v < 0],
        "note": "Exploratory interval; repeated tuning and small family counts limit inference. Inspect all metrics and per-category regressions, not only this statistic.",
    }


def main() -> None:
    global CORPUS
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--corpus-manifest",
        type=Path,
        help="Pinned corpus manifest; defaults to Werkzeug",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser(
        "prepare", help="Clone pinned corpus and create an external config; no indexing"
    )
    p.add_argument("--workspace", type=Path, default=DEFAULT_WORKSPACE)
    p.add_argument("--profile", default="baseline")
    p.add_argument("--device", choices=["cpu", "cuda", "auto"], default="cuda")
    p.add_argument("--model", type=Path)
    p.set_defaults(action=prepare)
    p = sub.add_parser(
        "validate",
        help="Check corpus, labels and evidence hashes without loading a model",
    )
    p.add_argument("--repo", type=Path)
    p.add_argument("--dataset", type=Path)
    p.set_defaults(action=validate)
    p = sub.add_parser(
        "index", help="Build a fresh isolated index and audit embedding truncation"
    )
    p.add_argument("--config", type=Path, required=True)
    p.set_defaults(action=build_index)
    p = sub.add_parser(
        "run", help="Run one retrieval variant; never invoke an LLM or execute a patch"
    )
    p.add_argument("--config", type=Path, required=True)
    p.add_argument("--dataset", type=Path)
    p.add_argument("--split", choices=["dev", "holdout"], default="dev")
    p.add_argument("--mode", choices=list(MODES), default="hybrid")
    p.add_argument("--allow-seed", action="store_true")
    p.add_argument("--limit", type=int, default=12)
    p.add_argument("--repeats", type=int, default=3)
    p.add_argument("--order-seed", type=int, default=42)
    p.add_argument("--output", type=Path, required=True)
    p.set_defaults(action=run)
    p = sub.add_parser(
        "compare", help="Paired evidence-hit comparison of two saved reports"
    )
    p.add_argument("baseline", type=Path)
    p.add_argument("candidate", type=Path)
    p.set_defaults(
        action=lambda args: print(
            json.dumps(
                paired_comparison(
                    json.loads(args.baseline.read_text()),
                    json.loads(args.candidate.read_text()),
                ),
                indent=2,
            )
        )
    )
    args = parser.parse_args()
    try:
        if args.corpus_manifest:
            manifest = json.loads(args.corpus_manifest.read_text())
            if (
                not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", manifest.get("id", ""))
                or not re.fullmatch(r"[0-9a-f]{40}", manifest.get("commit", ""))
                or not manifest.get("url", "").startswith("https://github.com/")
                or not manifest.get("ref")
            ):
                raise ValueError("Invalid corpus id, GitHub URL, ref or pinned commit")
            CORPUS = manifest
        if args.command in {"validate", "run"} and args.dataset is None:
            if args.corpus_manifest:
                raise ValueError("A custom corpus requires an explicit --dataset")
            args.dataset = HERE / "werkzeug.seed.json"
        if args.command == "validate" and args.repo is None:
            args.repo = DEFAULT_WORKSPACE / "repos" / CORPUS["id"]
        args.action(args)
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        parser.exit(2, f"benchmark error: {exc}\n")


if __name__ == "__main__":
    main()
