"""Real-repository paired coding campaign, with evaluator-only acceptance.

Run `verify` before `run`. Each candidate is scored in a separate copy after the
coding process exits, so acceptance source cannot enter generation or retrieval.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import signal
import sqlite3
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


def load_cases(path=None):
    cases = json.loads(path.read_text()) if path else CASES
    if not isinstance(cases, list) or not cases:
        raise ValueError("Dataset must contain a nonempty case list")
    seen = set()
    for case in cases:
        if (
            not re.fullmatch(r"[a-z][a-z0-9_]{0,79}", case.get("id", ""))
            or case["id"] in seen
            or case.get("corpus") not in CORPORA
            or case.get("workload") not in {"small", "feature"}
            or not case.get("query")
            or not case.get("checks")
            or not case.get("edits")
        ):
            raise ValueError("Invalid, incomplete or duplicate coding case")
        seen.add(case["id"])
    return cases


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


def accept(case, repo, artifact, *, protect_regressions=False):
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
            if protect_regressions:
                # The direct-Pi comparison allows ordinary test edits. Restore
                # evaluator regressions so deleting/weakening them cannot win.
                source = BENCH / "repos" / CORPORA[case["corpus"]][0] / "tests"
                shutil.rmtree(work / "tests", ignore_errors=True)
                shutil.copytree(source, work / "tests")
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


def verify(output, cases=CASES):
    output.mkdir(parents=True, exist_ok=False)
    results = []
    for case in cases:
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
    write(output / "cases.json", cases)
    write(
        output / "manifest.json",
        {
            "cases_sha256": hashlib.sha256(
                json.dumps(cases, sort_keys=True).encode()
            ).hexdigest(),
            "corpora": CORPORA,
        },
    )
    if any(r["base_pass"] or not r["reference_pass"] for r in results):
        raise RuntimeError("Case verification failed; do not run the campaign")


def copy_index(source, destination):
    """Private writable index; never hard-link a feature worker to the frozen corpus."""
    destination.mkdir(parents=True, exist_ok=True)
    if (source.parent / ".indexing-dirty").exists():
        raise ValueError("Source index needs recovery before benchmarking")
    shutil.copytree(source, destination / "chroma")
    with (
        sqlite3.connect(
            f"file:{source.parent / 'metadata.sqlite'}?mode=ro", uri=True
        ) as src,
        sqlite3.connect(destination / "metadata.sqlite") as dst,
    ):
        src.backup(dst)


def invoke_worker(command, env, log, timeout=360):
    """Terminate the worker and its Pi children together on timeout/interruption."""
    with subprocess.Popen(
        command, env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True
    ) as process:
        try:
            code = process.wait(timeout=timeout)
        except BaseException:
            try:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
            except ProcessLookupError:
                pass
            raise
        if code:
            raise subprocess.CalledProcessError(code, command)


def runtime_settings(llm_type, executor_type):
    # The frozen Quick version predates the top-level Feature configuration.
    # Both sides use identical LLM/executor settings; feature policy is an
    # explicit benchmark operating point, not inherited shell checks.
    raw = json.loads((ROOT / "devstill.json").read_text())
    return (
        llm_type.model_validate(raw.get("llm", {})),
        executor_type.model_validate(raw.get("executor", {})),
    )


def worker(
    case,
    variant,
    repo,
    output,
    *,
    baseline_quick=False,
    private_index=False,
    mode="yolo",
):
    # Imports occur after the coordinator chooses the frozen or candidate source
    # via PYTHONPATH. Never import both implementations into one interpreter.
    from devstill.agent import AgentClient
    from devstill.config import load_config, resolve_index_path
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
    llm, executor_config = runtime_settings(type(cfg.llm), type(cfg.executor))
    cfg.llm = llm.model_copy(
        update={"planning_max_output_tokens": 4096, "max_output_tokens": 4096}
    )
    cfg.executor = executor_config.model_copy(
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
    composed = variant == "feature" and case["workload"] == "feature"
    if composed or private_index:
        copy_index(resolve_index_path(config_path, cfg), artifacts)
        cfg.repo.root = str(repo)
        cfg.index.path = str(artifacts / "chroma")
    copy_seconds = time.perf_counter() - setup
    engine = HybridSearch(config_path, cfg)
    engine.embedder.warm()
    row = {
        "id": case["id"],
        "variant": variant,
        "workload": case["workload"],
        "corpus": case["corpus"],
        "setup_seconds": time.perf_counter() - setup,
        "index_copy_seconds": copy_seconds if composed or private_index else 0,
        "source_module": sys.modules["devstill.tasks"].__file__,
    }
    start = time.perf_counter()
    try:
        if composed:
            from devstill.feature_planning import (
                FeatureBudget,
                FeatureClient,
                FeatureDraft,
                prepare_feature_draft,
                save_feature,
            )

            budget = FeatureBudget(cfg.feature.timeout_seconds)
            pool = engine.query(case["query"], limit=12)
        elif variant in {"old", "compact", "quick", "feature"}:
            pool = engine.query(case["query"], limit=12)
        else:
            from devstill.task_retrieval import retrieve_task_candidates

            pool = retrieve_task_candidates(engine, case["query"])
        row["retrieval_seconds"] = time.perf_counter() - start
        write(output / "pool.json", [r.to_dict() for r in pool])
        client_context = (
            FeatureClient(
                cfg.llm, budget=budget, log_path=artifacts / "definition.jsonl"
            )
            if composed
            else AgentClient(cfg.llm, log_path=artifacts / "definition.jsonl")
        )
        with client_context as client:
            if composed:
                draft = prepare_feature_draft(
                    client,
                    query=case["query"],
                    repo=repo,
                    candidates=pool,
                    mode="yolo",
                    config=cfg,
                )
                budget.remaining()
                if isinstance(draft, FeatureDraft):
                    draft.discovery_seconds = budget.spent
                row["feature_route"] = (
                    "coordinated" if isinstance(draft, FeatureDraft) else "quick"
                )
                save_feature(artifacts, draft)
                row["task_count"] = len(draft.plan.tasks)
                row["dependency_edges"] = sum(
                    len(t.depends_on) for t in draft.plan.tasks
                )
            elif variant == "old" and not baseline_quick:
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
                if variant in {"quick", "feature"} or baseline_quick:
                    from devstill.quick_tasks import create_quick_draft

                    create = create_quick_draft
                draft = create(
                    client,
                    query=case["query"],
                    repo=repo,
                    candidates=pool,
                    mode=mode,
                    excluded=cfg.retrieval.exclude,
                )
                save_task_draft(artifacts, draft)
                if mode == "review":
                    from devstill.tasks import load_task_draft

                    reviewed = load_task_draft(artifacts, draft.id)
                    if reviewed != draft:
                        raise ValueError("Saved review changed before approval")
                    draft = reviewed
                    row["approval"] = (
                        "automatic, unedited saved review; human time excluded"
                    )
                row["mode"] = mode
                row["task_count"] = len(draft.plan.tasks)
        row["review_seconds"] = time.perf_counter() - start
        if composed:
            from devstill.feature_runs import run_feature
            from devstill.indexer import RepositoryIndexer

            def retrieve(query):
                if (artifacts / ".tasks-need-index").exists():
                    indexer = RepositoryIndexer(
                        config_path,
                        cfg,
                        embedder=engine.embedder,
                        chroma_store=engine.chroma,
                    )
                    try:
                        indexer.run()
                        (artifacts / ".tasks-need-index").unlink(missing_ok=True)
                    finally:
                        indexer.close()
                fresh = HybridSearch(
                    config_path,
                    cfg,
                    embedder=engine.embedder,
                    chroma_store=engine.chroma,
                )
                try:
                    return fresh.query(query, limit=12)
                finally:
                    fresh.close()

            row["execution"] = run_feature(
                draft, repo=repo, artifact_root=artifacts, config=cfg, retrieve=retrieve
            )
        elif variant == "old" and not baseline_quick:
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
        shutil.copytree(
            artifacts,
            output / "artifacts",
            ignore=shutil.ignore_patterns("chroma", "metadata.sqlite*", "cache"),
        )
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
    baseline = args.baseline
    dataset = load_cases(args.dataset)
    verified = json.loads((args.verified / "verification.json").read_text())
    manifest = json.loads((args.verified / "manifest.json").read_text())
    if (
        manifest["cases_sha256"]
        != hashlib.sha256(json.dumps(dataset, sort_keys=True).encode()).hexdigest()
        or json.loads((args.verified / "cases.json").read_text()) != dataset
        or len(verified) != len(dataset)
        or {r["id"] for r in verified} != {c["id"] for c in dataset}
        or any(r["base_pass"] or not r["reference_pass"] for r in verified)
    ):
        raise ValueError("Need complete, passing, current reference verification")
    cases = [
        c
        for c in dataset
        if (not args.case or c["id"] in args.case)
        and (args.workload == "all" or c["workload"] == args.workload)
    ]
    if not cases or args.repeats < 1:
        raise ValueError("Select at least one case and a positive repeat count")
    baseline_manifest = json.loads((baseline / "manifest.json").read_text())
    if not all(
        hashlib.sha256((baseline / p).read_bytes()).hexdigest() == h
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
            "baseline": str(baseline.resolve()),
            "dataset_status": "exposed development cases"
            if not args.dataset
            else "user-supplied cases; independent review/held-out status not certified",
            "aggregate_budget": "natural operating points; not equal total model-call/token budgets",
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
                        str((baseline if variant == "old" else ROOT) / "src")
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
                    if args.candidate == "feature" and variant == "old":
                        command.append("--baseline-quick")
                    if args.dataset:
                        command += ["--dataset", str(args.dataset.resolve())]
                    worker_start = time.perf_counter()
                    with (directory / "worker.log").open("w") as log:
                        try:
                            invoke_worker(command, env, log)
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
                                    "coding_seconds": time.perf_counter()
                                    - worker_start,
                                },
                            )
                            if (repo / ".devstill").exists():
                                shutil.copytree(
                                    repo / ".devstill",
                                    directory / "artifacts",
                                    dirs_exist_ok=True,
                                    ignore=shutil.ignore_patterns(
                                        "chroma", "metadata.sqlite*", "cache"
                                    ),
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
                    if args.candidate == "feature":
                        from benchmarks.retrieval.coding_report import (
                            summarize_features,
                        )

                        summary = summarize_features(rows, cases, args.repeats)
                    elif args.candidate == "quick":
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
                hashlib.sha256((baseline / p).read_bytes()).hexdigest() == h
                for p, h in baseline_manifest.items()
            ),
        },
    )


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("action", choices=["verify", "run", "worker", "preflight", "freeze"])
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--verified", type=Path)
    p.add_argument("--repeats", type=int, default=3)
    p.add_argument("--case", action="append")
    p.add_argument("--variant", choices=["old", "task", "compact", "quick", "feature"])
    p.add_argument("--candidate", choices=["task", "quick", "feature"], default="task")
    p.add_argument("--baseline", type=Path, default=BASELINE)
    p.add_argument("--baseline-quick", action="store_true")
    p.add_argument("--revision", default="831b199")
    p.add_argument(
        "--dataset",
        type=Path,
        help="Versioned JSON case list; defaults to the exposed 24-case development set.",
    )
    p.add_argument("--workload", choices=["all", "small", "feature"], default="all")
    p.add_argument("--repo", type=Path)
    args = p.parse_args()
    if args.action == "freeze":
        from benchmarks.retrieval.feature_preflight import freeze

        freeze(args.output, args.revision)
    elif args.action == "preflight":
        from benchmarks.retrieval.feature_preflight import preflight

        write_new = preflight(args)
        args.output.mkdir(parents=True, exist_ok=False)
        write(args.output / "preflight.json", write_new)
        print(json.dumps(write_new, indent=2))
        if not write_new["ready"]:
            raise SystemExit(1)
    elif args.action == "verify":
        verify(args.output, load_cases(args.dataset))
    elif args.action == "run":
        if args.candidate == "feature":
            from benchmarks.retrieval.feature_preflight import preflight

            checks = preflight(args)
            if not checks["ready"]:
                raise ValueError(
                    "Feature campaign preflight failed: " + json.dumps(checks)
                )
        run(args)
    else:
        worker(
            next(c for c in load_cases(args.dataset) if c["id"] == args.case[0]),
            args.variant,
            args.repo,
            args.output,
            baseline_quick=args.baseline_quick,
        )


if __name__ == "__main__":
    main()
