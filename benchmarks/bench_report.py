"""Deterministic campaign reports, rebuilt from the raw records at any time.

`write_campaign_report(dir)` reads campaign.json plus each suite's
results.json, acceptance logs and quality.json, and writes report.json and
REPORT.md. The same records always give the same report (apart from
`generated_at`), so `python -m benchmarks.bench_suite report DIR --check`
confirms a saved report against the raw results.
"""

from __future__ import annotations

import json
import shutil
import statistics
from datetime import UTC, datetime
from pathlib import Path

from benchmarks.retrieval.coding_report import completed

SCHEMA_VERSION = 1
ARM_NAMES = {"pi": "Pi", "runtime": "Devstill"}
INFRASTRUCTURE = {"harness_error", "endpoint_error", "worker_crash"}
COMPACT_FIELDS = (
    "id",
    "repeat",
    "variant",
    "workload",
    "corpus",
    "acceptance_pass",
    "failure_stage",
    "harness_error",
    "endpoint_error",
    "attempt",
    "worker_wall_seconds",
    "coding_seconds",
    "validation_seconds",
)


def arm(name):
    return ARM_NAMES.get(name, name)


def read_json(path, default=None):
    path = Path(path)
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except ValueError:
        return default


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    temporary.replace(path)


def one_line(text, limit=180):
    lines = [line.strip() for line in str(text or "").splitlines() if line.strip()]
    line = lines[0] if lines else ""
    return line if len(line) <= limit else line[: limit - 1] + "…"


def last_line(text, limit=180):
    lines = [line.strip() for line in str(text or "").splitlines() if line.strip()]
    line = lines[-1] if lines else ""
    return line if len(line) <= limit else line[: limit - 1] + "…"


def gist(text, limit=180):
    """First line, plus the last one when they differ (tracebacks end in the cause)."""
    first, last = one_line(text, limit), last_line(text, limit)
    if not last or first == last:
        return first
    joined = f"{first} … {last}"
    if len(joined) <= limit:
        return joined
    half = limit // 2
    return f"{first[:half]} … {last[-half:]}"


def median(values, digits=1):
    values = [v for v in values if v is not None]
    return round(statistics.median(values), digits) if values else None


# --- Failure classification ----------------------------------------------------


def acceptance_failure(trial):
    """Which part of the independent acceptance failed, and the key line."""
    logs = read_json(trial / "acceptance.json", []) or []
    for index, entry in enumerate(logs):
        if entry.get("returncode") == 0:
            continue
        if "resolved" in entry:  # official SWE-bench grading
            report = entry.get("report") or {}
            if report.get("patch_successfully_applied") is False:
                return (
                    "wrong_fix:patch_does_not_apply",
                    "grader could not apply the patch",
                )
            status = report.get("tests_status") or {}
            issue = (status.get("FAIL_TO_PASS") or {}).get("failure") or []
            existing = (status.get("PASS_TO_PASS") or {}).get("failure") or []
            if issue:
                return (
                    "wrong_fix:issue_tests_fail",
                    f"{len(issue)} issue test(s) fail, e.g. {issue[0]}",
                )
            if existing:
                return (
                    "wrong_fix:broke_existing_tests",
                    f"{len(existing)} existing test(s) broke, e.g. {existing[0]}",
                )
            return "wrong_fix:not_resolved", "not resolved"
        output = entry.get("output", "")
        if entry.get("returncode") == 124:
            return (
                "wrong_fix:behavior_check"
                if index == 0
                else "wrong_fix:regression_tests"
            ), ("acceptance timed out")
        if index == 0:
            return "wrong_fix:behavior_check", last_line(output)
        failed = [
            line for line in output.splitlines() if line.startswith(("FAILED", "ERROR"))
        ]
        return "wrong_fix:regression_tests", one_line(
            failed[0] if failed else last_line(output)
        )
    return "wrong_fix:unknown", "acceptance failed without a log"


