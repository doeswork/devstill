# ruff: noqa: B023
# Callbacks run synchronously within each case; none escape the loop.
"""Frozen-index comparison of legacy Plan and Task Review; no corpus edits."""

from __future__ import annotations

import argparse
import json
import statistics
import time
from pathlib import Path

from benchmarks.retrieval import runner
from devstill.agent import AgentClient
from devstill.config import load_config, resolve_index_path, resolve_repo_root
from devstill.context import (
    context_record,
    query_requests_tests,
    select_adaptive_context_results,
)
from devstill.planning import generate_plan, select_planning_context
from devstill.search import HybridSearch
from devstill.task_execution import source_view
from devstill.tasks import create_task_draft, prompt_fits, task_messages


def run(args):
    args.output.mkdir(parents=True, exist_ok=False)
    llm = load_config(args.agent_config).llm.model_copy(
        update={"planning_max_output_tokens": 4096, "max_output_tokens": 4096}
    )
    runner.write_json(
        args.output / "provenance.json",
        {
            "source_sha256": runner.code_hash(
                list((runner.ROOT / "src/devstill").rglob("*.py"))
            ),
            "model": llm.model,
            "input_cap": llm.planning_input_tokens,
            "output_cap": 4096,
            "note": "Exposed development sets; current compact Task schema, pool12/chunks5. No generated code is executed. Historical v1 results used the frozen prior source.",
            "order": "Alternated per case; seven warm searches per variant; one definition generation per variant except legacy schema repairs.",
        },
    )
    rows = []
    for corpus, config_path, dataset_path in [
        (
            "werkzeug",
            Path("/tmp/devstill-retrieval-bench/configs/baseline.json"),
            Path("benchmarks/retrieval/werkzeug.changes.v2_1.json"),
        ),
        (
            "cargo",
            Path("/tmp/devstill-retrieval-bench/configs/cargo-final.json"),
            Path("benchmarks/retrieval/cargo.pilot.v1.json"),
        ),
    ]:
        if args.corpus and corpus != args.corpus:
            continue
        config = load_config(config_path)
        repo = resolve_repo_root(config_path, config)
        dataset = json.loads(dataset_path.read_text())
        runner.check_repo(repo, dataset["corpus_commit"])
        previous_corpus = runner.CORPUS
        runner.CORPUS = {**previous_corpus, "commit": dataset["corpus_commit"]}
        try:
            runner.validate_dataset(dataset, repo)
        finally:
            runner.CORPUS = previous_corpus
        runner.write_json(args.output / f"{corpus}-dataset.json", dataset)
        root = resolve_index_path(config_path, config).parent
        with runner.index_lock(root):
            start = time.perf_counter()
            engine = HybridSearch(config_path, config)
            engine.embedder.warm()
            setup = time.perf_counter() - start
            try:
                for index, case in enumerate(dataset["cases"]):
                    order = ["old", "task"] if index % 2 == 0 else ["task", "old"]
                    for variant in order:
                        row = {
                            "corpus": corpus,
                            "id": case["id"],
                            "workload": case.get("workload", "lookup"),
                            "variant": variant,
                            "query": case["query"],
                            "setup_warm_seconds": setup,
                        }
                        limit = 12
                        engine.query(
                            case["query"], limit=limit, **case.get("filters", {})
                        )
                        samples = []
                        for _ in range(7):
                            start = time.perf_counter()
                            pool = engine.query(
                                case["query"], limit=limit, **case.get("filters", {})
                            )
                            samples.append(1000 * (time.perf_counter() - start))
                        row["search_ms"] = samples
                        row["pool"] = [runner.serialize(r) for r in pool]
                        log = args.output / f"{corpus}-{case['id']}-{variant}.jsonl"
                        with AgentClient(llm, log_path=log) as client:
                            count_cache = {}

                            def context_count(results):
                                payload = json.dumps(
                                    [context_record(r) for r in results],
                                    ensure_ascii=False,
                                )
                                if payload not in count_cache:
                                    response = client.client.post(
                                        llm.base_url.removesuffix("/v1") + "/tokenize",
                                        headers=client._headers(),
                                        json={"model": llm.model, "prompt": payload},
                                    )
                                    response.raise_for_status()
                                    count_cache[payload] = int(response.json()["count"])
                                return count_cache[payload]

                            budget = (
                                4096
                                if case.get("workload") in {"feature", "new_feature"}
                                or corpus == "cargo"
                                else 2048
                            )
                            start = time.perf_counter()
                            matched = select_adaptive_context_results(
                                pool,
                                min_limit=3,
                                max_limit=5,
                                include_tests=query_requests_tests(case["query"]),
                                fits=lambda rs: context_count(rs) <= budget,
                            )
                            row["matched_selection_seconds"] = (
                                time.perf_counter() - start
                            )
                            row["matched_context"] = [
                                runner.serialize(r) for r in matched
                            ]
                            row["matched_metrics"] = runner.score(
                                case, row["pool"], row["matched_context"]
                            )
                            start = time.perf_counter()
                            if variant == "old":
                                selected = select_planning_context(
                                    pool,
                                    client=client,
                                    query=case["query"],
                                    repository=str(repo),
                                )
                            else:
                                selected = select_adaptive_context_results(
                                    pool,
                                    min_limit=3,
                                    max_limit=5,
                                    include_tests=True,
                                    fits=lambda rs: prompt_fits(
                                        client,
                                        task_messages(
                                            case["query"],
                                            [context_record(r) for r in rs],
                                        ),
                                        4096,
                                    ),
                                )
                            row["native_selection_seconds"] = (
                                time.perf_counter() - start
                            )
                            row["context"] = [runner.serialize(r) for r in selected]
                            row["native_metrics"] = runner.score(
                                case, row["pool"], row["context"]
                            )
                            start = time.perf_counter()
                            try:
                                if variant == "old":
                                    output = generate_plan(
                                        client,
                                        query=case["query"],
                                        repository=str(repo),
                                        repo_root=repo,
                                        results=selected,
                                        retrieval_exclude=config.retrieval.exclude,
                                    )
                                    row["output"] = output.model_dump(mode="json")
                                    row["unresolved"] = sum(
                                        i.label.value == "Search" for i in output.items
                                    )
                                    row["tasks"] = 1
                                else:
                                    output = create_task_draft(
                                        client,
                                        query=case["query"],
                                        repo=repo,
                                        candidates=pool,
                                        mode="review",
                                        excluded=config.retrieval.exclude,
                                    )
                                    row["output"] = output.model_dump(mode="json")
                                    row["unresolved"] = len(output.plan.unresolved)
                                    row["tasks"] = len(output.plan.tasks)
                                    views = []
                                    for task in output.plan.tasks:
                                        for item in task.items:
                                            path = repo / item.target
                                            if (
                                                item.label.value
                                                not in {"Search", "New"}
                                                and path.is_file()
                                            ):
                                                views.append(
                                                    source_view(
                                                        item.target,
                                                        path.read_text(),
                                                        task,
                                                        output,
                                                    )
                                                )
                                    views = [
                                        {"path": view["path"], **span}
                                        for view in views
                                        for span in view.get("spans", [view])
                                    ]
                                    row["coding_view_metrics"] = runner.score(
                                        case, row["pool"], views
                                    )
                                row["valid"] = True
                            except Exception as exc:  # noqa: BLE001 - preserve failures in benchmark reports
                                row.update(
                                    valid=False,
                                    error=str(exc),
                                    error_type=type(exc).__name__,
                                )
                            row["definition_seconds"] = time.perf_counter() - start
                        logs = (
                            [json.loads(line) for line in log.read_text().splitlines()]
                            if log.exists()
                            else []
                        )
                        row["http_attempts"] = len(logs)
                        row["prompt_tokens"] = sum(
                            (r.get("usage") or {}).get("prompt_tokens", 0) for r in logs
                        )
                        row["output_tokens"] = sum(
                            (r.get("usage") or {}).get("completion_tokens", 0)
                            for r in logs
                        )
                        row["request_to_review_seconds"] = (
                            statistics.median(samples) / 1000
                            + row["native_selection_seconds"]
                            + row["definition_seconds"]
                        )
                        row["usable_complete"] = bool(
                            row["valid"]
                            and row.get("unresolved") == 0
                            and row["native_metrics"].get("context_complete")
                        )
                        rows.append(row)
                        with (args.output / "queries.jsonl").open("a") as f:
                            f.write(json.dumps(row) + "\n")
                        print(
                            corpus,
                            case["id"],
                            variant,
                            "valid",
                            row["valid"],
                            "coverage",
                            row["native_metrics"].get("context_evidence_recall"),
                            "seconds",
                            round(row["request_to_review_seconds"], 2),
                            flush=True,
                        )
            finally:
                engine.close()
    summarize(rows, args.output)


