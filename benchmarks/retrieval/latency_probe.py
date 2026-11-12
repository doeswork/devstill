"""Measure fresh-process startup separately from repeated warm search."""

from __future__ import annotations

import argparse
import json
import resource
import statistics
import subprocess
import sys
import time
from pathlib import Path


def child(config_path, query):
    started = time.perf_counter()
    from devstill.config import load_config
    from devstill.search import HybridSearch

    config = load_config(config_path)
    search = HybridSearch(config_path, config)
    opened = time.perf_counter()
    try:
        results = search.query(query, limit=12)
        first_done = time.perf_counter()
        samples = []
        for _ in range(3):
            before = time.perf_counter()
            search.query(query, limit=12)
            samples.append((time.perf_counter() - before) * 1000)
        return {
            "setup_seconds": opened - started,
            "first_search_seconds": first_done - opened,
            "warm_search_ms": samples,
            "peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            "result_count": len(results),
        }
    finally:
        search.close()


def run(args):
    if args.child:
        print(json.dumps(child(args.config, args.query)))
        return
    if args.repeats < 1:
        raise ValueError("repeats must be positive")
    rows = []
    for _ in range(args.repeats):
        started = time.perf_counter()
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "benchmarks.retrieval.latency_probe",
                "--child",
                "--config",
                str(args.config),
                "--query",
                args.query,
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        row = json.loads(result.stdout)
        row["process_wall_seconds"] = time.perf_counter() - started
        rows.append(row)
    report = {
        "config": str(args.config),
        "query": args.query,
        "samples": rows,
        "median_process_wall_seconds": statistics.median(
            r["process_wall_seconds"] for r in rows
        ),
        "note": "Fresh Python processes; OS/model file caches may be warm. Wall time includes three follow-up warm queries and process cleanup. No LLM or tokenization.",
    }
    with args.output.open("x") as f:
        json.dump(report, f, indent=2)
        f.write("\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--config", type=Path, required=True)
    p.add_argument("--query", required=True)
    p.add_argument("--child", action="store_true")
    p.add_argument("--repeats", type=int, default=3)
    p.add_argument("--output", type=Path)
    args = p.parse_args()
    if not args.child and args.output is None:
        p.error("--output is required")
    run(args)
