"""Isolated design probes; no production behavior is patched or registered."""

from __future__ import annotations

import argparse
import ast
import json
import re
import statistics
import textwrap
import time
from collections import defaultdict
from pathlib import Path

import httpx

from benchmarks.retrieval import runner
from devstill.agent import AgentClient, extract_json_object
from devstill.config import load_config
from devstill.context import context_record, select_adaptive_context_results
from devstill.credentials import resolve_api_key
from devstill.models import SearchResult
from devstill.search import HybridSearch


def is_overload(chunk) -> bool:
    # Canonical chunks can start at a decorator with the class indentation
    # retained on following lines; parser metadata avoids reparsing that fragment.
    decorators = chunk.parser_metadata.get("decorators", [])
    if any(d.rsplit(".", 1)[-1] == "overload" for d in decorators):
        return True
    try:
        tree = ast.parse(textwrap.dedent(chunk.code))
    except SyntaxError:
        return False
    functions = [
        n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    return bool(functions) and all(
        any(
            isinstance(d, ast.Name)
            and d.id == "overload"
            or isinstance(d, ast.Attribute)
            and d.attr == "overload"
            for d in n.decorator_list
        )
        for n in functions
    )


def canonicalize(results, implementations):
    output, seen = [], set()
    for result in results:
        chunk = result.chunk
        if is_overload(chunk):
            chunk = implementations.get((chunk.path, chunk.qualname), chunk)
        key = (chunk.path, chunk.start_line, chunk.end_line)
        if key not in seen:
            seen.add(key)
            output.append(SearchResult(chunk, result.final_score, result.scores))
    return output


def identifiers(query):
    return list(
        dict.fromkeys(
            re.findall(
                r"\b[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+\b|\b[A-Za-z_]\w*_\w+\b", query
            )
        )
    )[:6]


def fuse(groups):
    scores, candidates = defaultdict(float), {}
    for group in groups:
        seen = set()
        for rank, result in enumerate(group, 1):
            if result.chunk.id in seen:
                continue
            seen.add(result.chunk.id)
            candidates.setdefault(result.chunk.id, result)
            scores[result.chunk.id] += 1 / (60 + rank)
    return [
        SearchResult(candidates[key].chunk, scores[key], candidates[key].scores)
        for key in sorted(scores, key=lambda key: (-scores[key], key))
    ]


def pack(results, token_count, budget, limit):
    selected = []
    for result in results:
        if len(selected) >= limit:
            break
        # Do not pay twice for near-identical source ranges.
        c = result.chunk
        duplicate = any(
            p.chunk.path == c.path
            and max(
                0,
                min(p.chunk.end_line, c.end_line)
                - max(p.chunk.start_line, c.start_line)
                + 1,
            )
            / min(
                c.end_line - c.start_line + 1, p.chunk.end_line - p.chunk.start_line + 1
            )
            >= 0.8
            for p in selected
        )
        if not duplicate and token_count(selected + [result]) <= budget:
            selected.append(result)
    return selected


def run(args):
    config, repo, index_root, identity = runner.index_identity(args.config)
    data = json.loads(args.dataset.read_text())
    runner.validate_dataset(data, repo)
    llm = load_config(args.agent_config).llm
    source_files = list((runner.ROOT / "src/devstill").rglob("*.py"))
    source_hash = runner.code_hash(source_files)
    args.output.mkdir(parents=True, exist_ok=False)
    runner.write_json(args.output / "dataset.json", data)
    runner.write_json(
        args.output / "preregistered.json",
        {
            "dataset_sha256": runner.fingerprint(data),
            "source_sha256": source_hash,
            "prototype_sha256": runner.digest(Path(__file__).read_bytes()),
            "rewrites_source_sha256": runner.digest(args.reuse_rewrites.read_bytes())
            if args.reuse_rewrites
            else None,
            "tokenizer_model": llm.model,
            "tokenizer_endpoint": llm.base_url.removesuffix("/v1") + "/tokenize",
            "variants": ["current", "opt_in_wide", "canonical_anchors", "model_facets"],
            "context_token_budgets": {
                "small_edit": 2048,
                "feature": 4096,
                "new_feature": 4096,
                "no_answer": 2048,
            },
            "note": "Exploratory design cases, not held-out evaluation. Qwen receives queries only for decomposition. Evidence labels are used only for scoring; author-provided workload labels select budgets and caps, so routing is not evaluated.",
        },
    )
    key = resolve_api_key(llm)
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    rows = []
    reused = {}
    if args.reuse_rewrites:
        for line in args.reuse_rewrites.read_text().splitlines():
            row = json.loads(line)
            if row["variant"] == "model_facets":
                reused[row["query"]] = row
    with (
        runner.index_lock(index_root),
        httpx.Client(timeout=30, headers=headers) as http,
        AgentClient(llm, log_path=args.output / "rewrites.jsonl") as agent,
    ):
        manifest = json.loads((index_root / "benchmark-index.json").read_text())
        if manifest["identity_sha256"] != runner.fingerprint(identity):
            raise ValueError("Index identity changed")
        search = HybridSearch(args.config, config)
        search.embedder.warm()
        symbols = search.store.list_symbol_chunks()
        implementations = {
            (c.path, c.qualname): c
            for c in symbols
            if c.kind in {"function", "method"} and not is_overload(c)
        }
        cache = {}

        def count(results):
            prompt = json.dumps(
                [context_record(r) for r in results], ensure_ascii=False
            )
            if prompt not in cache:
                response = http.post(
                    llm.base_url.removesuffix("/v1") + "/tokenize",
                    json={"model": llm.model, "prompt": prompt},
                )
                response.raise_for_status()
                cache[prompt] = int(response.json()["count"])
            return cache[prompt]

        try:
            for case in data["cases"]:
                query = case["query"]
                feature = case["workload"] in {"feature", "new_feature"}
                budget, limit = (4096, 8) if feature else (2048, 5)
                t = time.perf_counter()
                base = search.query(query, limit=12)
                current_ms = (time.perf_counter() - t) * 1000
                t = time.perf_counter()
                wide = search.query(query, limit=20)
                wide_ms = (time.perf_counter() - t) * 1000
                t = time.perf_counter()
                anchored = []
                for name in identifiers(query):
                    chunks = search.store.get_chunks_by_ids(
                        search.store.find_symbol_exact(name)
                    )
                    anchored.extend(
                        SearchResult(c, 1, {"exact": 1}) for c in chunks.values()
                    )
                canonical = canonicalize(anchored + wide, implementations)
                canonical_ms = wide_ms + (time.perf_counter() - t) * 1000
                if query in reused:
                    queries = reused[query]["generated_queries"]
                    model_seconds = reused[query]["rewrite_seconds"]
                else:
                    t = time.perf_counter()
                    content = agent.chat(
                        [
                            {
                                "role": "system",
                                "content": "Return JSON with queries: up to three short repository search queries for implementation, integration, and tests/docs needed for the user's change. Use only identifiers supplied by the user. Do not invent filenames or APIs. Preserve the requested scope. No code, no answers, only search queries.",
                            },
                            {"role": "user", "content": query},
                        ],
                        schema={
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "queries": {
                                    "type": "array",
                                    "maxItems": 3,
                                    "items": {"type": "string", "maxLength": 240},
                                }
                            },
                            "required": ["queries"],
                        },
                        max_output_tokens=384,
                        reasoning="off",
                    )
                    queries = extract_json_object(content)["queries"]
                    if (
                        not isinstance(queries, list)
                        or len(queries) > 3
                        or any(not isinstance(q, str) or len(q) > 240 for q in queries)
                    ):
                        raise ValueError("Invalid facet queries")
                    model_seconds = time.perf_counter() - t
                t = time.perf_counter()
                groups = search.query_many(queries, limit=20)
                expanded = canonicalize(
                    anchored + fuse([wide, *groups]), implementations
                )
                facet_ms = canonical_ms + (time.perf_counter() - t) * 1000
                variants = [
                    ("current", base, False, 5, current_ms),
                    ("opt_in_wide", wide, True, limit, wide_ms),
                    ("canonical_anchors", canonical, True, limit, canonical_ms),
                    ("model_facets", expanded, True, limit, facet_ms),
                ]
                for name, pool, include_tests, maximum, local_ms in variants:
                    selected = select_adaptive_context_results(
                        pool,
                        min_limit=3,
                        max_limit=maximum,
                        include_tests=include_tests,
                    )
                    unbudgeted = list(selected)
                    # Current path gets only a budget cap, not prototype overlap removal.
                    if name == "current":
                        selected = []
                        for r in unbudgeted:
                            if count(selected + [r]) <= budget:
                                selected.append(r)
                    else:
                        selected = pack(selected, count, budget, maximum)
                    hits, ctx = (
                        list(map(runner.serialize, pool)),
                        list(map(runner.serialize, selected)),
                    )
                    metrics = runner.score(case, hits, ctx)
                    coverage = [
                        any(runner.covers(target, ctx) for target in group)
                        for group in case["evidence_groups"]
                    ]
                    row = {
                        "id": case["id"],
                        "workload": case["workload"],
                        "variant": name,
                        "query": query,
                        "budget": budget,
                        "context_tokens": count(selected),
                        "context_tokens_before_budget": count(unbudgeted),
                        "context_chunks": len(selected),
                        "metrics": metrics,
                        "required_roles": case["roles"],
                        "covered_roles": coverage,
                        "local_retrieval_ms_single_sample": local_ms,
                        "rewrite_seconds": model_seconds
                        if name == "model_facets"
                        else 0,
                        "generated_queries": queries if name == "model_facets" else [],
                        "rewrite_reused": query in reused
                        if name == "model_facets"
                        else False,
                        "pool": hits,
                        "context": ctx,
                    }
                    rows.append(row)
                    with (args.output / "queries.jsonl").open("a") as f:
                        f.write(json.dumps(row) + "\n")
                print(case["id"], "done", flush=True)
            # Targeted diagnostic: same original lookup, replacing stubs before selection.
            raw = search.query("MultiDict.getlist", limit=12)
            fixed = canonicalize(raw, implementations)
            runner.write_json(
                args.output / "overload_probe.json",
                {
                    name: [
                        runner.serialize(r)
                        for r in select_adaptive_context_results(
                            pool, min_limit=3, max_limit=5, include_tests=False
                        )
                    ]
                    for name, pool in [("current", raw), ("canonical", fixed)]
                },
            )
        finally:
            search.close()
    if runner.code_hash(source_files) != source_hash:
        raise ValueError("Production source changed during probes")
    summary = {}
    for workload in sorted({r["workload"] for r in rows}):
        summary[workload] = {}
        for variant in ("current", "opt_in_wide", "canonical_anchors", "model_facets"):
            group = [
                r for r in rows if r["workload"] == workload and r["variant"] == variant
            ]
            summary[workload][variant] = {
                "cases": len(group),
                "metrics": {
                    k: statistics.mean(r["metrics"][k] for r in group)
                    for k in group[0]["metrics"]
                },
                "median_context_tokens": statistics.median(
                    r["context_tokens"] for r in group
                ),
                "median_local_ms_single_sample": statistics.median(
                    r["local_retrieval_ms_single_sample"] for r in group
                ),
                "median_rewrite_seconds": statistics.median(
                    r["rewrite_seconds"] for r in group
                ),
            }
    runner.write_json(args.output / "summary.json", summary)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("/tmp/devstill-retrieval-bench/configs/baseline.json"),
    )
    parser.add_argument(
        "--dataset", type=Path, default=runner.HERE / "werkzeug.changes.v2_1.json"
    )
    parser.add_argument("--agent-config", type=Path, default=Path("devstill.json"))
    parser.add_argument("--reuse-rewrites", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    run(parser.parse_args())
