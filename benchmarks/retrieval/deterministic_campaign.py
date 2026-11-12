"""Matched-budget comparison against an explicitly saved production baseline."""

from __future__ import annotations

import argparse
import importlib.util
import json
import statistics
import time
from pathlib import Path

import httpx

from benchmarks.retrieval import runner
from devstill.config import load_config, resolve_index_path, resolve_repo_root
from devstill.context import (
    context_record,
    query_requests_tests,
    select_adaptive_context_results,
)
from devstill.credentials import resolve_api_key
from devstill.search import HybridSearch


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def run(args):
    config = load_config(args.config)
    repo = resolve_repo_root(args.config, config)
    runner.check_repo(repo, runner.CORPUS["commit"])
    dataset = json.loads(args.dataset.read_text())
    runner.validate_dataset(dataset, repo)
    args.output.mkdir(parents=True, exist_ok=False)
    baseline_search = module("baseline_search", args.baseline / "search.py")
    baseline_store = module("baseline_store", args.baseline / "storage/sqlite_store.py")
    baseline_context = module("baseline_context", args.baseline / "context.py")
    baseline_search.SQLiteStore = baseline_store.SQLiteStore
    llm = load_config(args.agent_config).llm
    key = resolve_api_key(llm)
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    root = resolve_index_path(args.config, config).parent
    manifest = json.loads((root / "benchmark-index.json").read_text())
    # This campaign intentionally reuses the frozen index: no index writes occur.
    if manifest["identity"]["corpus_commit"] != dataset["corpus_commit"]:
        raise ValueError("Index corpus differs from dataset")
    if manifest["identity"]["embedding_config"] != config.embedding.model_dump():
        raise ValueError("Embedding configuration differs from frozen index")
    runner.write_json(args.output / "dataset.json", dataset)
    runner.write_json(
        args.output / "provenance.json",
        {
            "baseline_sha256": runner.code_hash(
                list(args.baseline.resolve().rglob("*.py"))
            ),
            "candidate_sha256": runner.code_hash(
                list((runner.ROOT / "src/devstill").rglob("*.py"))
            ),
            "dataset_sha256": runner.fingerprint(dataset),
            "index_manifest_sha256": runner.fingerprint(manifest),
            "index_policy": "Read-only frozen vectors; retrieval/storage query changes do not rebuild embeddings. Schema and index writer unchanged.",
            "variant_order": "Rotated per task, 3 timed warm repetitions per query engine; one untimed warmup.",
            "pool": 12,
            "maximum_context_chunks": 5,
            "context_tokenizer_model": llm.model,
            "latency_note": "Search time excludes selection and tokenizer HTTP. Context counts are not full-prompt counts.",
        },
    )
    rows = []
    with runner.index_lock(root), httpx.Client(headers=headers, timeout=30) as http:
        new = HybridSearch(args.config, config)
        old = baseline_search.HybridSearch(
            args.config, config, embedder=new.embedder, chroma_store=new.chroma
        )
        new.embedder.warm()
        cache = {}

        def count(rs):
            prompt = json.dumps([context_record(r) for r in rs], ensure_ascii=False)
            if prompt not in cache:
                response = http.post(
                    llm.base_url.removesuffix("/v1") + "/tokenize",
                    json={"model": llm.model, "prompt": prompt},
                )
                response.raise_for_status()
                cache[prompt] = int(response.json()["count"])
            return cache[prompt]

        try:
            for index, case in enumerate(dataset["cases"]):
                budget = (
                    4096
                    if case.get("workload", "lookup") in {"feature", "new_feature"}
                    else 2048
                )
                pools, timings = {}, {}
                engines = [("baseline", old), ("candidate", new)]
                if index % 2:
                    engines.reverse()
                for name, engine in engines:
                    options = {"limit": 12, **case.get("filters", {})}
                    engine.query(case["query"], **options)
                    samples = []
                    for _ in range(3):
                        started = time.perf_counter()
                        pools[name] = engine.query(case["query"], **options)
                        samples.append((time.perf_counter() - started) * 1000)
                    timings[name] = samples
                tests = query_requests_tests(case["query"])
                variants = [
                    (
                        "baseline",
                        pools["baseline"],
                        baseline_context.select_adaptive_context_results,
                        False,
                    ),
                    (
                        "test_eligibility",
                        pools["baseline"],
                        baseline_context.select_adaptive_context_results,
                        tests,
                    ),
                    (
                        "scoped_anchors",
                        pools["candidate"],
                        baseline_context.select_adaptive_context_results,
                        tests,
                    ),
                    (
                        "packed",
                        pools["candidate"],
                        select_adaptive_context_results,
                        tests,
                    ),
                ]
                for name, pool, select, include in variants:
                    started = time.perf_counter()
                    options = {"min_limit": 3, "max_limit": 5, "include_tests": include}
                    if name == "packed":
                        selected = select(
                            pool,
                            **options,
                            fits=lambda rs, cap=budget: count(rs) <= cap,
                        )
                    else:
                        selected = []
                        for hit in select(pool, **options):
                            if count([*selected, hit]) <= budget:
                                selected.append(hit)
                    elapsed = time.perf_counter() - started
                    hits, context = (
                        list(map(runner.serialize, pool)),
                        list(map(runner.serialize, selected)),
                    )
                    rows.append(
                        {
                            "id": case["id"],
                            "workload": case.get(
                                "workload",
                                "lookup" if case["answerable"] else "no_answer",
                            ),
                            "variant": name,
                            "query": case["query"],
                            "budget": budget,
                            "include_tests": include,
                            "pool": hits,
                            "context": context,
                            "context_tokens": count(selected),
                            "metrics": runner.score(case, hits, context),
                            "search_ms": timings[
                                "candidate"
                                if name in {"packed", "scoped_anchors"}
                                else "baseline"
                            ],
                            "selection_with_tokenizer_seconds": elapsed,
                        }
                    )
                    with (args.output / "queries.jsonl").open("a") as f:
                        f.write(json.dumps(rows[-1]) + "\n")
                print(case["id"], "complete", flush=True)
        finally:
            old.close()
            new.close()
    summary = {}
    for workload in sorted({r["workload"] for r in rows}):
        summary[workload] = {}
        for variant in ["baseline", "test_eligibility", "scoped_anchors", "packed"]:
            group = [
                r for r in rows if r["workload"] == workload and r["variant"] == variant
            ]
            times = [statistics.median(r["search_ms"]) for r in group]
            summary[workload][variant] = {
                "cases": len(group),
                "metrics": {
                    key: statistics.mean(r["metrics"][key] for r in group)
                    for key in group[0]["metrics"]
                },
                "median_context_tokens": statistics.median(
                    r["context_tokens"] for r in group
                ),
                "median_search_ms": statistics.median(times),
                "p95_per_query_median_search_ms": runner.percentile(times, 0.95),
            }
    runner.write_json(args.output / "summary.json", summary)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--config",
        type=Path,
        default=Path("/tmp/devstill-retrieval-bench/configs/baseline.json"),
    )
    p.add_argument(
        "--dataset", type=Path, default=runner.HERE / "werkzeug.changes.v2_1.json"
    )
    p.add_argument("--baseline", type=Path, required=True)
    p.add_argument("--agent-config", type=Path, default=Path("devstill.json"))
    p.add_argument("--output", type=Path, required=True)
    run(p.parse_args())
