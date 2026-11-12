"""Apply a versioned label correction uniformly to already captured contexts."""

import argparse
import json
import statistics
from pathlib import Path

from benchmarks.retrieval import runner


def rescore(input_path, dataset_path, repo, output):
    dataset = json.loads(dataset_path.read_text())
    runner.check_repo(repo, dataset["corpus_commit"])
    runner.validate_dataset(dataset, repo)
    cases = {c["id"]: c for c in dataset["cases"]}
    rows = []
    for line in input_path.read_text().splitlines():
        row = json.loads(line)
        case = cases[row["id"]]
        if row["query"] != case["query"]:
            raise ValueError("Rescoring cannot change the retrieval query")
        row["metrics"] = runner.score(case, row["pool"], row["context"])
        row["required_roles"] = case["roles"]
        row["covered_roles"] = [
            any(runner.covers(t, row["context"]) for t in group)
            for group in case["evidence_groups"]
        ]
        rows.append(row)
    summary = {}
    for workload in sorted({r["workload"] for r in rows}):
        summary[workload] = {}
        for variant in sorted({r["variant"] for r in rows}):
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
                "median_rewrite_seconds": statistics.median(
                    r["rewrite_seconds"] for r in group
                ),
            }
    output.mkdir(parents=True, exist_ok=False)
    runner.write_json(output / "dataset.json", dataset)
    runner.write_json(
        output / "provenance.json",
        {
            "input_sha256": runner.digest(input_path.read_bytes()),
            "dataset_sha256": runner.fingerprint(dataset),
            "changes": dataset.get("label_corrections", []),
        },
    )
    (output / "queries.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    runner.write_json(output / "summary.json", summary)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--input", type=Path, required=True)
    p.add_argument("--dataset", type=Path, required=True)
    p.add_argument(
        "--repo",
        type=Path,
        default=runner.DEFAULT_WORKSPACE / "repos" / runner.CORPUS["id"],
    )
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    rescore(a.input, a.dataset, a.repo, a.output)
