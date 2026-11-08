"""Compare stock Pi's single-request coding loop with Devstill YOLO.

No production prompts/tools are changed. Run directories are immutable experiments,
not a pool from which to select the best model draws.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx

from benchmarks.retrieval import coding_campaign as campaign
from benchmarks.retrieval.coding_report import completed
from benchmarks.retrieval.recording_proxy import recording_proxy, transport_metrics

ROOT = Path(__file__).resolve().parents[2]
VARIANTS = ("pi", "quick", "review", "baseline", "feature", "pi_context", "minimal")


def write(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n")
    temporary.replace(path)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_manifest(root):
    manifest = {
        p.relative_to(root).as_posix(): digest(p)
        for folder in (root / "src/devstill", root / "benchmarks/retrieval")
        for p in sorted(folder.rglob("*"))
        if p.is_file() and p.suffix in {".py", ".ts", ".json"}
    }
    for name in ("benchmarks/__init__.py",):
        if (root / name).exists():
            manifest[name] = digest(root / name)
    return manifest


def pi_identity():
    from devstill.pi_runtime import inspect_pi

    status = inspect_pi()
    if not status.ready:
        raise ValueError(status.reason)
    executable = Path(status.pi_path).resolve()
    # Includes bundled tools/provider code, rather than just the tiny CLI launcher.
    files = {
        str(p.relative_to(executable.parent)): digest(p)
        for p in sorted(executable.parent.rglob("*"))
        if p.is_file() and p.suffix in {".js", ".json"}
    }
    return {
        "version": status.pi_version,
        "executable": str(executable),
        "node": status.node_version,
        "runtime_sha256": hashlib.sha256(
            json.dumps(files, sort_keys=True).encode()
        ).hexdigest(),
    }


def input_manifest(cases):
    """Identify external corpus configurations and persisted retrieval metadata."""
    from devstill.config import load_config, resolve_index_path

    manifest = {}
    for corpus in sorted({c["corpus"] for c in cases}):
        path = campaign.BENCH / "configs" / campaign.CORPORA[corpus][1]
        cfg = load_config(path)
        index = resolve_index_path(path, cfg)
        for file in [path, index.parent / "metadata.sqlite", index / "chroma.sqlite3"]:
            manifest[str(file)] = digest(file)
            wal = file.with_name(file.name + "-wal")
            # SQLite can create an empty WAL when a read-only backup opens it.
            # It contains no committed data and is not retrieval-content drift.
            if wal.exists() and wal.stat().st_size:
                manifest[str(wal)] = digest(wal)
    return manifest


def trial_order(variants, case_index, repeat):
    # Cyclic order puts each arm in each position once per len(variants) repeats.
    offset = (case_index + repeat) % len(variants)
    return [*variants[offset:], *variants[:offset]]


def prepared_tool_environment():
    """Give coding shells the same prepared toolchain as the hidden evaluator."""
    prefixes = os.pathsep.join((str(ROOT / ".venv/bin"), "/tmp/devstill-cargo/bin"))
    return {
        **campaign.RUST_ENV,
        "DEVSTILL_BENCH_TOOL_PATH": prefixes,
        "PATH": prefixes + os.pathsep + os.environ.get("PATH", ""),
    }


def check_tool_environment(environment):
    checks = {}
    for name, command in {
        "python": [
            "python",
            "-c",
            "import sys, pytest, ephemeral_port_reserve, markupsafe; print(sys.executable); print(sys.version)",
        ],
        "cargo": ["cargo", "--version"],
        "rustc": ["rustc", "--version"],
    }.items():
        result = subprocess.run(
            command,
            env=dict(os.environ, **environment),
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
        checks[name] = result.stdout.strip()
    return checks


def direct_pi(case, repo, output, runtime):
    from devstill.config import DevstillConfig
    from devstill.credentials import resolve_api_key
    from devstill.pi_runtime import (
        pi_environment,
        resolve_pi_limits,
        write_provider_config,
    )

    cfg = DevstillConfig.model_validate(runtime)
    artifacts = repo / ".devstill"
    artifacts.mkdir()
    row = {
        "id": case["id"],
        "variant": "pi",
        "workload": case["workload"],
        "corpus": case["corpus"],
        "review_seconds": 0,
        "setup_seconds": 0,
        "retrieval_seconds": 0,
    }
    started = time.perf_counter()
    limits = resolve_pi_limits(cfg.llm, cfg.executor)
    write_provider_config(artifacts / "pi-config", cfg.llm, cfg.executor, limits)
    env = pi_environment()
    # pi_environment adds user bin directories. Keep the prepared test tools
    # ahead of user-level pytest/pip shims for this controlled comparison.
    if prefixes := os.environ.get("DEVSTILL_BENCH_TOOL_PATH"):
        env["PATH"] = prefixes + os.pathsep + env["PATH"]
    env.update(
        PI_CODING_AGENT_DIR=str(artifacts / "pi-config"),
        PI_OFFLINE="1",
        PI_TELEMETRY="0",
    )
    env[cfg.llm.api_key_env] = resolve_api_key(cfg.llm) or "devstill-local"
    command = [
        shutil.which("pi", path=env["PATH"]),
        "--print",
        "--mode",
        "json",
        "--no-session",
        "--no-context-files",
        "--no-extensions",
        "--no-skills",
        "--no-prompt-templates",
        "--no-mcp",
        "--no-approve",
        "--offline",
        "--tools",
        "read,bash,edit,write",
        "--thinking",
        "off",
        "--provider",
        "devstill",
        "--model",
        cfg.llm.model,
        "--",
        case["query"],
    ]
    write(output / "pi-command.json", command)
    with (
        (artifacts / "pi-direct.jsonl").open("w") as stdout,
        (artifacts / "pi-stderr.log").open("w") as stderr,
    ):
        process = subprocess.run(
            command, cwd=repo, env=env, stdout=stdout, stderr=stderr, check=False
        )
    events = []
    malformed = 0
    for line in (artifacts / "pi-direct.jsonl").read_text().splitlines():
        try:
            events.append(json.loads(line))
        except ValueError:
            malformed += 1
    assistants = [
        e["message"]
        for e in events
        if e.get("type") == "message_end"
        and e.get("message", {}).get("role") == "assistant"
    ]
    reason = assistants[-1].get("stopReason") if assistants else None
    success = (
        process.returncode == 0
        and any(e.get("type") == "agent_end" for e in events)
        and reason == "stop"
    )
    row["execution"] = {
        "engine": "stock_pi",
        "status": "applied_unvalidated" if success else "failed",
        "returncode": process.returncode,
        "stop_reason": reason,
        "tool_calls": sum(e.get("type") == "tool_execution_start" for e in events),
        "malformed_log_lines": malformed,
    }
    if not success:
        row["error"] = (
            f"Pi did not complete normally: exit={process.returncode}, stop={reason}"
        )
    row["coding_seconds"] = time.perf_counter() - started
    shutil.copytree(artifacts, output / "artifacts")
    write(output / "result.json", row)


def worker(args):
    if args.variant == "pi_context":
        from benchmarks.retrieval.context_pilot import context_worker

        return context_worker(args)
    if args.variant == "minimal":
        from benchmarks.retrieval.minimal_editor import minimal_worker

        return minimal_worker(args)
    runtime = json.loads(args.runtime.read_text())
    case = {
        "id": args.case[0],
        "query": args.query.read_text(),
        "workload": args.workload,
        "corpus": args.corpus,
    }
    if args.variant == "pi":
        direct_pi(case, args.repo, args.output, runtime)
    else:
        # Runtime is frozen per trial; reuse actual production YOLO algorithms.
        campaign.runtime_settings = lambda llm, executor: (
            llm.model_validate(runtime["llm"]),
            executor.model_validate(runtime["executor"]),
        )
        campaign.worker(
            case,
            "old"
            if args.variant == "baseline"
            else "quick"
            if args.variant == "review"
            else args.variant,
            args.repo,
            args.output,
            private_index=True,
            baseline_quick=args.variant == "baseline",
            mode="review" if args.variant == "review" else "yolo",
        )


def summarize(rows, cases, variants, repeats):
    expected = {
        (c["id"], n, v) for c in cases for n in range(repeats) for v in variants
    }
    observed = [(r["id"], r["repeat"], r["variant"]) for r in rows]
    complete = len(observed) == len(expected) and set(observed) == expected
    report = {
        "complete": complete,
        "attempts": len(rows),
        "expected_attempts": len(expected),
        "workloads": {},
        "promotion": "diagnostic only; fresh cases and repeated frozen-version evidence required",
    }
    for workload in sorted({c["workload"] for c in cases}):
        group = [r for r in rows if r["workload"] == workload]
        arms = {}
        for variant in variants:
            values = [r for r in group if r["variant"] == variant]
            arms[variant] = {
                "attempts": len(values),
                "behavior_passes": sum(r["acceptance_pass"] for r in values),
                "workflow_passes": sum(bool(completed(r)) for r in values),
                "model_calls": sum(r["transport"]["model_calls"] for r in values),
                "all_attempt_worker_seconds": sum(
                    r["worker_wall_seconds"] for r in values
                ),
            }
        comparisons = {}
        for variant in variants:
            if variant == "pi":
                continue
            pairs = [
                (p, q)
                for p in group
                if p["variant"] == "pi"
                for q in group
                if q["variant"] == variant
                and (p["id"], p["repeat"]) == (q["id"], q["repeat"])
            ]
            shared = [(p, q) for p, q in pairs if completed(p) and completed(q)]
            ratios = [
                q["worker_wall_seconds"] / p["worker_wall_seconds"] for p, q in shared
            ]
            comparisons[variant] = {
                "paired_trials": len(pairs),
                "shared_successes": len(shared),
                "candidate_only_passes": sum(
                    bool(completed(q) and not completed(p)) for p, q in pairs
                ),
                "pi_only_passes": sum(
                    bool(completed(p) and not completed(q)) for p, q in pairs
                ),
                "median_candidate_over_pi_wall_ratio": statistics.median(ratios)
                if ratios
                else None,
                "shared_success_medians": {
                    metric: {
                        name: statistics.median(row[i][metric] for row in shared)
                        if shared
                        else None
                        for i, name in enumerate(("pi", variant))
                    }
                    for metric in (
                        "coding_seconds",
                        "worker_wall_seconds",
                        "end_to_end_seconds",
                    )
                },
                "descriptive_target_met": bool(
                    complete
                    and ratios
                    and sum(completed(q) for _, q in pairs)
                    >= sum(completed(p) for p, _ in pairs)
                    and statistics.median(ratios) < 1
                ),
            }
        report["workloads"][workload] = {"variants": arms, "versus_pi": comparisons}
    return report


def validate_verification(directory, dataset):
    manifest = json.loads((directory / "manifest.json").read_text())
    results = json.loads((directory / "verification.json").read_text())
    if (
        json.loads((directory / "cases.json").read_text()) != dataset
        or manifest["cases_sha256"]
        != hashlib.sha256(json.dumps(dataset, sort_keys=True).encode()).hexdigest()
        or len(results) != len(dataset)
        or {r["id"] for r in results} != {c["id"] for c in dataset}
        or any(r["base_pass"] or not r["reference_pass"] for r in results)
    ):
        raise ValueError(
            "Need passing reference verification matching this exact dataset"
        )


def run(args):
    from devstill.config import load_config
    from devstill.credentials import resolve_api_key

    tool_environment = prepared_tool_environment()
    tool_versions = check_tool_environment(tool_environment)
    dataset = campaign.load_cases(args.dataset)
    validate_verification(args.verified, dataset)
    if set(args.case or []) - {c["id"] for c in dataset}:
        raise ValueError("Unknown selected case")
    cases = [
        c
        for c in dataset
        if (not args.case or c["id"] in args.case)
        and (args.workload == "all" or c["workload"] == args.workload)
    ]
    variants = args.variants.split(",")
    if (
        not cases
        or args.repeats < 1
        or len(set(variants)) != len(variants)
        or not set(variants) <= set(VARIANTS)
        or "pi" not in variants
        or len(variants) < 2
    ):
        raise ValueError(
            "Select cases, positive repeats and distinct variants including pi and a candidate"
        )
    # Serial campaigns may not share the same server unnoticed.
    import fcntl

    (ROOT / ".devstill").mkdir(exist_ok=True)
    lock = (ROOT / ".devstill/pi-comparison.lock").open("a")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        lock.close()
        raise ValueError("Another Pi comparison is active") from exc
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    source = output / "source"
    manifest = source_manifest(ROOT)
    for name in manifest:
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, path)
    write(output / "source-manifest.json", manifest)
    baseline_manifest = None
    if "baseline" in variants:
        baseline_manifest = json.loads((args.baseline / "manifest.json").read_text())
        if (
            not baseline_manifest
            or not (args.baseline / "src/devstill/quick_tasks.py").is_file()
        ):
            raise ValueError("A frozen Quick baseline is required")
        if any(
            digest(args.baseline / name) != sha
            for name, sha in baseline_manifest.items()
        ):
            raise ValueError("Frozen Quick baseline changed")
        for name in baseline_manifest:
            target = output / "baseline" / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(args.baseline / name, target)
        write(output / "baseline-manifest.json", baseline_manifest)

    cfg = load_config(ROOT / "devstill.json")
    if not cfg.llm.enabled or not cfg.llm.model:
        raise ValueError("Configure the model first")
    cfg.llm.max_output_tokens = cfg.llm.planning_max_output_tokens = 4096
    cfg.llm.planning_reasoning = "off"
    cfg.executor.reasoning = "off"
    cfg.executor.timeout_seconds = 120
    cfg.executor.max_tool_calls = 12
    cfg.executor.max_output_tokens = 4096
    cfg.executor.validation_commands = []
    runtime = cfg.model_dump(mode="json")
    write(source / "devstill.json", runtime)
    identity = pi_identity()
    inputs = input_manifest(cases)
    write(output / "retrieval-input-manifest.json", inputs)
    package_versions = subprocess.check_output(
        [sys.executable, "-m", "pip", "freeze"], text=True
    )
    (output / "python-packages.txt").write_text(package_versions)
    headers = (
        {"Authorization": f"Bearer {resolve_api_key(cfg.llm)}"}
        if resolve_api_key(cfg.llm)
        else {}
    )
    with httpx.Client(timeout=15) as client:
        metadata = client.get(cfg.llm.base_url.rstrip("/") + "/models", headers=headers)
        metadata.raise_for_status()
        write(output / "server-models.json", metadata.json())
    protocol = {
        "schema_version": 1,
        "pi_disable_reasoning": args.pi_disable_reasoning,
        "pi_request_override": {"chat_template_kwargs": {"enable_thinking": False}}
        if args.pi_disable_reasoning
        else None,
        "pi_context_mode": "Native Pi plus Quick-selected source excerpts; normal native tools",
        "minimal_mode": "Benchmark-only exact replacements, Python checks, two generation calls maximum; explicit reasoning off; same selected source excerpts",
        "created_at": datetime.now(UTC).isoformat(),
        "purpose": args.purpose,
        "variants": variants,
        "baseline": str(args.baseline) if baseline_manifest else None,
        "review_policy": "Saved review reloaded and accepted unchanged automatically; no human think time or intervention",
        "coding_tool_environment": tool_environment,
        "coding_tool_versions": tool_versions,
        "repeats": args.repeats,
        "case_ids": [c["id"] for c in cases],
        "expected_attempts": len(cases) * args.repeats * len(variants),
        "pi": identity,
        "runtime": runtime,
        "python": sys.version,
        "platform": platform.platform(),
        "corpora": campaign.CORPORA,
        "dataset_sha256": hashlib.sha256(
            json.dumps(dataset, sort_keys=True).encode()
        ).hexdigest(),
        "dataset_status": "exposed development cases"
        if not args.dataset
        else "user supplied; held-out status not certified",
        "worker_timeout_seconds": args.timeout,
        "max_model_calls_per_attempt": args.max_model_calls or None,
        "operating_point": "stock Pi tools vs production Devstill limits; common outer timeout; not equal total token budgets",
        "timing": "worker wall includes process startup, embedding warmup and index copy; coding excludes those; acceptance separate; checkout excluded",
        "pi_mode": "one initial request; stock system prompt and read,bash,edit,write; multiple model/tool turns allowed",
        "target": "no lower completed-acceptance success rate and median paired-success worker-wall ratio below 1, separately by workload",
        "sampling": "server defaults unless recorded in actual requests; cache state uncontrolled; order rotated; full bodies recorded without authorization headers",
    }
    write(output / "protocol.json", protocol)
    write(output / "cases.json", dataset)
    # Preserve verification provenance without copying it into any coding repo.
    write(
        output / "verification-manifest.json",
        json.loads((args.verified / "manifest.json").read_text()),
    )
    rows = []
    try:
        for repeat in range(args.repeats):
            for index, case in enumerate(cases):
                for variant in trial_order(variants, index, repeat):
                    if pi_identity() != identity:
                        raise ValueError("Pi runtime changed mid-campaign")
                    directory = output / f"{case['id']}-{repeat}-{variant}"
                    directory.mkdir()
                    (directory / "query.txt").write_text(case["query"])
                    with tempfile.TemporaryDirectory(
                        prefix="devstill-pi-compare-"
                    ) as temporary:
                        repo = Path(temporary) / "repo"
                        # Independent clones avoid shared Git object writes by stock bash tools.
                        campaign.clone(case, repo)
                        subprocess.run(
                            ["git", "repack", "-a", "-d"],
                            cwd=repo,
                            check=True,
                            capture_output=True,
                        )
                        (repo / ".git/objects/info/alternates").unlink(missing_ok=True)
                        with recording_proxy(
                            cfg.llm.base_url,
                            directory / "transport",
                            args.max_model_calls,
                            disable_reasoning=args.pi_disable_reasoning
                            and variant in {"pi", "pi_context"},
                        ) as url:
                            trial_runtime = json.loads(json.dumps(runtime))
                            trial_runtime["llm"]["base_url"] = url
                            write(directory / "runtime.json", trial_runtime)
                            env = dict(
                                os.environ,
                                PYTHONPATH=str(
                                    (
                                        output / "baseline"
                                        if variant == "baseline"
                                        else source
                                    )
                                    / "src"
                                )
                                + os.pathsep
                                + str(source),
                                PI_OFFLINE="1",
                                PI_TELEMETRY="0",
                            )
                            env.update(tool_environment)
                            command = [
                                sys.executable,
                                "-P",
                                "-m",
                                "benchmarks.retrieval.pi_comparison",
                                "worker",
                                "--case",
                                case["id"],
                                "--variant",
                                variant,
                                "--workload",
                                case["workload"],
                                "--corpus",
                                case["corpus"],
                                "--repo",
                                str(repo),
                                "--output",
                                str(directory),
                                "--runtime",
                                str(directory / "runtime.json"),
                                "--query",
                                str(directory / "query.txt"),
                            ]
                            started = time.perf_counter()
                            failure = None
                            with (directory / "worker.log").open("w") as log:
                                try:
                                    campaign.invoke_worker(
                                        command, env, log, args.timeout
                                    )
                                except (
                                    subprocess.TimeoutExpired,
                                    subprocess.CalledProcessError,
                                ) as exc:
                                    failure = type(exc).__name__
                            elapsed = time.perf_counter() - started
                        row = (
                            json.loads((directory / "result.json").read_text())
                            if (directory / "result.json").exists()
                            else {}
                        )
                        row.update(
                            id=case["id"],
                            repeat=repeat,
                            variant=variant,
                            workload=case["workload"],
                            corpus=case["corpus"],
                            worker_wall_seconds=elapsed,
                        )
                        row.setdefault("coding_seconds", elapsed)
                        if failure:
                            row["error"] = failure
                        if (repo / ".devstill").exists() and not (
                            directory / "artifacts"
                        ).exists():
                            shutil.copytree(
                                repo / ".devstill",
                                directory / "artifacts",
                                ignore=shutil.ignore_patterns(
                                    "chroma", "metadata.sqlite*", "cache"
                                ),
                            )
                        row["transport"] = transport_metrics(directory / "transport")
                        passed, validation = campaign.accept(
                            case,
                            repo,
                            directory / "acceptance.json",
                            protect_regressions=True,
                        )
                        row.update(
                            acceptance_pass=passed,
                            validation_seconds=validation,
                            total_seconds=row["coding_seconds"] + validation,
                            end_to_end_seconds=elapsed + validation,
                        )
                        subprocess.run(
                            ["git", "add", "-N", "--", ".", ":!.devstill"],
                            cwd=repo,
                            check=True,
                        )
                        (directory / "change.patch").write_bytes(
                            subprocess.check_output(
                                [
                                    "git",
                                    "diff",
                                    "--binary",
                                    campaign.CORPORA[case["corpus"]][2],
                                    "--",
                                    ".",
                                    ":!.devstill",
                                ],
                                cwd=repo,
                            )
                        )
                        write(directory / "result.json", row)
                        rows.append(row)
                        write(output / "results.json", rows)
                        write(
                            output / "summary.json",
                            summarize(rows, cases, variants, args.repeats),
                        )
                        print(
                            case["id"],
                            repeat,
                            variant,
                            "pass" if completed(row) else "FAIL",
                            round(elapsed, 2),
                            "s",
                            flush=True,
                        )
    finally:
        final_inputs = input_manifest(cases)
        write(output / "retrieval-input-final-manifest.json", final_inputs)
        write(
            output / "final-audit.json",
            {
                "frozen_source_unchanged": source_manifest(source) == manifest,
                "frozen_runtime_unchanged": json.loads(
                    (source / "devstill.json").read_text()
                )
                == runtime,
                "pi_unchanged": pi_identity() == identity,
                "coding_toolchain_unchanged": check_tool_environment(tool_environment)
                == tool_versions,
                "baseline_unchanged": all(
                    digest(output / "baseline" / name) == sha
                    for name, sha in baseline_manifest.items()
                )
                if baseline_manifest
                else None,
                "retrieval_inputs_unchanged": final_inputs == inputs,
                "working_tree_source_changed": source_manifest(ROOT) != manifest,
                "complete": len(rows) == protocol["expected_attempts"],
            },
        )
        lock.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["run", "worker", "report", "history"])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--verified", type=Path)
    parser.add_argument("--dataset", type=Path)
    parser.add_argument(
        "--baseline", type=Path, default=ROOT / ".devstill/feature-quick-baseline"
    )
    parser.add_argument("--case", action="append")
    parser.add_argument(
        "--workload", choices=["all", "small", "feature"], default="all"
    )
    parser.add_argument("--variants", default="pi,quick,feature")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--timeout", type=float, default=180)
    parser.add_argument("--max-model-calls", type=int, default=0)
    parser.add_argument(
        "--pi-disable-reasoning",
        action="store_true",
        help="Explicitly disable server reasoning for both native Pi arms; record original and upstream requests",
    )
    parser.add_argument(
        "--purpose",
        choices=["smoke", "development", "evaluation"],
        default="development",
    )
    parser.add_argument("--variant", choices=VARIANTS)
    parser.add_argument("--corpus", choices=campaign.CORPORA)
    parser.add_argument("--repo", type=Path)
    parser.add_argument("--runtime", type=Path)
    parser.add_argument("--query", type=Path)
    args = parser.parse_args()
    if args.timeout <= 0 or args.max_model_calls < 0:
        parser.error("Timeout must be positive and call budget nonnegative")
    if args.action == "worker":
        worker(args)
    elif args.action == "history":
        history = []
        for path in sorted(args.output.glob("*/protocol.json")):
            protocol = json.loads(path.read_text())
            if protocol.get("schema_version") != 1 or "pi_mode" not in protocol:
                continue
            summary = path.parent / "summary.json"
            audit = path.parent / "final-audit.json"
            history.append(
                {
                    "directory": str(path.parent),
                    "created_at": protocol["created_at"],
                    "purpose": protocol["purpose"],
                    "pi_version": protocol["pi"]["version"],
                    "dataset_sha256": protocol["dataset_sha256"],
                    "source_manifest_sha256": digest(
                        path.parent / "source-manifest.json"
                    ),
                    "summary": json.loads(summary.read_text())
                    if summary.exists()
                    else None,
                    "audit": json.loads(audit.read_text()) if audit.exists() else None,
                }
            )
        print(json.dumps(sorted(history, key=lambda r: r["created_at"]), indent=2))
    elif args.action == "report":
        protocol = json.loads((args.output / "protocol.json").read_text())
        cases = [
            c
            for c in json.loads((args.output / "cases.json").read_text())
            if c["id"] in protocol["case_ids"]
        ]
        print(
            json.dumps(
                summarize(
                    json.loads((args.output / "results.json").read_text()),
                    cases,
                    protocol["variants"],
                    protocol["repeats"],
                ),
                indent=2,
            )
        )
    elif not args.verified:
        parser.error("--verified is required for run")
    else:
        run(args)


if __name__ == "__main__":
    main()
