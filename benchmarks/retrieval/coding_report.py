"""Audit a finished campaign without model calls or changes to its artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import statistics
from pathlib import Path


def completed(row):
    execution = row.get("execution", {})
    if execution.get("engine") == "composed_quick" and (
        not execution.get("tasks")
        or any(
            t.get("status") not in {"validated", "applied_unvalidated"}
            for t in execution["tasks"].values()
        )
    ):
        return False
    return (
        row["acceptance_pass"]
        and not row.get("error")
        and row.get("execution", {}).get("status")
        in {"applied", "validated", "applied_unvalidated", "no_changes"}
    )


def summarize_features(rows, cases, repeats):
    expected = {
        (c["id"], repeat, v)
        for c in cases
        for repeat in range(repeats)
        for v in ("old", "feature")
    }
    observed = [(r["id"], r["repeat"], r["variant"]) for r in rows]
    report = {
        "complete": len(observed) == len(expected) and set(observed) == expected,
        "expected_attempts": len(expected),
        "workloads": {},
        "promotion": "development diagnostic only; fresh acceptance and aggregate-budget comparison still required",
    }
    for workload in sorted({c["workload"] for c in cases}):
        selected = [r for r in rows if r["workload"] == workload]
        group = {}
        for variant in ("old", "feature"):
            values = [r for r in selected if r["variant"] == variant]
            ids = {c["id"] for c in cases if c["workload"] == workload}
            group[variant] = {
                "attempts": len(values),
                "behavior_passes": sum(r["acceptance_pass"] for r in values),
                "workflow_passes": sum(completed(r) for r in values),
                "consistent_cases": sorted(
                    id
                    for id in ids
                    if len([r for r in values if r["id"] == id]) == repeats
                    and all(completed(r) for r in values if r["id"] == id)
                ),
                "composed_success_cases": sorted(
                    {
                        r["id"]
                        for r in values
                        if completed(r) and r.get("task_count", 0) >= 2
                    }
                ),
                "dependent_success_cases": sorted(
                    {
                        r["id"]
                        for r in values
                        if completed(r)
                        and r.get("task_count", 0) >= 2
                        and r.get("dependency_edges", 0) > 0
                    }
                ),
            }
        pairs = [
            (o, n)
            for o in selected
            if o["variant"] == "old"
            for n in selected
            if n["variant"] == "feature"
            and (o["id"], o["repeat"]) == (n["id"], n["repeat"])
            and completed(o)
            and completed(n)
        ]
        group["paired_successes"] = len(pairs)
        group["paired_medians"] = {
            metric: {
                v: statistics.median(p[i][metric] for p in pairs) if pairs else None
                for i, v in enumerate(("old", "feature"))
            }
            for metric in ("review_seconds", "coding_seconds", "total_seconds")
        }
        group["consistent_case_regressions"] = sorted(
            set(group["old"]["consistent_cases"])
            - set(group["feature"]["consistent_cases"])
        )
        report["workloads"][workload] = group
    return report


def audit_features(root):
    protocol = json.loads((root / "protocol.json").read_text())
    rows = json.loads((root / "results.json").read_text())
    cases = [
        c
        for c in json.loads((root / "cases.json").read_text())
        if c["id"] in protocol["case_ids"]
    ]
    report = summarize_features(rows, cases, protocol["repeats"])
    report["source_audit"] = json.loads((root / "final-audit.json").read_text())
    for row in rows:
        artifact = root / f"{row['id']}-{row['repeat']}-{row['variant']}" / "artifacts"
        direct, assistant, malformed = [], [], 0
        for path in artifact.rglob("*.jsonl"):
            if path.name not in {
                "definition.jsonl",
                "pi-execute.jsonl",
            } and not path.name.endswith("-definition.jsonl"):
                continue
            for line in path.read_text().splitlines():
                try:
                    event = json.loads(line)
                except ValueError:
                    malformed += 1
                    continue
                if path.name == "pi-execute.jsonl":
                    if (
                        event.get("type") == "message_end"
                        and event.get("message", {}).get("role") == "assistant"
                    ):
                        assistant.append(event["message"])
                else:
                    direct.append(event)
        row["workflow_pass"] = completed(row)
        row["logged_direct_http_attempts"] = len(direct)
        row["logged_pi_generations"] = len(assistant)
        row["logged_model_calls"] = len(direct) + len(assistant)
        row["known_direct_tokens"] = sum(
            (r.get("usage") or {}).get("total_tokens", 0) for r in direct
        )
        row["total_tokens"] = None  # Pi usage is not consistently available.
        row["incomplete_log_lines"] = malformed
    report["rows"] = rows
    return report


def summarize_quick(rows, case_ids, repeats):
    """A small-edit sanity comparison, not the historical feature promotion gate."""
    expected = {
        (case, repeat, variant)
        for case in case_ids
        for repeat in range(repeats)
        for variant in ("old", "quick")
    }
    observed = [(r["id"], r["repeat"], r["variant"]) for r in rows]
    by = {v: [r for r in rows if r["variant"] == v] for v in ("old", "quick")}
    pairs = [
        (o, n)
        for o in by["old"]
        for n in by["quick"]
        if (o["id"], o["repeat"]) == (n["id"], n["repeat"])
        and completed(o)
        and completed(n)
    ]
    return {
        "complete": len(observed) == len(expected) and set(observed) == expected,
        "expected_attempts": len(expected),
        "cases": len(case_ids),
        "repeats": repeats,
        "variants": {
            v: {
                "attempts": len(rs),
                "behavior_passes": sum(r["acceptance_pass"] for r in rs),
                "workflow_passes": sum(completed(r) for r in rs),
            }
            for v, rs in by.items()
        },
        "paired_successes": len(pairs),
        "paired_medians": {
            metric: {
                v: statistics.median(pair[i][metric] for pair in pairs)
                if pairs
                else None
                for i, v in enumerate(("old", "quick"))
            }
            for metric in ("review_seconds", "coding_seconds", "total_seconds")
        },
        "note": "Independent model draws on the same Quick algorithm. This sample does not establish statistical equivalence or feature quality; failed attempts are excluded from paired timings.",
    }


def audit_quick(root: Path):
    protocol = json.loads((root / "protocol.json").read_text())
    rows = json.loads((root / "results.json").read_text())
    report = summarize_quick(rows, protocol["case_ids"], protocol["repeats"])
    report["source_audit"] = json.loads((root / "final-audit.json").read_text())
    report["request_pairs"] = []

    def normalize(value):
        # Only disposable checkout names vary. Keep the actual query, source,
        # file order, prompt instructions and generation options intact.
        return re.sub(
            r"/tmp/devstill-coding-v2-[^/\s\"\\]+/repo",
            "<repo>",
            json.dumps(value, sort_keys=True),
        )

    for case in protocol["case_ids"]:
        for repeat in range(protocol["repeats"]):
            logs, pools = {}, {}
            for variant in ("old", "quick"):
                directory = root / f"{case}-{repeat}-{variant}"
                log = directory / "artifacts/definition.jsonl"
                pool = directory / "pool.json"
                logs[variant] = (
                    [json.loads(line) for line in log.read_text().splitlines()]
                    if log.exists()
                    else []
                )
                pools[variant] = json.loads(pool.read_text()) if pool.exists() else None
            keys = (
                "messages",
                "schema_name",
                "max_tokens",
                "reasoning_mode",
                "mode",
                "model",
                "endpoint",
            )
            summaries_match = bool(logs["old"] and logs["quick"]) and all(
                normalize(logs["old"][0].get(k)) == normalize(logs["quick"][0].get(k))
                for k in keys
            )
            full_messages = (
                bool(logs["old"] and logs["quick"])
                and all(
                    isinstance(m.get("content"), str)
                    for v in ("old", "quick")
                    for m in logs[v][0].get("messages", [])
                )
                and all(logs[v][0].get("messages") for v in ("old", "quick"))
            )
            report["request_pairs"].append(
                {
                    "id": case,
                    "repeat": repeat,
                    "same_retrieval_pool": pools["old"] is not None
                    and pools["old"] == pools["quick"],
                    "same_request_summary": summaries_match,
                    "same_initial_request": summaries_match if full_messages else None,
                    "request_comparison_limit": "Character-count summaries cannot establish prompt-content identity"
                    if not full_messages
                    else None,
                    "definition_calls": {v: len(log) for v, log in logs.items()},
                    "definition_schemas": {
                        v: [r["schema_name"] for r in log] for v, log in logs.items()
                    },
                }
            )
    return report


def audit(root: Path):
    rows = json.loads((root / "results.json").read_text())
    cases = json.loads((root / "cases.json").read_text())
    keys = [(r["id"], r["repeat"], r["variant"]) for r in rows]
    expected = {
        (c["id"], n, v) for c in cases for n in range(3) for v in ["old", "task"]
    }
    report = {
        "complete_144_attempts": len(keys) == 144 and set(keys) == expected,
        "workloads": {},
    }
    source_root = Path("/tmp/devstill-retrieval-bench/repos")
    repo_names = {"werkzeug": "werkzeug-3.1.3", "cargo": "cargo-0.83.0"}
    case_map = {c["id"]: c for c in cases}
    for row in rows:
        directory = root / f"{row['id']}-{row['repeat']}-{row['variant']}" / "artifacts"
        case = case_map[row["id"]]
        required = {
            e["path"]
            for e in case["edits"]
            if (source_root / repo_names[case["corpus"]] / e["path"]).is_file()
        }
        pool_file = directory.parent / "pool.json"
        if pool_file.exists():
            paths = {r["path"] for r in json.loads(pool_file.read_text())}
            row["reference_file_diagnostic"] = {
                "required_existing_files": sorted(required),
                "missing_from_pool": sorted(required - paths),
            }
        # Reference-path coverage is diagnostic only: alternative valid patches
        # and new extension points are assessed by behavior, not path matching.
        direct = [
            json.loads(line)
            for p in directory.rglob("*.jsonl")
            if p.name in {"agent.jsonl", "definition.jsonl"}
            for line in p.read_text().splitlines()
        ]
        pi = [
            json.loads(line)
            for p in directory.rglob("pi-execute.jsonl")
            for line in p.read_text().splitlines()
        ]
        assistant = [
            e["message"]
            for e in pi
            if e.get("type") == "message_end"
            and e.get("message", {}).get("role") == "assistant"
        ]
        row["direct_http_attempts"] = len(direct)
        row["logical_generations"] = len(direct) + len(assistant)
        row["direct_tokens"] = sum(
            (e.get("usage") or {}).get("total_tokens", 0) for e in direct
        )
        row["total_tokens"] = row["direct_tokens"] if row["variant"] == "task" else None
        row["definition_output_tokens"] = sum(
            (e.get("usage") or {}).get("completion_tokens", 0)
            for e in direct
            if e.get("schema_name") in {"devstill_tasks_compact_v2", "devstill_plan"}
        )
        row["workflow_pass"] = completed(row)
    for workload in ["small", "feature"]:
        group = [r for r in rows if r["workload"] == workload]
        values = {}
        for variant in ["old", "task"]:
            selected = [r for r in group if r["variant"] == variant]
            values[variant] = {
                "attempts": len(selected),
                "acceptance_pass": sum(r["acceptance_pass"] for r in selected),
                "workflow_pass": sum(r["workflow_pass"] for r in selected),
                "reliable_cases": [
                    c["id"]
                    for c in cases
                    if c["workload"] == workload
                    and sum(r["id"] == c["id"] and r["workflow_pass"] for r in selected)
                    == 3
                ],
            }
        pairs = []
        for old in group:
            if old["variant"] != "old" or not old["workflow_pass"]:
                continue
            new = next(
                (
                    r
                    for r in group
                    if (r["id"], r["repeat"], r["variant"])
                    == (old["id"], old["repeat"], "task")
                ),
                None,
            )
            if new and new["workflow_pass"]:
                pairs.append((old, new))
        values["paired_successes"] = len(pairs)
        values["paired_case_median_ratios"] = {}
        for metric in ["review_seconds", "total_seconds"]:
            per_case = [
                statistics.median(
                    n[metric] / o[metric] for o, n in pairs if o["id"] == id
                )
                for id in {o["id"] for o, n in pairs}
            ]
            values["paired_case_median_ratios"][metric] = (
                statistics.median(per_case) if per_case else None
            )
            values["paired_" + metric] = {
                v: statistics.median(pair[i][metric] for pair in pairs)
                if pairs
                else None
                for i, v in enumerate(["old", "task"])
            }
        values["reliable_case_regressions"] = sorted(
            set(values["old"]["reliable_cases"]) - set(values["task"]["reliable_cases"])
        )
        values["gate_pass"] = (
            report["complete_144_attempts"]
            and not values["reliable_case_regressions"]
            and values["task"]["workflow_pass"] >= values["old"]["workflow_pass"]
            and all(
                r is not None and r <= 1
                for r in values["paired_case_median_ratios"].values()
            )
        )
        report["workloads"][workload] = values
    report["promotion"] = (
        "eligible for review"
        if all(v["gate_pass"] for v in report["workloads"].values())
        else "do not promote"
    )
    baseline = Path(__file__).resolve().parents[2] / ".devstill/task-v2-baseline"
    manifest = json.loads((baseline / "manifest.json").read_text())
    report["baseline_unchanged"] = all(
        hashlib.sha256((baseline / p).read_bytes()).hexdigest() == h
        for p, h in manifest.items()
    )
    report["rows"] = rows
    return report


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("directory", type=Path)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    if a.output.exists():
        raise ValueError("Output must be new")
    protocol = json.loads((a.directory / "protocol.json").read_text())
    report = {"quick": audit_quick, "feature": audit_features}.get(
        protocol.get("candidate"), audit
    )(a.directory)
    a.output.write_text(json.dumps(report, indent=2) + "\n")