def classify(row, trial):
    """(category, detail): `pass`, or why the trial did not count as a pass."""
    error = str(row.get("error") or "")
    execution = row.get("execution") or {}
    if row.get("harness_error"):
        return "harness_error", gist(error)
    if row.get("endpoint_error"):
        return "endpoint_error", "model server unreachable during the trial"
    if completed(row):
        return "pass", ""
    if error == "TimeoutExpired":
        if row.get("acceptance_pass"):
            return (
                "timeout_after_correct_edit",
                "edit was correct but the agent did not stop in time",
            )
        return "timeout", "no finished change within the time limit"
    if error == "CalledProcessError":
        log = trial / "worker.log"
        tail = last_line(log.read_text(errors="replace") if log.exists() else "")
        return "worker_crash", tail or "agent process exited with an error"
    stage = row.get("failure_stage")
    prefix = f"{stage}: " if stage and stage != "independent_acceptance" else ""
    if row.get("acceptance_pass"):
        detail = gist(error) or f"status {execution.get('status')}"
        return "incomplete_workflow", prefix + detail
    patch = trial / "change.patch"
    if patch.exists() and not patch.read_text(errors="replace").strip():
        calls = execution.get("tool_calls")
        idle = "nothing changed"
        if calls is not None:
            idle = (
                f"agent ended its turn after {calls} tool call(s) without editing "
                f"(stop reason: {execution.get('stop_reason', 'unknown')})"
            )
        return "no_change", prefix + (gist(error) or idle)
    if prefix:
        return f"gave_up:{stage}", prefix + gist(error)
    return acceptance_failure(trial)


# --- Suite and campaign reports --------------------------------------------------


def suite_report(directory, record):
    output = directory / "suites" / record["name"]
    settings = record.get("settings", {})
    rows = read_json(output / "results.json", []) or []
    protocol = read_json(output / "protocol.json", {}) or {}
    variants = protocol.get("variants") or settings.get("variants", "pi,runtime").split(
        ","
    )
    report = {
        "name": record["name"],
        "status": record.get("status"),
        "reason": record.get("reason"),
        "description": settings.get("description"),
        "settings": {
            k: settings.get(k) for k in ("repeats", "timeout", "variants", "dataset")
        },
        "excluded_cases": record.get("excluded_cases") or {},
        "trials": len(rows),
        "expected_trials": protocol.get("expected_attempts"),
        "arms": {},
        "paired": {},
        "cases": [],
        "failures": [],
        "quality": {},
        "audit": read_json(output / "final-audit.json"),
    }
    if not rows:
        return report
    classified = []
    for row in sorted(rows, key=lambda r: (r["id"], r["repeat"], r["variant"])):
        trial = output / f"{row['id']}-{row['repeat']}-{row['variant']}"
        category, detail = classify(row, trial)
        classified.append((row, category, detail))
        if category != "pass":
            report["failures"].append(
                {
                    "id": row["id"],
                    "repeat": row["repeat"],
                    "variant": row["variant"],
                    "category": category,
                    "detail": detail,
                    "trial": trial.relative_to(directory).as_posix(),
                }
            )
    for variant in variants:
        mine = [(r, c) for r, c, _ in classified if r["variant"] == variant]
        counts = {}
        for _, category in mine:
            if category != "pass":
                counts[category] = counts.get(category, 0) + 1
        report["arms"][variant] = {
            "attempts": len(mine),
            "passes": sum(c == "pass" for _, c in mine),
            "acceptance_passes": sum(bool(r.get("acceptance_pass")) for r, _ in mine),
            "median_pass_seconds": median(
                r["worker_wall_seconds"] for r, c in mine if c == "pass"
            ),
            "failures": dict(sorted(counts.items())),
        }
    by_key = {(r["id"], r["repeat"], r["variant"]): (r, c) for r, c, _ in classified}
    for candidate in [v for v in variants if v != "pi"]:
        pairs = [
            (by_key[(i, n, "pi")], by_key[(i, n, candidate)])
            for (i, n, v) in sorted(by_key)
            if v == "pi" and (i, n, candidate) in by_key
        ]
        shared = [(p, q) for p, q in pairs if p[1] == "pass" and q[1] == "pass"]
        ratios = [
            q[0]["worker_wall_seconds"] / p[0]["worker_wall_seconds"] for p, q in shared
        ]
        report["paired"][candidate] = {
            "pairs": len(pairs),
            "both": len(shared),
            "pi_only": sum(p[1] == "pass" and q[1] != "pass" for p, q in pairs),
            "candidate_only": sum(p[1] != "pass" and q[1] == "pass" for p, q in pairs),
            "neither": sum(p[1] != "pass" and q[1] != "pass" for p, q in pairs),
            "median_shared_seconds": {
                "pi": median(p[0]["worker_wall_seconds"] for p, _ in shared),
                candidate: median(q[0]["worker_wall_seconds"] for _, q in shared),
            },
            "median_ratio": median(ratios, 2),
            "candidate_faster": sum(r < 1 for r in ratios),
            "shared_seconds": [
                [
                    round(p[0]["worker_wall_seconds"], 2),
                    round(q[0]["worker_wall_seconds"], 2),
                ]
                for p, q in shared
            ],
        }
    for case in sorted({r["id"] for r, _, _ in classified}):
        entry = {"id": case, "outcomes": {}, "median_seconds": {}}
        for variant in variants:
            mine = [
                (r, c)
                for r, c, _ in classified
                if r["id"] == case and r["variant"] == variant
            ]
            entry["outcomes"][variant] = (
                f"{sum(c == 'pass' for _, c in mine)}/{len(mine)}"
            )
            entry["median_seconds"][variant] = median(
                r["worker_wall_seconds"] for r, c in mine if c == "pass"
            )
        report["cases"].append(entry)
    quality = read_json(output / "quality.json")
    if quality:
        report["quality"] = quality_summary(quality, variants)
    return report


