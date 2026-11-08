"""Isolated ablation: Quick-selected evidence followed by unmodified native Pi."""

from __future__ import annotations

import json
import time

from benchmarks.retrieval import coding_campaign as campaign
from benchmarks.retrieval import pi_comparison as comparison


def append_context(query, records):
    return (
        query
        + (
            "\n\nRepository excerpts retrieved for this request "
            "(context only; use your normal tools to verify or explore further):\n"
        )
        + json.dumps({"source": records}, ensure_ascii=False)
    )


def retrieve_context(args, runtime, query):
    from devstill.agent import AgentClient
    from devstill.config import DevstillConfig, load_config, resolve_index_path
    from devstill.context import context_record
    from devstill.planning import select_planning_context
    from devstill.search import HybridSearch
    from devstill.tasks import digest_file

    config_path = campaign.BENCH / "configs" / campaign.CORPORA[args.corpus][1]
    cfg = load_config(config_path)
    cfg.llm = DevstillConfig.model_validate(runtime).llm
    started = time.perf_counter()
    private = args.output / "private-index"
    campaign.copy_index(resolve_index_path(config_path, cfg), private)
    copy_seconds = time.perf_counter() - started
    cfg.repo.root = str(args.repo)
    cfg.index.path = str(private / "chroma")
    engine = HybridSearch(config_path, cfg)
    try:
        engine.embedder.warm()
        setup_seconds = time.perf_counter() - started
        retrieval_started = time.perf_counter()
        pool = engine.query(query, limit=12)
        retrieval_seconds = time.perf_counter() - retrieval_started
        comparison.write(args.output / "pool.json", [r.to_dict() for r in pool])
        selection_started = time.perf_counter()
        with AgentClient(cfg.llm) as client:
            # This is Quick's actual evidence packing, without generate_plan().
            # Its /tokenize calls, when enabled, are captured by the HTTP recorder.
            selected = select_planning_context(
                pool, client=client, query=query, repository=str(args.repo)
            )
        for result in selected:
            if (
                result.source_hash
                and digest_file(args.repo / result.chunk.path) != result.source_hash
            ):
                raise ValueError(f"Stale retrieved source: {result.chunk.path}")
        records = [context_record(r) for r in selected]
        comparison.write(args.output / "selected-context.json", records)
        return records, {
            "index_copy_seconds": copy_seconds,
            "setup_seconds": setup_seconds,
            "retrieval_seconds": retrieval_seconds,
            "context_selection_seconds": time.perf_counter() - selection_started,
        }
    finally:
        engine.close()


def context_worker(args):
    runtime = json.loads(args.runtime.read_text())
    query = args.query.read_text()
    case = {
        "id": args.case[0],
        "query": query,
        "corpus": args.corpus,
        "workload": args.workload,
    }
    started = time.perf_counter()
    try:
        records, timing = retrieve_context(args, runtime, query)
        augmented = append_context(query, records)
        (args.output / "pi-input.txt").write_text(augmented)
        # The exact same stock-Pi executor handles the control and context arms.
        # It retains read/bash/edit/write, its own system prompt, and no scoped extension.
        comparison.direct_pi(
            {**case, "query": augmented}, args.repo, args.output, runtime
        )
        row = json.loads((args.output / "result.json").read_text())
        row["coding_seconds"] += (
            timing["retrieval_seconds"] + timing["context_selection_seconds"]
        )
        row.update(timing)
        row["context_records"] = len(records)
        row["context_characters"] = len(augmented) - len(query)
        row["execution"]["engine"] = "stock_pi_with_retrieved_context"
    except Exception as exc:  # noqa: BLE001 - preserve retrieval and coding failures
        row = {
            "id": case["id"],
            "workload": case["workload"],
            "corpus": case["corpus"],
            "error": str(exc),
            "coding_seconds": time.perf_counter() - started,
            "execution": {
                "engine": "stock_pi_with_retrieved_context",
                "status": "failed",
            },
        }
    row["variant"] = "pi_context"
    comparison.write(args.output / "result.json", row)
