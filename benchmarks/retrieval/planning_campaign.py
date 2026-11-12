"""Exercise Qwen planning with saved candidate pools, without executing changes."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import httpx

from benchmarks.retrieval import runner
from devstill.agent import AgentClient, AgentError
from devstill.config import load_config, resolve_index_path, resolve_repo_root
from devstill.models import SearchResult
from devstill.paths import IndexPaths
from devstill.planning import generate_plan, render_plan_json, select_planning_context
from devstill.storage import SQLiteStore


def run(args):
    config = load_config(args.config)
    repo = resolve_repo_root(args.config, config)
    llm = load_config(args.agent_config).llm
    args.output.mkdir(parents=True, exist_ok=False)
    rows = [json.loads(line) for line in args.input.read_text().splitlines()]
    rows = [r for r in rows if r["variant"] == "packed"]
    store = SQLiteStore(
        IndexPaths.from_chroma_path(resolve_index_path(args.config, config)).sqlite
    )
    runner.check_repo(repo, runner.CORPUS["commit"])
    results = []
    try:
        with AgentClient(llm, log_path=args.output / "agent.jsonl") as client:
            for row in rows:
                chunks = store.get_chunks_by_ids([r["id"] for r in row["pool"]])
                candidates = [
                    SearchResult(chunks[r["id"]], r["score"], r["channels"])
                    for r in row["pool"]
                ]
                started = time.perf_counter()
                record = {
                    "id": row["id"],
                    "query": row["query"],
                    "review_status": "needs independent semantic review",
                }
                try:
                    selected = select_planning_context(
                        candidates,
                        client=client,
                        query=row["query"],
                        repository=str(repo),
                    )
                    plan = generate_plan(
                        client,
                        query=row["query"],
                        repository=str(repo),
                        repo_root=repo,
                        results=selected,
                    )
                    record.update(
                        {
                            "valid": True,
                            "selected_context": [runner.serialize(r) for r in selected],
                            "plan": json.loads(render_plan_json(plan)),
                        }
                    )
                except (AgentError, httpx.HTTPError, ValueError, TypeError) as exc:
                    record.update(
                        {
                            "valid": False,
                            "error": str(exc),
                            "error_type": type(exc).__name__,
                        }
                    )
                record["seconds"] = time.perf_counter() - started
                results.append(record)
                with (args.output / "plans.jsonl").open("a") as f:
                    f.write(json.dumps(record) + "\n")
                print(row["id"], record["valid"], flush=True)
    finally:
        store.close()
    runner.write_json(
        args.output / "summary.json",
        {
            "cases": len(results),
            "valid": sum(r["valid"] for r in results),
            "model": llm.model,
            "input_budget": llm.planning_input_tokens,
            "context_window": llm.context_window,
            "note": "Schema/path validation and actual request diagnostics only; no code execution or semantic plan-quality certification.",
        },
    )


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--input", type=Path, required=True)
    p.add_argument(
        "--config",
        type=Path,
        default=Path("/tmp/devstill-retrieval-bench/configs/baseline.json"),
    )
    p.add_argument("--agent-config", type=Path, default=Path("devstill.json"))
    p.add_argument("--output", type=Path, required=True)
    run(p.parse_args())