def summarize(rows, output):
    summary = {}
    for corpus in ["werkzeug", "cargo"]:
        summary[corpus] = {}
        for workload in sorted({r["workload"] for r in rows if r["corpus"] == corpus}):
            summary[corpus][workload] = {}
            for variant in ["old", "task"]:
                rs = [
                    r
                    for r in rows
                    if r["corpus"] == corpus
                    and r["workload"] == workload
                    and r["variant"] == variant
                ]
                result = {
                    "cases": len(rs),
                    "valid": sum(r["valid"] for r in rs),
                    "usable_complete": sum(r["usable_complete"] for r in rs),
                    "unresolved_or_invalid": sum(
                        not r["valid"] or r.get("unresolved", 0) > 0 for r in rs
                    ),
                }
                for stage in ["matched", "native"]:
                    positive = [
                        r
                        for r in rs
                        if "context_evidence_recall" in r[stage + "_metrics"]
                    ]
                    if positive:
                        result[stage + "_coverage"] = statistics.mean(
                            r[stage + "_metrics"]["context_evidence_recall"]
                            for r in positive
                        )
                        result[stage + "_complete"] = sum(
                            r[stage + "_metrics"]["context_complete"] for r in positive
                        )
                for key in [
                    "request_to_review_seconds",
                    "prompt_tokens",
                    "output_tokens",
                    "http_attempts",
                ]:
                    result[key + "_median"] = statistics.median(r[key] for r in rs)
                result["warm_search_ms_median"] = statistics.median(
                    statistics.median(r["search_ms"]) for r in rs
                )
                result["warm_search_ms_p95_per_query_medians"] = runner.percentile(
                    [statistics.median(r["search_ms"]) for r in rs], 0.95
                )
                summary[corpus][workload][variant] = result
    runner.write_json(output / "summary.json", summary)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--agent-config", type=Path, default=Path("devstill.json"))
    p.add_argument("--corpus", choices=["werkzeug", "cargo"])
    p.add_argument("--output", type=Path, required=True)
    run(p.parse_args())