def quality_summary(quality, variants):
    summary = {"tool": quality.get("tool")}
    for variant in variants:
        mine = [
            t for t in quality["trials"] if t["variant"] == variant and "error" not in t
        ]
        changed = [t for t in mine if t.get("changed_lines")]
        lint = [t["lint"] for t in changed if isinstance(t.get("lint"), dict)]
        summary[variant] = {
            "trials_with_change": len(changed),
            "median_changed_lines": median(t["changed_lines"] for t in changed),
            "median_size_ratio": median((t.get("size_ratio") for t in changed), 2),
            "trials_touching_extra_files": sum(
                bool(t.get("extra_files")) for t in changed
            ),
            "trials_touching_tests": sum(bool(t.get("tests_touched")) for t in changed),
            "new_lint_findings": sum(x.get("new_findings", 0) for x in lint),
            "lint_not_scored": sum("error" in x for x in lint),
        }
    return summary


def fraction(text):
    passed, total = text.split("/")
    return int(passed), int(total)


def previous_campaign(index_path, campaign):
    """The latest earlier campaign that has an archived report."""
    if not index_path or not Path(index_path).exists():
        return None
    entries = [
        json.loads(line)
        for line in Path(index_path).read_text().splitlines()
        if line.strip()
    ]
    earlier = sorted(
        (
            e
            for e in entries
            if e["id"] != campaign["id"] and e["started_at"] < campaign["started_at"]
        ),
        key=lambda e: e["started_at"],
    )
    for entry in reversed(earlier):
        report = read_json(Path(index_path).parent / entry["id"] / "report.json")
        if report:
            return report
    return None


