"""Code-quality scores for every trial's patch: deterministic, no model.

Per trial: changed files and lines; size relative to the reference fix;
files the reference fix did not touch; test files touched; and new ruff
findings in the changed Python files. Ruff runs with its default rules and
`--isolated` (project settings ignored) so scores compare across projects.
mypy is left out: it needs every project's dependencies and stubs.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from collections import Counter
from pathlib import Path

from benchmarks.retrieval.patches import is_test_path, patch_stats

ROOT = Path(__file__).resolve().parents[1]
RUFF = ROOT / ".venv/bin/ruff"


def ruff_counts(directory, files, ruff=RUFF):
    """Rule code -> count for the given files ("syntax-error" for parse errors)."""
    files = [f for f in files if (Path(directory) / f).is_file()]
    if not files:
        return Counter()
    reply = subprocess.run(
        [
            str(ruff),
            "check",
            "--isolated",
            "--no-cache",
            "--exit-zero",
            "--output-format",
            "json",
            *files,
        ],
        cwd=directory,
        capture_output=True,
        text=True,
        check=True,
    )
    return Counter(
        item.get("code") or "syntax-error" for item in json.loads(reply.stdout)
    )


def corpus_source(case):
    """(checkout, commit) holding the code each trial started from."""
    from benchmarks.retrieval import coding_campaign as campaign

    name, _, commit = campaign.corpus_entry(case)
    return campaign.BENCH / "repos" / name, commit


def original(checkout, commit, path):
    reply = subprocess.run(
        ["git", "show", f"{commit}:{path}"],
        cwd=checkout,
        capture_output=True,
        check=False,
    )
    return reply.stdout if reply.returncode == 0 else None


def lint_delta(patch, files, read_original, ruff=RUFF):
    """New ruff findings that the patch introduces in `files`."""
    with tempfile.TemporaryDirectory(prefix="devstill-quality-") as temp:
        before, after = Path(temp) / "before", Path(temp) / "after"
        for root in (before, after):
            root.mkdir()
        for path in files:
            content = read_original(path)
            if content is not None:
                for root in (before, after):
                    (root / path).parent.mkdir(parents=True, exist_ok=True)
                    (root / path).write_bytes(content)
        subprocess.run(["git", "init", "-q"], cwd=after, check=True)
        (Path(temp) / "change.patch").write_text(patch)
        applied = subprocess.run(
            [
                "git",
                "apply",
                *[f"--include={f}" for f in files],
                str(Path(temp) / "change.patch"),
            ],
            cwd=after,
            capture_output=True,
            text=True,
            check=False,
        )
        if applied.returncode:
            return {"error": "patch did not apply: " + applied.stderr.strip()[:300]}
        old, new = ruff_counts(before, files, ruff), ruff_counts(after, files, ruff)
    added = {code: n - old[code] for code, n in new.items() if n > old[code]}
    return {
        "new_findings": sum(added.values()),
        "new_codes": dict(sorted(added.items())),
    }


def trial_quality(patch, reference, read_original, ruff=RUFF):
    stats = patch_stats(patch)
    changed = stats["added"] + stats["removed"]
    score = {
        "files": len(stats["files"]),
        "changed_lines": changed,
        "tests_touched": [f for f in stats["files"] if is_test_path(f)],
    }
    if reference:
        ref = patch_stats(reference)
        ref_changed = ref["added"] + ref["removed"]
        score["reference_lines"] = ref_changed
        score["size_ratio"] = round(changed / ref_changed, 2) if ref_changed else None
        score["extra_files"] = sorted(
            f for f in set(stats["files"]) - set(ref["files"]) if not is_test_path(f)
        )
    python = [f for f in stats["files"] if f.endswith(".py")]
    if python and patch.strip():
        score["lint"] = lint_delta(patch, python, read_original, ruff)
    return score


def suite_quality(output, verified=None, ruff=RUFF):
    """Score every trial of a pi_comparison run; writes quality.json there."""
    output = Path(output)
    cases = {c["id"]: c for c in json.loads((output / "cases.json").read_text())}
    rows = json.loads((output / "results.json").read_text())
    verified = Path(verified) if verified else None
    sources = {}
    trials = []
    for row in rows:
        trial = output / f"{row['id']}-{row['repeat']}-{row['variant']}"
        patch_file = trial / "change.patch"
        if not patch_file.exists():
            continue
        reference_file = verified / f"{row['id']}.patch" if verified else None
        reference = (
            reference_file.read_text(errors="replace")
            if reference_file and reference_file.exists()
            else None
        )
        try:
            if row["id"] not in sources:
                sources[row["id"]] = corpus_source(cases[row["id"]])
            checkout, commit = sources[row["id"]]
            score = trial_quality(
                patch_file.read_text(errors="replace"),
                reference,
                lambda path, checkout=checkout, commit=commit: original(
                    checkout, commit, path
                ),
                ruff,
            )
        except Exception as exc:  # noqa: BLE001 - one score never blocks the rest
            score = {"error": f"{type(exc).__name__}: {exc}"[:300]}
        trials.append(
            {
                "id": row["id"],
                "repeat": row["repeat"],
                "variant": row["variant"],
                **score,
            }
        )
    version = subprocess.run(
        [str(ruff), "--version"], capture_output=True, text=True, check=False
    ).stdout.strip()
    result = {
        "tool": version,
        "rules": "ruff default rules, --isolated",
        "trials": trials,
    }
    (output / "quality.json").write_text(json.dumps(result, indent=2) + "\n")
    return result
