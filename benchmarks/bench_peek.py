"""Read-only progress and interim scores of the latest (or a given) campaign.

    .venv/bin/python -m benchmarks.bench_peek [CAMPAIGN_DIR]

Reads only result files the runner has already written, so it is safe while
a campaign is running. Interim numbers, not the final report: a suite's
trials run case by case, so early results cover only the first cases.
"""

from __future__ import annotations

import json
import re
import statistics
import sys
from pathlib import Path

from benchmarks.retrieval.coding_report import completed

HISTORY = Path(".devstill/bench-history")


def planned(campaign):
    plan = {}
    try:
        for line in (campaign / "campaign.log").read_text().splitlines():
            if m := re.search(r"plan: (\S+) (\d+) trials", line):
                plan[m.group(1)] = int(m.group(2))
    except OSError:
        pass
    return plan


def seconds(row):
    """Agent wall time, as in REPORT.md (acceptance checking excluded)."""
    return row.get("worker_wall_seconds") or row.get("coding_seconds") or 0.0


def summarize(name, rows, total):
    by = {}
    for row in rows:
        by.setdefault(row["variant"], []).append(row)
    lines = [f"{name}: {len(rows)}/{total or '?'} trials done"]
    for variant, values in sorted(by.items()):
        passes = sum(completed(r) for r in values)
        late = sum(bool(r.get("acceptance_pass")) and not completed(r) for r in values)
        median = statistics.median(seconds(r) for r in values)
        timeouts = sum("Timeout" in str(r.get("error", "")) for r in values)
        lines.append(
            f"  {variant:8} {passes}/{len(values)} passed, median {median:.0f} s"
            + (f", {timeouts} timed out" if timeouts else "")
            + (f" ({late} with a correct edit anyway)" if late else "")
        )
    pairs = {}
    for row in rows:
        pairs.setdefault((row["id"], row.get("repeat")), {})[row["variant"]] = row
    both = [
        p
        for p in pairs.values()
        if {"pi", "runtime"} <= set(p)
        and completed(p["pi"])
        and completed(p["runtime"])
    ]
    if both:
        ratio = statistics.median(
            seconds(p["runtime"]) / max(seconds(p["pi"]), 0.01) for p in both
        )
        faster = sum(seconds(p["runtime"]) < seconds(p["pi"]) for p in both)
        lines.append(
            f"  Devstill/Pi time (median, cases both passed): {ratio:.2f}"
            f"; Devstill faster on {faster}/{len(both)}"
        )
    failed = sorted(
        f"{r['variant']}:{r['id']}#{r.get('repeat')}" for r in rows if not completed(r)
    )
    if failed:
        lines.append("  not passed: " + ", ".join(failed))
    damaged = [
        f"{r['variant']}:{r['id']}#{r.get('repeat')}"
        for r in rows
        if r.get("dependencies_damaged")
    ]
    if damaged:
        lines.append("  changed node_modules (repaired after): " + ", ".join(damaged))
    return lines


def main():
    campaign = Path(sys.argv[1]) if len(sys.argv) > 1 else HISTORY / "latest"
    campaign = campaign.resolve()
    if not (campaign / "suites").is_dir():
        print(f"No suite results under {campaign}")
        return
    plan = planned(campaign)
    print(f"campaign {campaign.name} (interim; the final numbers are in REPORT.md)")
    for name in plan or sorted(p.name for p in (campaign / "suites").iterdir()):
        path = campaign / "suites" / name / "results.json"
        try:
            rows = json.loads(path.read_text())
        except (OSError, ValueError):
            print(f"{name}: no finished trials yet ({plan.get(name, '?')} planned)")
            continue
        print("\n".join(summarize(name, rows, plan.get(name))))


if __name__ == "__main__":
    main()