def compare(previous, suites):
    if not previous:
        return None
    before = {s["name"]: s for s in previous["suites"]}
    result = {
        "id": previous["campaign"]["id"],
        "started_at": previous["campaign"]["started_at"],
        "model": previous["campaign"].get("model"),
        "git": (previous["campaign"].get("git") or {}).get("short"),
        "suites": {},
    }
    for suite in suites:
        old = before.get(suite["name"])
        if not old or not old.get("arms") or not suite.get("arms"):
            continue
        arms = {
            variant: {
                "before": f"{old['arms'][variant]['passes']}/{old['arms'][variant]['attempts']}",
                "now": f"{data['passes']}/{data['attempts']}",
            }
            for variant, data in suite["arms"].items()
            if variant in old["arms"]
        }
        old_cases = {c["id"]: c for c in old.get("cases", [])}
        flips = []
        for case in suite["cases"]:
            prior = old_cases.get(case["id"])
            for variant, outcome in case["outcomes"].items():
                was = (prior or {}).get("outcomes", {}).get(variant)
                if was is None:
                    continue
                (p0, n0), (p1, n1) = fraction(was), fraction(outcome)
                if n0 and n1 and p0 / n0 != p1 / n1:
                    flips.append(
                        {
                            "id": case["id"],
                            "variant": variant,
                            "before": was,
                            "now": outcome,
                        }
                    )
        result["suites"][suite["name"]] = {
            "arms": arms,
            "median_ratio": {
                "before": (old.get("paired", {}).get("runtime") or {}).get(
                    "median_ratio"
                ),
                "now": (suite.get("paired", {}).get("runtime") or {}).get(
                    "median_ratio"
                ),
            },
            "flips": flips,
        }
    return result


def pooled_pairs(suites):
    """Paired outcomes and timings pooled over every suite, per candidate arm."""
    pooled = {}
    for suite in suites:
        for candidate, data in suite["paired"].items():
            total = pooled.setdefault(
                candidate,
                {"pairs": 0, "both": 0, "pi_only": 0, "candidate_only": 0, "neither": 0}
                | {"seconds": []},
            )
            for key in ("pairs", "both", "pi_only", "candidate_only", "neither"):
                total[key] += data[key]
            total["seconds"] += data["shared_seconds"]
    for total in pooled.values():
        seconds = total.pop("seconds")
        ratios = [q / p for p, q in seconds if p > 0]
        total["median_shared_seconds"] = {
            "pi": median(p for p, _ in seconds),
            "candidate": median(q for _, q in seconds),
        }
        total["median_ratio"] = median(ratios, 2)
        total["candidate_faster"] = sum(r < 1 for r in ratios)
    return pooled


def campaign_report(directory, index_path=None):
    directory = Path(directory)
    campaign = read_json(directory / "campaign.json")
    suites = [suite_report(directory, record) for record in campaign["suites"]]
    totals = {}
    for suite in suites:
        for variant, data in suite["arms"].items():
            total = totals.setdefault(variant, {"attempts": 0, "passes": 0})
            total["attempts"] += data["attempts"]
            total["passes"] += data["passes"]
    started, finished = campaign.get("started_at"), campaign.get("finished_at")
    hours = None
    if started and finished:
        hours = round(
            (
                datetime.fromisoformat(finished) - datetime.fromisoformat(started)
            ).total_seconds()
            / 3600,
            2,
        )
    model = campaign.get("model") or {}
    return {
        "schema_version": SCHEMA_VERSION,
        "campaign": {
            "id": campaign["id"],
            "status": campaign["status"],
            "reason": campaign.get("reason"),
            "started_at": started,
            "finished_at": finished,
            "hours": hours,
            "note": campaign.get("note"),
            "command": campaign.get("command"),
            "host": campaign.get("host"),
            "git": campaign.get("git"),
            "model": model.get("configured_model"),
            "model_endpoint": model.get("base_url"),
            "served_models": model.get("served"),
            "pi_version": (campaign.get("pi") or {}).get("version"),
            "sampling": (campaign.get("settings") or {}).get("sampling"),
            "tools": campaign.get("tools"),
        },
        "suites": suites,
        "totals": totals,
        "totals_paired": pooled_pairs(suites),
        "previous": compare(previous_campaign(index_path, campaign), suites),
    }


