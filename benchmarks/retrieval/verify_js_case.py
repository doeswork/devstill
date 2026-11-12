"""Verify JS/TS coding cases one at a time, for case authors and reviewers.

    .venv/bin/python -m benchmarks.retrieval.verify_js_case CASE.json
    .venv/bin/python -m benchmarks.retrieval.verify_js_case CASE.json --edits ALT.json

CASE.json holds one case object (or a list). Without --edits it checks that the
pinned checkout fails the hidden check (and only that) and that the reference
edits pass acceptance (tsc, upstream vitest suite, hidden check). With --edits,
ALT.json is a list of {path, old, new} edits scored in place of the reference:
use it to confirm another valid implementation passes, or a wrong one fails.
Exit status 0 means the case (or the alternative) passed as described.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

from benchmarks.retrieval import coding_campaign as campaign


def describe(logs):
    names = ["tsc", "upstream suite", "hidden check"]
    lines = []
    for name, log in zip(names, logs):
        status = "pass" if log["returncode"] == 0 else "FAIL"
        detail = ""
        if log["returncode"] != 0:
            if log.get("new_failures"):
                detail = "new failures: " + "; ".join(log["new_failures"][:8])
            elif log.get("messages"):
                detail = "\n".join(m[:1200] for m in log["messages"][:6])
            else:
                detail = log["output"][-3000:]
        lines.append(f"  {name}: {status}" + (f"\n{indent(detail)}" if detail else ""))
    return "\n".join(lines)


def indent(text):
    return "\n".join("      " + line for line in text.strip().splitlines())


def score(case, edits=None):
    with tempfile.TemporaryDirectory(prefix="devstill-js-case-") as temp:
        temp = Path(temp)
        repo = temp / "repo"
        campaign.clone(case, repo)
        base_ok, _ = campaign.accept(case, repo, temp / "base.json")
        base = json.loads((temp / "base.json").read_text())
        campaign.reference(dict(case, edits=edits or case["edits"]), repo)
        ref_ok, _ = campaign.accept(case, repo, temp / "ref.json")
        ref = json.loads((temp / "ref.json").read_text())
    return base_ok, base, ref_ok, ref


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("case", type=Path)
    parser.add_argument("--edits", type=Path, help="alternative edits to score")
    args = parser.parse_args()
    data = json.loads(args.case.read_text())
    cases = campaign.load_cases_from_list(data if isinstance(data, list) else [data])
    alternative = json.loads(args.edits.read_text()) if args.edits else None
    ok = True
    for case in cases:
        if case["corpus"] not in campaign.JS_CORPORA:
            raise SystemExit(f"{case['id']}: not a JS corpus case")
        try:
            base_ok, base, ref_ok, ref = score(case, alternative)
        except Exception as exc:  # noqa: BLE001 - report, keep going
            print(f"{case['id']}: ERROR {type(exc).__name__}: {exc}")
            ok = False
            continue
        label = "alternative edits" if alternative else "reference"
        base_shape = (
            not base_ok
            and base[0]["returncode"] == 0
            and base[1]["returncode"] == 0
            and base[2]["returncode"] != 0
        )
        print(f"{case['id']}:")
        print(
            " base checkout (must fail only the hidden check):"
            + ("" if base_shape else "  <-- PROBLEM")
        )
        print(describe(base))
        print(f" {label} (must pass everything):" + ("" if ref_ok else "  <-- FAILS"))
        print(describe(ref))
        ok = ok and base_shape and ref_ok
    print("RESULT:", "OK" if ok else "NOT OK")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
