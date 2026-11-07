"""Real-repository paired coding campaign, with evaluator-only acceptance.

Run `verify` before `run`. Each candidate is scored in a separate copy after the
coding process exits, so acceptance source cannot enter generation or retrieval.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from benchmarks.retrieval.coding_cases import CASES

ROOT = Path(__file__).resolve().parents[2]
BASELINE = ROOT / ".devstill/task-v2-baseline"
CORPORA = {
    "werkzeug": (
        "werkzeug-3.1.3",
        "baseline.json",
        "6389612fd1ee1bd93579eed5026e8fd471d04abd",
    ),
    "cargo": (
        "cargo-0.83.0",
        "cargo-final.json",
        "8f40fc59fb0c8df91c97405785197f3c630304ea",
    ),
}
BENCH = Path("/tmp/devstill-retrieval-bench")
RUST_ENV = {
    "RUSTUP_HOME": "/tmp/devstill-rustup",
    "CARGO_HOME": "/tmp/devstill-cargo",
    "CARGO_TARGET_DIR": "/tmp/devstill-cargo-target",
}


def write(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n")


def fingerprint():
    h = hashlib.sha256()
    for path in sorted((ROOT / "src/devstill").rglob("*.py")):
        h.update(str(path.relative_to(ROOT)).encode())
        h.update(path.read_bytes())
    return h.hexdigest()


def clone(case, destination):
    name, _, commit = CORPORA[case["corpus"]]
    source = BENCH / "repos" / name
    actual = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=source, text=True
    ).strip()
    if actual != commit:
        raise ValueError("Corpus revision drift")
    subprocess.run(
        [
            "git",
            "-c",
            "advice.detachedHead=false",
            "clone",
            "--shared",
            "--quiet",
            str(source),
            str(destination),
        ],
        check=True,
    )


def reference(case, repo):
    for edit in case["edits"]:
        path = repo / edit["path"]
        path.parent.mkdir(parents=True, exist_ok=True)
        original = path.read_text() if path.exists() else ""
        if edit["old"] is None:
            value = original + edit["new"]
        else:
            if edit["old"] not in original:
                raise ValueError(f"Reference anchor missing: {edit['path']}")
            value = original.replace(edit["old"], edit["new"], 1)
        path.write_text(value)


def accept(case, repo, artifact):
    """Acceptance copy is destroyed; model never sees appended test modules."""
    start = time.perf_counter()
    env = dict(os.environ, **RUST_ENV)
    with tempfile.TemporaryDirectory(prefix="devstill-accept-") as temp:
        work = Path(temp) / "repo"
        shutil.copytree(
            repo,
            work,
            ignore=shutil.ignore_patterns(".git", ".devstill", "__pycache__"),
        )
        if case["corpus"] == "werkzeug":
            check = Path(temp) / "accept.py"
            check.write_text(case["checks"] + "\n")
            env["PYTHONPATH"] = str(work / "src")
            commands = [
                [str(ROOT / ".venv/bin/python"), str(check)],
                [
                    str(ROOT / ".venv/bin/python"),
                    "-m",
                    "pytest",
                    "-q",
                    "tests/test_http.py",
                    "tests/test_security.py",
                    "tests/test_wrappers.py",
                    "tests/test_wsgi.py",
                    "tests/test_exceptions.py",
                ],
            ]
        else:
            path = work / case["check_file"]
            with path.open("a") as f:
                f.write(
                    "\n#[cfg(test)] mod devstill_acceptance { use super::*; #[test] fn behavior() {\n"
                    + case["checks"]
                    + "\n} }\n"
                )
            commands = [
                [
                    "/tmp/devstill-cargo/bin/cargo",
                    "test",
                    "--locked",
                    "--offline",
                    "-p",
                    case["package"],
                    "--lib",
                    "--",
                    "--test-threads=1",
                ]
            ]
        logs = []
        for command in commands:
            try:
                r = subprocess.run(
                    command,
                    cwd=work,
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=180,
                    check=False,
                )
                logs.append(
                    {
                        "command": command,
                        "returncode": r.returncode,
                        "output": r.stdout + r.stderr,
                    }
                )
            except subprocess.TimeoutExpired:
                logs.append(
                    {
                        "command": command,
                        "returncode": 124,
                        "output": "validation timeout",
                    }
                )
        write(artifact, logs)
        return all(r["returncode"] == 0 for r in logs), time.perf_counter() - start


def verify(output):
    output.mkdir(parents=True, exist_ok=False)
    results = []
    for case in CASES:
        with tempfile.TemporaryDirectory(prefix="devstill-case-verify-") as temp:
            repo = Path(temp) / "repo"
            clone(case, repo)
            baseline, _ = accept(case, repo, output / (case["id"] + "-base.json"))
            reference(case, repo)
            good, _ = accept(case, repo, output / (case["id"] + "-reference.json"))
            # Include untracked New reference files in the patch.
            subprocess.run(
                ["git", "add", "-N", "--", ".", ":!.devstill"], cwd=repo, check=True
            )
            patch = subprocess.check_output(
                ["git", "diff", "--binary", "--", ".", ":!.devstill"],
                cwd=repo,
                text=True,
            )
            (output / (case["id"] + ".patch")).write_text(patch)
            results.append(
                {"id": case["id"], "base_pass": baseline, "reference_pass": good}
            )
            print(case["id"], "base", baseline, "reference", good, flush=True)
            write(output / "verification.json", results)
    write(output / "cases.json", CASES)
    write(
        output / "manifest.json",
        {
            "cases_sha256": hashlib.sha256(
                json.dumps(CASES, sort_keys=True).encode()
            ).hexdigest(),
            "corpora": CORPORA,
        },
    )
    if any(r["base_pass"] or not r["reference_pass"] for r in results):
        raise RuntimeError("Case verification failed; do not run the campaign")


def worker(case, variant, repo, output):
    # Imports occur after the coordinator chooses the frozen or candidate source
    # via PYTHONPATH. Never import both implementations into one interpreter.
    from devstill.agent import AgentClient
    from devstill.config import load_config
    from devstill.executor import RepositoryExecutor
    from devstill.planning import (
        generate_plan,
        render_plan_json,
        render_plan_markdown,
        select_planning_context,
        write_plan_artifacts,
    )
    from devstill.search import HybridSearch
    from devstill.task_runs import run_tasks
    from devstill.tasks import create_task_draft, save_task_draft

    config_path = BENCH / "configs" / CORPORA[case["corpus"]][1]
    cfg = load_config(config_path)
    cfg.llm = load_config(ROOT / "devstill.json").llm.model_copy(
        update={"planning_max_output_tokens": 4096, "max_output_tokens": 4096}
    )
    cfg.executor = load_config(ROOT / "devstill.json").executor.model_copy(
        update={
            "enabled": True,
            "timeout_seconds": 120,
            "max_tool_calls": 12,
            "max_output_tokens": 4096,
            "validation_commands": [],
            "task_validation_commands": [],
            "task_concurrency": 1,
        }
    )
    artifacts = repo / ".devstill"
    artifacts.mkdir()
    setup = time.perf_counter()
    engine = HybridSearch(config_path, cfg)
    engine.embedder.warm()
    row = {
        "id": case["id"],
        "variant": variant,
        "workload": case["workload"],
        "corpus": case["corpus"],
        "setup_seconds": time.perf_counter() - setup,
        "source_module": sys.modules["devstill.tasks"].__file__,
    }
    start = time.perf_counter()
    try:
        if variant in {"old", "compact", "quick"}:
            pool = engine.query(case["query"], limit=12)
        else:
            from devstill.task_retrieval import retrieve_task_candidates

            pool = retrieve_task_candidates(engine, case["query"])
        row["retrieval_seconds"] = time.perf_counter() - start
        write(output / "pool.json", [r.to_dict() for r in pool])
        with AgentClient(cfg.llm, log_path=artifacts / "definition.jsonl") as client:
            if variant == "old":
                selected = select_planning_context(
                    pool, client=client, query=case["query"], repository=str(repo)
                )
                plan = generate_plan(
                    client,
                    query=case["query"],
                    repository=str(repo),
                    repo_root=repo,
                    results=selected,
                    retrieval_exclude=cfg.retrieval.exclude,
                )
                write_plan_artifacts(
                    artifacts, render_plan_markdown(plan), render_plan_json(plan)
                )
            else:
                create = create_task_draft
                if variant == "quick":
                    from devstill.quick_tasks import create_quick_draft

                    create = create_quick_draft
                draft = create(
                    client,
                    query=case["query"],
                    repo=repo,
                    candidates=pool,
                    mode="yolo",
                    excluded=cfg.retrieval.exclude,
                )
                save_task_draft(artifacts, draft)
                row["task_count"] = len(draft.plan.tasks)
        row["review_seconds"] = time.perf_counter() - start
        if variant == "old":
            executor = RepositoryExecutor(repo, artifacts, cfg)
            prepared = executor.prepare(source="plan")
            try:
                row["execution"] = executor.apply(prepared).to_dict()
            finally:
                prepared.close()
        else:
            row["execution"] = run_tasks(
                draft, repo=repo, artifact_root=artifacts, config=cfg
            )
    except Exception as exc:  # noqa: BLE001 - retain failed model attempts
        row["error"] = str(exc)
    finally:
        row["coding_seconds"] = time.perf_counter() - start
        engine.close()
        shutil.copytree(artifacts, output / "artifacts")
        write(output / "result.json", row)


def summarize(rows):
    summary = {}
    for workload in ["small", "feature"]:
        group = [r for r in rows if r["workload"] == workload]
        by = {
            variant: [r for r in group if r["variant"] == variant]
            for variant in ["old", "task"]
        }
        summary[workload] = {}
        for variant, values in by.items():
            summary[workload][variant] = {
                "attempts": len(values),
                "passes": sum(r["acceptance_pass"] for r in values),
                "reliable_cases": sum(
                    all(r["acceptance_pass"] for r in values if r["id"] == id)
                    and sum(r["id"] == id for r in values) == 3
                    for id in {r["id"] for r in values}
                ),
            }
        paired = []
        for old in by["old"]:
            new = next(
                (
                    r
                    for r in by["task"]
                    if (r["id"], r["repeat"]) == (old["id"], old["repeat"])
                ),
                None,
            )
            if new and old["acceptance_pass"] and new["acceptance_pass"]:
                paired.append((old, new))
        ratios = {
            key: statistics.median(n[key] / o[key] for o, n in paired)
            if paired
            else None
            for key in ["review_seconds", "total_seconds"]
        }
        regressions = [
            old["id"]
            for old in by["old"]
            if old["acceptance_pass"]
            and any(
                r["id"] == old["id"]
                and r["repeat"] == old["repeat"]
                and not r["acceptance_pass"]
                for r in by["task"]
            )
        ]
        summary[workload]["paired_successes"] = len(paired)
        summary[workload]["median_paired_ratios"] = ratios
        summary[workload]["regressions"] = sorted(set(regressions))
        summary[workload]["gate_pass"] = (
            len(group) == 72
            and not regressions
            and all(v is not None and v <= 1 for v in ratios.values())
            and summary[workload]["task"]["passes"]
            >= summary[workload]["old"]["passes"]
        )
    summary["promotion"] = (
        "eligible for review"
        if all(summary[w]["gate_pass"] for w in ["small", "feature"])
        else "do not promote"
    )
    return summary


def run(args):
    verified = json.loads((args.verified / "verification.json").read_text())
    manifest = json.loads((args.verified / "manifest.json").read_text())
    if (
        manifest["cases_sha256"]
        != hashlib.sha256(json.dumps(CASES, sort_keys=True).encode()).hexdigest()
        or len(verified) != 24
        or any(r["base_pass"] or not r["reference_pass"] for r in verified)
    ):
        raise ValueError("Need complete, passing, current reference verification")
    cases = [
        c
        for c in CASES
        if (not args.case or c["id"] in args.case)
        and (args.workload == "all" or c["workload"] == args.workload)
    ]
    if not cases or args.repeats < 1:
        raise ValueError("Select at least one case and a positive repeat count")
    baseline_manifest = json.loads((BASELINE / "manifest.json").read_text())
    if not all(
        hashlib.sha256((BASELINE / p).read_bytes()).hexdigest() == h
        for p, h in baseline_manifest.items()
    ):
        raise ValueError("Frozen baseline changed")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    frozen = fingerprint()
    write(
        output / "protocol.json",
        {
            "candidate_sha256": frozen,
            "corpora": CORPORA,
            "model": "from configured endpoint",
            "repeats": args.repeats,
            "candidate": args.candidate,
            "case_ids": [c["id"] for c in cases],
            "expected_attempts": len(cases) * args.repeats * 2,
            "baseline_unchanged_at_start": True,
            "cases_sha256": manifest["cases_sha256"],
            "usage": "Pi usage may be unavailable; do not claim equal total token cost",
            "sampling": "unchanged server defaults, same endpoint; record server fingerprint in generation logs",
        },
    )
    shutil.copyfile(args.verified / "cases.json", output / "cases.json")
    rows = []
    for repeat in range(args.repeats):
        for index, case in enumerate(cases):
            for variant in (
                ["old", args.candidate]
                if (index + repeat) % 2 == 0
                else [args.candidate, "old"]
            ):
                if fingerprint() != frozen:
                    raise ValueError("Candidate changed during campaign")
                directory = output / f"{case['id']}-{repeat}-{variant}"
                directory.mkdir()
                with tempfile.TemporaryDirectory(prefix="devstill-coding-v2-") as temp:
                    repo = Path(temp) / "repo"
                    clone(case, repo)
                    env = dict(os.environ)
                    env["PYTHONPATH"] = (
                        str((BASELINE if variant == "old" else ROOT) / "src")
                        + os.pathsep
                        + str(ROOT)
                    )
                    command = [
                        sys.executable,
                        str(Path(__file__).resolve()),
                        "worker",
                        "--case",
                        case["id"],
                        "--variant",
                        variant,
                        "--repo",
                        str(repo),
                        "--output",
                        str(directory),
                    ]
                    with (directory / "worker.log").open("w") as log:
                        try:
                            subprocess.run(
                                command,
                                env=env,
                                stdout=log,
                                stderr=subprocess.STDOUT,
                                timeout=360,
                                check=True,
                            )
                        except (
                            subprocess.TimeoutExpired,
                            subprocess.CalledProcessError,
                        ) as exc:
                            write(
                                directory / "result.json",
                                {
                                    "id": case["id"],
                                    "variant": variant,
                                    "workload": case["workload"],
                                    "error": str(exc),
                                    "coding_seconds": 360,
                                },
                            )
                    row = json.loads((directory / "result.json").read_text())
                    passed, validation = accept(
                        case, repo, directory / "acceptance.json"
                    )
                    row.update(
                        repeat=repeat,
                        acceptance_pass=passed,
                        validation_seconds=validation,
                        total_seconds=row["coding_seconds"] + validation,
                    )
                    subprocess.run(
                        ["git", "add", "-N", "--", ".", ":!.devstill"],
                        cwd=repo,
                        check=True,
                    )
                    (directory / "change.patch").write_bytes(
                        subprocess.check_output(
                            ["git", "diff", "--binary", "--", ".", ":!.devstill"],
                            cwd=repo,
                        )
                    )
                    write(directory / "result.json", row)
                    rows.append(row)
                    write(output / "results.json", rows)
                    if args.candidate == "quick":
                        from benchmarks.retrieval.coding_report import summarize_quick

                        summary = summarize_quick(
                            rows, [c["id"] for c in cases], args.repeats
                        )
                    else:
                        summary = summarize(rows)
                    write(output / "summary.json", summary)
                    print(
                        case["id"],
                        repeat,
                        variant,
                        passed,
                        round(row["total_seconds"], 2),
                        row.get("error", "")[:120],
                        flush=True,
                    )
    write(
        output / "final-audit.json",
        {
            "candidate_unchanged": fingerprint() == frozen,
            "source_sha256": frozen,
            "baseline_unchanged": all(
                hashlib.sha256((BASELINE / p).read_bytes()).hexdigest() == h
                for p, h in baseline_manifest.items()
            ),
        },
    )


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("action", choices=["verify", "run", "worker"])
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--verified", type=Path)
    p.add_argument("--repeats", type=int, default=3)
    p.add_argument("--case", action="append")
    p.add_argument("--variant", choices=["old", "task", "compact", "quick"])
    p.add_argument("--candidate", choices=["task", "quick"], default="task")
    p.add_argument("--workload", choices=["all", "small", "feature"], default="all")
    p.add_argument("--repo", type=Path)
    args = p.parse_args()
    if args.action == "verify":
        verify(args.output)
    elif args.action == "run":
        run(args)
    else:
        worker(
            next(c for c in CASES if c["id"] == args.case[0]),
            args.variant,
            args.repo,
            args.output,
        )


if __name__ == "__main__":
    main()