# --- Markdown -----------------------------------------------------------------------


def cell(value):
    return "–" if value is None else str(value).replace("|", "\\|").replace("\n", " ")


def render(report):
    c = report["campaign"]
    git = c.get("git") or {}
    lines = [
        f"# Benchmark campaign {c['id']}",
        "",
        f"**Status:** {c['status']}"
        + (f" ({c['reason']})" if c.get("reason") else "")
        + f" · **Started:** {c['started_at']} · **Finished:** {cell(c['finished_at'])}"
        + (f" ({c['hours']} h)" if c.get("hours") is not None else ""),
        "",
        f"**Code:** {cell(git.get('branch'))} @ {cell(git.get('short'))}"
        + (" with uncommitted changes (see git.diff)" if git.get("dirty") else "")
        + f" · **Model:** {cell(c.get('model'))} at {cell(c.get('model_endpoint'))}"
        + f" · **Pi:** {cell(c.get('pi_version'))} · **Sampling:** {cell(c.get('sampling'))}",
    ]
    if c.get("note"):
        lines += ["", f"**Note:** {c['note']}"]
    lines += [
        "",
        "## Results",
        "",
        (
            "Pass = the agent finished and the independent acceptance check passed "
            "(hidden behaviour check plus protected tests; SWE-bench: the official "
            "grader's `resolved`). Times are worker wall seconds; the time ratio is the "
            "median of Devstill/Pi over cases both passed (below 1 = Devstill faster)."
        ),
        "",
        (
            "| Suite | Status | Trials | Pi passed | Devstill passed | Both | Pi only "
            "| Devstill only | Median s, shared (Pi / Devstill) | Time ratio | Devstill faster |"
        ),
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | ---: | ---: |",
    ]
    for s in report["suites"]:
        pi = s["arms"].get("pi")
        dev = s["arms"].get("runtime")
        paired = s["paired"].get("runtime") or {}
        shared = paired.get("median_shared_seconds") or {}
        status = s["status"] + (
            f": {one_line(s['reason'], 90)}" if s.get("reason") else ""
        )
        if not s["trials"]:
            lines.append(f"| {s['name']} | {cell(status)} | 0 |" + " – |" * 8)
            continue
        expected = f"/{s['expected_trials']}" if s.get("expected_trials") else ""
        lines.append(
            f"| {s['name']} | {cell(status)} | {s['trials']}{expected} "
            f"| {pi['passes'] if pi else '–'}/{pi['attempts'] if pi else '–'} "
            f"| {dev['passes'] if dev else '–'}/{dev['attempts'] if dev else '–'} "
            f"| {cell(paired.get('both'))} | {cell(paired.get('pi_only'))} "
            f"| {cell(paired.get('candidate_only'))} "
            f"| {cell(shared.get('pi'))} / {cell(shared.get('runtime'))} "
            f"| {cell(paired.get('median_ratio'))} "
            f"| {cell(paired.get('candidate_faster'))}/{cell(paired.get('both'))} |"
        )
    t = report["totals"]
    if t:
        both = (report.get("totals_paired") or {}).get("runtime") or {}
        shared = both.get("median_shared_seconds") or {}
        lines.append(
            "| **All** | | "
            f"{sum(v['attempts'] for v in t.values())} "
            f"| **{t.get('pi', {}).get('passes', '–')}/{t.get('pi', {}).get('attempts', '–')}** "
            f"| **{t.get('runtime', {}).get('passes', '–')}/{t.get('runtime', {}).get('attempts', '–')}** "
            f"| {cell(both.get('both'))} | {cell(both.get('pi_only'))} "
            f"| {cell(both.get('candidate_only'))} "
            f"| {cell(shared.get('pi'))} / {cell(shared.get('candidate'))} "
            f"| **{cell(both.get('median_ratio'))}** "
            f"| {cell(both.get('candidate_faster'))}/{cell(both.get('both'))} |"
        )
    previous = report.get("previous")
    if previous and previous["suites"]:
        lines += [
            "",
            f"## Change since the previous campaign ({previous['id']})",
            "",
            f"Previous run: model {cell(previous.get('model'))}, code {cell(previous.get('git'))}.",
            "",
            "| Suite | Pi passed | Devstill passed | Time ratio |",
            "| --- | --- | --- | --- |",
        ]
        flips = []
        for name, data in previous["suites"].items():
            pi = data["arms"].get("pi", {})
            dev = data["arms"].get("runtime", {})
            ratio = data["median_ratio"]
            lines.append(
                f"| {name} | {cell(pi.get('before'))} → {cell(pi.get('now'))} "
                f"| {cell(dev.get('before'))} → {cell(dev.get('now'))} "
                f"| {cell(ratio['before'])} → {cell(ratio['now'])} |"
            )
            flips += [(name, f) for f in data["flips"]]
        if flips:
            lines += ["", "Cases whose outcome changed:", ""]
            lines += [
                f"- {name} / {f['id']} ({arm(f['variant'])}): {f['before']} → {f['now']}"
                for name, f in flips
            ]
    lines += ["", "## What went wrong", ""]
    failures = [(s["name"], f) for s in report["suites"] for f in s["failures"]]
    infrastructure = [(n, f) for n, f in failures if f["category"] in INFRASTRUCTURE]
    groups = [
        (
            "Infrastructure (harness, model server, crashes): fix these first",
            infrastructure,
        ),
        (
            "Devstill failures: improvement targets",
            [
                (n, f)
                for n, f in failures
                if f["variant"] == "runtime" and (n, f) not in infrastructure
            ],
        ),
        (
            "Pi failures (for comparison)",
            [
                (n, f)
                for n, f in failures
                if f["variant"] == "pi" and (n, f) not in infrastructure
            ],
        ),
        (
            "Other arms",
            [
                (n, f)
                for n, f in failures
                if f["variant"] not in {"pi", "runtime"}
                and (n, f) not in infrastructure
            ],
        ),
    ]
    if not failures:
        lines.append("Nothing: every recorded trial passed.")
    for title, items in groups:
        if not items:
            continue
        lines += [
            f"### {title}",
            "",
            "| Suite | Case | Repeat | Arm | Category | Detail | Trial files |",
            "| --- | --- | ---: | --- | --- | --- | --- |",
        ]
        lines += [
            f"| {n} | {f['id']} | {f['repeat']} | {arm(f['variant'])} | {f['category']} "
            f"| {cell(f['detail'])} | `{f['trial']}` |"
            for n, f in items
        ]
        lines.append("")
    counts = {}
    for _, f in failures:
        key = (arm(f["variant"]), f["category"])
        counts[key] = counts.get(key, 0) + 1
    if counts:
        lines += [
            "### Failure counts",
            "",
            "| Arm | Category | Trials |",
            "| --- | --- | ---: |",
        ]
        lines += [f"| {a} | {cat} | {n} |" for (a, cat), n in sorted(counts.items())]
        lines.append("")
    skipped = [s for s in report["suites"] if s["status"] not in {"completed"}]
    excluded = [
        (s["name"], i, why)
        for s in report["suites"]
        for i, why in sorted(s["excluded_cases"].items())
    ]
    if skipped or excluded:
        lines += ["## Skipped, unfinished and excluded", ""]
        lines += [
            f"- **{s['name']}**: {s['status']}"
            + (f" — {cell(s.get('reason'))}" if s.get("reason") else "")
            for s in skipped
        ]
        lines += [f"- {n} / {i}: excluded — {why}" for n, i, why in excluded]
        lines.append("")
    quality_rows = [
        (s["name"], variant, data)
        for s in report["suites"]
        for variant, data in sorted(s.get("quality", {}).items())
        if variant != "tool"
    ]
    if quality_rows:
        lines += [
            "## Code quality (trials that changed code)",
            "",
            (
                "Size ratio compares changed lines with the reference fix. Lint counts new "
                "ruff findings (default rules, project settings ignored) in changed Python files."
            ),
            "",
            (
                "| Suite | Arm | Trials | Median changed lines | Median size vs reference "
                "| Touched files beyond the reference | Touched tests | New lint findings |"
            ),
            "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
        lines += [
            f"| {n} | {arm(v)} | {d['trials_with_change']} | {cell(d['median_changed_lines'])} "
            f"| {cell(d['median_size_ratio'])} | {d['trials_touching_extra_files']} "
            f"| {d['trials_touching_tests']} | {d['new_lint_findings']} |"
            for n, v, d in quality_rows
        ]
        lines.append("")
    per_case = [s for s in report["suites"] if s["cases"]]
    if per_case:
        lines += ["## Per case", ""]
        for s in per_case:
            variants = list(s["arms"])
            lines += [
                f"### {s['name']}",
                "",
                "| Case | "
                + " | ".join(f"{arm(v)} passed" for v in variants)
                + " | "
                + " | ".join(f"{arm(v)} median s" for v in variants)
                + " |",
                "| --- |" + " ---: |" * (2 * len(variants)),
            ]
            lines += [
                f"| {case['id']} | "
                + " | ".join(case["outcomes"][v] for v in variants)
                + " | "
                + " | ".join(cell(case["median_seconds"][v]) for v in variants)
                + " |"
                for case in s["cases"]
            ]
            lines.append("")
    lines += [
        "## How this was run",
        "",
        f"- Command: `{' '.join(c.get('command') or [])}`",
        f"- Host: {cell(c.get('host'))}; tools: "
        + ", ".join(f"{k} {v}" for k, v in sorted((c.get("tools") or {}).items())),
    ]
    lines += [
        f"- {s['name']}: repeats {cell(s['settings']['repeats'])}, timeout "
        f"{cell(s['settings']['timeout'])} s, arms {cell(s['settings']['variants'])}, "
        f"dataset `{cell(s['settings']['dataset'])}`"
        for s in report["suites"]
    ]
    lines += [
        (
            f"- Rebuild or check this report: `python -m benchmarks.bench_suite report "
            f".devstill/bench-history/{c['id']} [--check]`"
        ),
        "",
        (
            "Single trials vary between runs of the same code (sampling, server batching, "
            "timing), so judge changes by pass counts over repeats and across suites, "
            "not by one case."
        ),
        "",
    ]
    return "\n".join(lines)


def write_campaign_report(directory, index_path=None):
    directory = Path(directory)
    report = campaign_report(directory, index_path)
    saved = {**report, "generated_at": datetime.now(UTC).isoformat(timespec="seconds")}
    write_json(directory / "report.json", saved)
    (directory / "REPORT.md").write_text(render(report))
    return saved


def check_report(directory, index_path=None):
    """Differences between the saved report and one rebuilt from raw records."""
    directory = Path(directory)
    saved = read_json(directory / "report.json")
    if saved is None:
        return ["report.json is missing"]
    saved.pop("generated_at", None)
    fresh = campaign_report(directory, index_path)
    rebuilt = json.loads(json.dumps(fresh, sort_keys=True))
    problems = [
        f"section '{key}' differs from the raw records"
        for key in sorted(set(saved) | set(rebuilt))
        if saved.get(key) != rebuilt.get(key)
    ]
    markdown = directory / "REPORT.md"
    if not markdown.exists():
        problems.append("REPORT.md is missing")
    elif markdown.read_text() != render(fresh):
        problems.append("REPORT.md differs from the raw records")
    return problems


# --- History ------------------------------------------------------------------------


def index_entry(report, directory):
    c = report["campaign"]
    return {
        "id": c["id"],
        "started_at": c["started_at"],
        "finished_at": c["finished_at"],
        "status": c["status"],
        "git": (c.get("git") or {}).get("short"),
        "branch": (c.get("git") or {}).get("branch"),
        "dirty": (c.get("git") or {}).get("dirty"),
        "model": c.get("model"),
        "pi_version": c.get("pi_version"),
        "sampling": c.get("sampling"),
        "note": c.get("note"),
        "local_dir": str(directory),
        "suites": {
            s["name"]: {
                "status": s["status"],
                "trials": s["trials"],
                "passes": {
                    v: f"{d['passes']}/{d['attempts']}"
                    for v, d in sorted(s["arms"].items())
                },
                "median_ratio": (s["paired"].get("runtime") or {}).get("median_ratio"),
                "failures": len(s["failures"]),
            }
            for s in report["suites"]
        },
    }


def history_markdown(entries):
    lines = [
        "# Benchmark history",
        "",
        (
            "One row per campaign, newest first (generated by `benchmarks.bench_suite`). "
            "Each suite shows Pi passes · Devstill passes · median Devstill/Pi time ratio. "
            "Full reports: `<campaign>/REPORT.md` in this folder."
        ),
        "",
        "| Campaign | Status | Code | Model | Sampling | Suites | Note |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for e in sorted(entries, key=lambda e: e["started_at"], reverse=True):
        suites = "<br>".join(
            f"{name}: "
            + (
                f"{s['passes'].get('pi', '–')} · {s['passes'].get('runtime', '–')} · "
                f"{cell(s['median_ratio'])}"
                if s["trials"]
                else s["status"]
            )
            for name, s in e["suites"].items()
        )
        code = f"{cell(e['git'])}{'+dirty' if e.get('dirty') else ''}"
        lines.append(
            f"| [{e['id']}]({e['id']}/REPORT.md) | {e['status']} | {code} "
            f"| {cell(e['model'])} | {cell(e['sampling'])} | {suites} | {cell(e.get('note'))} |"
        )
    return "\n".join(lines) + "\n"


def archive(directory, tracked):
    """Copy a campaign's compact records into the git-tracked history."""
    directory, tracked = Path(directory), Path(tracked)
    report = read_json(directory / "report.json")
    campaign = read_json(directory / "campaign.json")
    target = tracked / campaign["id"]
    target.mkdir(parents=True, exist_ok=True)
    for name in ("campaign.json", "preflight.json", "report.json", "REPORT.md"):
        if (directory / name).exists():
            shutil.copyfile(directory / name, target / name)
    for record in campaign["suites"]:
        output = directory / "suites" / record["name"]
        if not output.exists():
            continue
        destination = target / "suites" / record["name"]
        destination.mkdir(parents=True, exist_ok=True)
        for name in (
            "protocol.json",
            "summary.json",
            "final-audit.json",
            "quality.json",
        ):
            if (output / name).exists():
                shutil.copyfile(output / name, destination / name)
        rows = read_json(output / "results.json", []) or []
        compact = []
        for row in rows:
            trial = output / f"{row['id']}-{row['repeat']}-{row['variant']}"
            category, detail = classify(row, trial)
            compact.append(
                {
                    **{k: row[k] for k in COMPACT_FIELDS if k in row},
                    "status": (row.get("execution") or {}).get("status"),
                    "model_calls": (row.get("transport") or {}).get("model_calls"),
                    "total_tokens": (row.get("transport") or {}).get("total_tokens"),
                    "error": one_line(row.get("error"), 300) or None,
                    "category": category,
                    "detail": detail,
                }
            )
        write_json(destination / "results.compact.json", compact)
    index = tracked / "index.jsonl"
    entries = [
        json.loads(line)
        for line in (index.read_text().splitlines() if index.exists() else [])
        if line.strip()
    ]
    entries = [e for e in entries if e["id"] != campaign["id"]]
    entries.append(index_entry(report, directory))
    entries.sort(key=lambda e: e["started_at"])
    index.write_text("".join(json.dumps(e, sort_keys=True) + "\n" for e in entries))
    (tracked / "HISTORY.md").write_text(history_markdown(entries))
    return target
