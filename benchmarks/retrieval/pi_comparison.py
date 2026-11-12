"""Compare stock Pi's single-request coding loop with Devstill YOLO.

No production prompts/tools are changed. Run directories are immutable experiments,
not a pool from which to select the best model draws.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import platform
import shutil
import signal
import statistics
import subprocess
import sys
import tempfile
import time
import traceback
from datetime import UTC, datetime
from pathlib import Path

import httpx

from benchmarks.retrieval import coding_campaign as campaign
from benchmarks.retrieval import swebench
from benchmarks.retrieval.coding_report import completed
from benchmarks.retrieval.recording_proxy import recording_proxy, transport_metrics

ROOT = Path(__file__).resolve().parents[2]
# Applied by the recording proxy to every model request of BOTH arms.
SAMPLING_PROFILES = {"default": None, "greedy": {"temperature": 0, "seed": 1}}

VARIANTS = (
    "pi",
    "quick",
    "review",
    "baseline",
    "feature",
    "pi_context",
    "minimal",
    "runtime",
)


# Pi's trial-private files (config, event log, stderr): a sibling of the
# repository in the trial workspace, outside the working copy Pi's commands see.
PI_PRIVATE = "pi-private"


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
    for config in sorted({campaign.corpus_entry(c)[1] for c in cases}):
        path = campaign.BENCH / "configs" / config
        cfg = load_config(path)
        index = resolve_index_path(path, cfg)
        files = [path, index.parent / "metadata.sqlite"]
        if cfg.index.vectors:
            files.append(index / "chroma.sqlite3")
        for file in files:
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


def prepared_tool_environment(node=False):
    """Give coding shells the same prepared toolchain as the hidden evaluator."""
    paths = [str(ROOT / ".venv/bin"), "/tmp/devstill-cargo/bin"]
    if node:
        from benchmarks.retrieval import js_corpus

        # The evaluator's node, ahead of version-manager shims and Pi's own.
        paths.insert(0, js_corpus.node_bin())
    prefixes = os.pathsep.join(paths)
    return {
        **campaign.RUST_ENV,
        "DEVSTILL_BENCH_TOOL_PATH": prefixes,
        "PATH": prefixes + os.pathsep + os.environ.get("PATH", ""),
    }


def check_tool_environment(environment, rust=True, node=False):
    checks = {}
    commands = {
        "python": [
            "python",
            "-c",
            "import sys, pytest, ephemeral_port_reserve, markupsafe; print(sys.executable); print(sys.version)",
        ],
    }
    if rust:
        # Only cargo corpora need the Rust toolchain.
        commands.update(cargo=["cargo", "--version"], rustc=["rustc", "--version"])
    if node:
        commands.update(node=["node", "-p", "process.execPath + ' ' + process.version"])
    for name, command in commands.items():
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
    # Pi's trial-private files live next to the repository, never inside the
    # working copy its own commands see (e.g. git stash -u, git status).
    artifacts = repo.parent / PI_PRIVATE
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
    if args.variant == "runtime" or (
        args.variant == "baseline" and getattr(args, "baseline_ref", None)
    ):
        # "baseline" with a git ref: the same runtime worker, importing the
        # Devstill runtime materialized at that ref (PYTHONPATH).
        from benchmarks.retrieval.runtime_candidate import runtime_worker

        return runtime_worker(args)
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
        if {"baseline", "runtime"} <= set(variants):
            report["workloads"][workload]["versus_baseline"] = versus_baseline(
                group, complete
            )
    return report


def versus_baseline(group, complete):
    """Devstill-only A/B: the runtime arm against the baseline arm (the
    runtime at an earlier git ref), paired by case and repeat."""
    pairs = [
        (p, q)
        for p in group
        if p["variant"] == "baseline"
        for q in group
        if q["variant"] == "runtime"
        and (p["id"], p["repeat"]) == (q["id"], q["repeat"])
    ]
    passed = [(p, q) for p, q in pairs if p["acceptance_pass"] and q["acceptance_pass"]]
    ratios = [
        q["worker_wall_seconds"] / p["worker_wall_seconds"]
        for p, q in passed
        if p["worker_wall_seconds"] > 0
    ]
    return {
        "complete": complete,
        "paired_trials": len(pairs),
        "baseline_passes": sum(bool(p["acceptance_pass"]) for p, _ in pairs),
        "runtime_passes": sum(bool(q["acceptance_pass"]) for _, q in pairs),
        "shared_passes": len(passed),
        "runtime_only_passes": sum(
            bool(q["acceptance_pass"] and not p["acceptance_pass"]) for p, q in pairs
        ),
        "baseline_only_passes": sum(
            bool(p["acceptance_pass"] and not q["acceptance_pass"]) for p, q in pairs
        ),
        "median_runtime_over_baseline_wall_ratio": statistics.median(ratios)
        if ratios
        else None,
        "geometric_mean_runtime_over_baseline_wall_ratio": statistics.geometric_mean(
            ratios
        )
        if ratios
        else None,
    }


def materialize_baseline(ref, destination):
    """The Devstill runtime (src/devstill) at git `ref`, extracted read-only
    into `destination`; (commit sha, {name: sha256})."""
    commit = subprocess.check_output(
        ["git", "rev-parse", "--verify", f"{ref}^{{commit}}"], cwd=ROOT, text=True
    ).strip()
    destination.mkdir(parents=True, exist_ok=False)
    archive = subprocess.run(
        ["git", "archive", "--format=tar", commit, "src/devstill"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["tar", "-x", "-C", str(destination)], input=archive.stdout, check=True
    )
    manifest = {}
    for path in sorted(destination.rglob("*")):
        if path.is_file():
            manifest[path.relative_to(destination).as_posix()] = digest(path)
            path.chmod(0o444)
    return commit, manifest


# Infrastructure failures, not agent outcomes: retried once, and again on --resume.
# startup_stall: the agent failed without a single generation request (seen
# once as Pi hanging after GET /v1/models); that measures the machine, not
# the agent, so the trial is retried once and the retry is recorded.
RETRYABLE = ("harness_error", "endpoint_error", "startup_stall")


class EndpointUnavailable(RuntimeError):
    """The model server stayed down; stop rather than record agent failures."""


def retryable(row):
    return any(row.get(flag) for flag in RETRYABLE)


@contextlib.contextmanager
def trial_workspace(path=None):
    """The same directory for every trial (runs are serial under the lock).

    Pi's prompt and tool output name the working directory; a random temp path
    would make otherwise identical repeats differ.
    """
    path = Path(path or Path(tempfile.gettempdir()) / "devstill-pi-compare")
    shutil.rmtree(path, ignore_errors=True)
    if path.exists():
        raise RuntimeError(f"Cannot clear the trial workspace {path}")
    path.mkdir(parents=True)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def resume_mismatches(protocol, expected):
    """Recorded protocol settings that differ from this invocation."""
    return sorted(key for key, value in expected.items() if protocol.get(key) != value)


def pending_trials(cases, variants, repeats, rows):
    """Trials without a usable result, in the fixed rotation order."""
    done = {(r["id"], r["repeat"], r["variant"]) for r in rows if not retryable(r)}
    return [
        (case, repeat, variant)
        for repeat in range(repeats)
        for index, case in enumerate(cases)
        for variant in trial_order(variants, index, repeat)
        if (case["id"], repeat, variant) not in done
    ]


def set_aside(directory):
    """Keep an interrupted or retried trial's files for diagnosis."""
    if not directory.exists():
        return None
    stale, number = directory.with_name(directory.name + ".discarded"), 1
    while stale.exists():
        number += 1
        stale = directory.with_name(f"{directory.name}.discarded{number}")
    directory.rename(stale)
    return stale


def harness_error_row(case, repeat, variant, directory, exc, elapsed):
    return {
        "id": case["id"],
        "repeat": repeat,
        "variant": variant,
        "workload": case["workload"],
        "corpus": case["corpus"],
        "error": f"harness_error: {type(exc).__name__}: {exc}"[:500],
        "harness_error": True,
        "acceptance_pass": False,
        "worker_wall_seconds": elapsed,
        "coding_seconds": elapsed,
        "validation_seconds": 0.0,
        "total_seconds": elapsed,
        "end_to_end_seconds": elapsed,
        "transport": transport_metrics(directory / "transport"),
    }


def wait_for_endpoint(
    base_url, headers, budget_seconds, *, interval=30, sleep=time.sleep
):
    """Block until the model server answers, so a restart pauses the campaign."""
    deadline = time.monotonic() + budget_seconds
    while True:
        try:
            with httpx.Client(timeout=15, trust_env=False) as client:
                client.get(
                    base_url.rstrip("/") + "/models", headers=headers
                ).raise_for_status()
            return
        except httpx.HTTPError as exc:
            if time.monotonic() >= deadline:
                raise EndpointUnavailable(
                    f"Model endpoint unavailable for {budget_seconds:.0f}s: "
                    f"{type(exc).__name__}: {exc}"
                ) from exc
            print(
                f"model endpoint unavailable ({type(exc).__name__}); "
                f"retrying in {interval}s",
                flush=True,
            )
            sleep(interval)


def run_trials(
    output, cases, variants, repeats, rows, trial, *, before_trial=None, attempts=2
):
    """Run every pending trial; one trial's failure never stops the others.

    A trial that fails for infrastructure reasons (harness exception, model
    server unreachable) is retried up to `attempts` times; any other exception
    escaping `before_trial` (Pi changed, endpoint down for good) stops the run.
    """
    earlier = {(r["id"], r["repeat"], r["variant"]): r for r in rows if retryable(r)}
    rows[:] = [r for r in rows if not retryable(r)]
    for case, repeat, variant in pending_trials(cases, variants, repeats, rows):
        key = (case["id"], repeat, variant)
        directory = output / f"{case['id']}-{repeat}-{variant}"
        for attempt in range(1, attempts + 1):
            if before_trial:
                before_trial()
            set_aside(directory)
            directory.mkdir()
            started = time.perf_counter()
            try:
                row = trial(case, repeat, variant, directory)
            except Exception as exc:  # noqa: BLE001 - isolate this trial only
                (directory / "harness-error.txt").write_text(traceback.format_exc())
                row = harness_error_row(
                    case, repeat, variant, directory, exc, time.perf_counter() - started
                )
            if key in earlier or attempt > 1:
                row["attempt"] = attempt
                row["retry_of"] = (earlier.get(key) or {}).get("error") or "retryable"
            write(directory / "result.json", row)
            if not retryable(row):
                break
        rows.append(row)
        write(output / "results.json", rows)
        write(output / "summary.json", summarize(rows, cases, variants, repeats))
        print(
            case["id"],
            repeat,
            variant,
            "pass" if completed(row) else "FAIL",
            round(row["worker_wall_seconds"], 2),
            "s",
            flush=True,
        )
    return rows


def server_model_ids(models):
    """Served model identity without volatile fields such as `created`."""
    return sorted(
        (m.get("id"), m.get("root"), m.get("max_model_len"))
        for m in models.get("data", [])
    )


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


def model_headers(runtime):
    from devstill.config import LlmConfig
    from devstill.credentials import resolve_api_key

    key = resolve_api_key(LlmConfig.model_validate(runtime["llm"]))
    return {"Authorization": f"Bearer {key}"} if key else {}


def run(args):
    from devstill.config import load_config

    tool_environment = prepared_tool_environment()
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
    need_rust = any(c["corpus"] == "cargo" for c in cases)
    need_node = any(c["corpus"] in campaign.JS_CORPORA for c in cases)
    if need_node:
        tool_environment = prepared_tool_environment(node=True)
    tool_versions = check_tool_environment(tool_environment, need_rust, need_node)
    variants = args.variants.split(",")
    baseline_ref = getattr(args, "baseline_ref", None)
    if baseline_ref and "baseline" not in variants:
        raise ValueError("--baseline-ref needs the baseline variant")
    if (
        not cases
        or args.repeats < 1
        or len(set(variants)) != len(variants)
        or not set(variants) <= set(VARIANTS)
        # A Devstill-only A/B (a baseline at a git ref) needs no Pi arm.
        or ("pi" not in variants and not baseline_ref)
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
    resuming = bool(args.resume) and (output / "protocol.json").exists()
    dataset_sha = hashlib.sha256(
        json.dumps(dataset, sort_keys=True).encode()
    ).hexdigest()
    sampling = SAMPLING_PROFILES[args.sampling]
    if resuming:
        # Continue an interrupted run with the very same frozen inputs.
        protocol = json.loads((output / "protocol.json").read_text())
        source = output / "source"
        manifest = json.loads((output / "source-manifest.json").read_text())
        if source_manifest(ROOT) != manifest:
            raise ValueError(
                "Cannot resume: Devstill or benchmark source changed since this run started"
            )
        expected = {
            "dataset_sha256": dataset_sha,
            "case_ids": [c["id"] for c in cases],
            "variants": variants,
            "repeats": args.repeats,
            "sampling_override": sampling,
            "worker_timeout_seconds": args.timeout,
            "pi_disable_reasoning": args.pi_disable_reasoning,
            "max_model_calls_per_attempt": args.max_model_calls or None,
            "coding_tool_versions": tool_versions,
        }
        if baseline_ref:
            expected["baseline_ref"] = subprocess.check_output(
                ["git", "rev-parse", "--verify", f"{baseline_ref}^{{commit}}"],
                cwd=ROOT,
                text=True,
            ).strip()
            expected["baseline_budget"] = getattr(args, "baseline_budget", "configured")
        if mismatched := resume_mismatches(protocol, expected):
            raise ValueError(f"Cannot resume: {', '.join(mismatched)} changed")
        runtime = json.loads((source / "devstill.json").read_text())
        identity = pi_identity()
        if identity != protocol["pi"]:
            raise ValueError("Cannot resume: Pi changed")
        baseline_path = output / "baseline-manifest.json"
        baseline_manifest = (
            json.loads(baseline_path.read_text()) if baseline_path.exists() else None
        )
        inputs = json.loads((output / "retrieval-input-manifest.json").read_text())
        if input_manifest(cases) != inputs:
            raise ValueError("Cannot resume: retrieval inputs changed")
        headers = model_headers(runtime)
        wait_for_endpoint(runtime["llm"]["base_url"], headers, args.endpoint_wait)
        with httpx.Client(timeout=15, trust_env=False) as client:
            served = client.get(
                runtime["llm"]["base_url"].rstrip("/") + "/models", headers=headers
            )
            served.raise_for_status()
        recorded = json.loads((output / "server-models.json").read_text())
        if server_model_ids(served.json()) != server_model_ids(recorded):
            raise ValueError("Cannot resume: the server now serves a different model")
        results_path = output / "results.json"
        rows = json.loads(results_path.read_text()) if results_path.exists() else []
        protocol.setdefault("resumed_at", []).append(datetime.now(UTC).isoformat())
        write(output / "protocol.json", protocol)
    else:
        output.mkdir(parents=True, exist_ok=False)
        source = output / "source"
        manifest = source_manifest(ROOT)
        for name in manifest:
            path = source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, path)
        write(output / "source-manifest.json", manifest)
        baseline_manifest = None
        baseline_commit = None
        if baseline_ref:
            # The Devstill runtime at an earlier git ref, read-only; the
            # benchmark harness itself is the current one for both arms.
            baseline_commit, baseline_manifest = materialize_baseline(
                baseline_ref, output / "baseline"
            )
            write(output / "baseline-manifest.json", baseline_manifest)
        elif "baseline" in variants:
            baseline_manifest = json.loads(
                (args.baseline / "manifest.json").read_text()
            )
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
        headers = model_headers(runtime)
        wait_for_endpoint(runtime["llm"]["base_url"], headers, args.endpoint_wait)
        with httpx.Client(timeout=15, trust_env=False) as client:
            metadata = client.get(
                cfg.llm.base_url.rstrip("/") + "/models", headers=headers
            )
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
            "baseline": f"git {baseline_commit}"
            if baseline_commit
            else str(args.baseline)
            if baseline_manifest
            else None,
            **(
                {
                    "baseline_ref": baseline_commit,
                    "baseline_budget": getattr(args, "baseline_budget", "configured"),
                    "baseline_budget_rule": "configured: the baseline runtime keeps "
                    "its configured editing timeout (the JS trial budget rule applies "
                    "to the current runtime only); trial: both get the trial budget",
                }
                if baseline_commit
                else {}
            ),
            "devstill_js_budget": "JS corpora: editing.timeout_seconds is set to the "
            "time left until the worker stop (DEVSTILL_DEADLINE), so Devstill gets the "
            "trial's whole wall budget; each Devstill trial records its effective budget "
            "in result.json (editing_budget)",
            "pi_private_files": "pi-config, pi-direct.jsonl and pi-stderr.log live next "
            "to the repository (outside Pi's working copy) and are copied to artifacts/",
            "review_policy": "Saved review reloaded and accepted unchanged automatically; no human think time or intervention",
            "coding_tool_environment": tool_environment,
            "corpus_test_environment": campaign.CORPUS_TEST_ENV,
            "coding_tool_versions": tool_versions,
            "repeats": args.repeats,
            "case_ids": [c["id"] for c in cases],
            "expected_attempts": len(cases) * args.repeats * len(variants),
            "pi": identity,
            "runtime": runtime,
            "python": sys.version,
            "platform": platform.platform(),
            "corpora": campaign.CORPORA,
            "dataset_sha256": dataset_sha,
            "dataset_status": "exposed development cases"
            if not args.dataset
            else "user supplied; held-out status not certified",
            "worker_timeout_seconds": args.timeout,
            "sampling_override": sampling,
            "max_model_calls_per_attempt": args.max_model_calls or None,
            "operating_point": "stock Pi tools vs production Devstill limits; common outer timeout; not equal total token budgets",
            "timing": "worker wall includes process startup, embedding warmup and index copy; coding excludes those; acceptance separate; checkout excluded",
            "pi_mode": "one initial request; stock system prompt and read,bash,edit,write; multiple model/tool turns allowed",
            "target": "no lower completed-acceptance success rate and median paired-success worker-wall ratio below 1, separately by workload",
            "sampling": (
                f"fixed for both arms by the recording proxy: {sampling}"
                if sampling
                else "server defaults unless recorded in actual requests"
            )
            + "; cache state uncontrolled; order rotated; full bodies recorded without authorization headers",
            "workspace": "same checkout path for every trial (serial runs); PYTHONHASHSEED=0 for workers; private index copies deleted after each trial",
        }
        write(output / "protocol.json", protocol)
        write(output / "cases.json", dataset)
        # Preserve verification provenance without copying it into any coding repo.
        write(
            output / "verification-manifest.json",
            json.loads((args.verified / "manifest.json").read_text()),
        )
        rows = []

    def trial(case, repeat, variant, directory):
        (directory / "query.txt").write_text(case["query"])
        with trial_workspace() as temporary:
            repo = temporary / "repo"
            # Independent clones avoid shared Git object writes by stock bash tools.
            campaign.clone(case, repo)
            if case["corpus"] != swebench.CORPUS:
                subprocess.run(
                    ["git", "repack", "-a", "-d"],
                    cwd=repo,
                    check=True,
                    capture_output=True,
                )
                (repo / ".git/objects/info/alternates").unlink(missing_ok=True)
            with recording_proxy(
                runtime["llm"]["base_url"],
                directory / "transport",
                args.max_model_calls,
                disable_reasoning=args.pi_disable_reasoning
                and variant in {"pi", "pi_context"},
                sampling=sampling,
            ) as url:
                trial_runtime = json.loads(json.dumps(runtime))
                trial_runtime["llm"]["base_url"] = url
                write(directory / "runtime.json", trial_runtime)
                env = dict(
                    os.environ,
                    PYTHONPATH=str(
                        (output / "baseline" if variant == "baseline" else source)
                        / "src"
                    )
                    + os.pathsep
                    + str(source),
                    PI_OFFLINE="1",
                    PI_TELEMETRY="0",
                    # Stable set ordering in Python tool output across repeats.
                    PYTHONHASHSEED="0",
                )
                env.update(tool_environment)
                env.update(campaign.CORPUS_TEST_ENV.get(case["corpus"], {}))
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
                if variant == "baseline" and protocol.get("baseline_ref"):
                    command += [
                        "--baseline-ref",
                        protocol["baseline_ref"],
                        "--baseline-budget",
                        protocol.get("baseline_budget", "configured"),
                    ]
                started = time.perf_counter()
                failure = None
                # SWE-bench: python/pytest run inside the instance's image.
                sandbox = (
                    swebench.sandbox(case, repo)
                    if case["corpus"] == swebench.CORPUS
                    else contextlib.nullcontext({})
                )
                with sandbox as extra, (directory / "worker.log").open("w") as log:
                    env.update(extra)
                    # When the worker will be stopped (Unix time; see
                    # devstill.runtime.service.DEADLINE_ENV): Devstill's JS
                    # loop plans its final finish before it. Only JS cases
                    # use it, so Python workers keep their exact environment.
                    if case["corpus"] in campaign.JS_CORPORA:
                        env["DEVSTILL_DEADLINE"] = f"{time.time() + args.timeout:.3f}"
                    try:
                        campaign.invoke_worker(command, env, log, args.timeout)
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
            private = repo.parent / PI_PRIVATE
            if private.exists() and not (directory / "artifacts").exists():
                # Pi's private files (the worker may have been stopped
                # before copying them itself).
                shutil.copytree(private, directory / "artifacts")
            if (repo / ".devstill").exists() and not (directory / "artifacts").exists():
                shutil.copytree(
                    repo / ".devstill",
                    directory / "artifacts",
                    ignore=shutil.ignore_patterns(
                        "chroma", "metadata.sqlite*", "cache"
                    ),
                )
            row["transport"] = transport_metrics(directory / "transport")
            if row["transport"]["upstream_unreachable"]:
                row["endpoint_error"] = True
            if row.get("error") and not row["transport"]["model_calls"]:
                row["startup_stall"] = True
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
            if not passed and not row.get("error"):
                row.setdefault("failure_stage", "independent_acceptance")
            if case["corpus"] in campaign.JS_CORPORA:
                from benchmarks.retrieval import js_corpus

                # Trials share one install through a node_modules link; an
                # agent command can still change its top level. Attribute it
                # to this trial and rebuild before the next one.
                modules = campaign.BENCH / "repos" / campaign.corpus_entry(case)[0]
                if damaged := js_corpus.audit(modules):
                    row["dependencies_damaged"] = damaged
                    js_corpus.install(
                        modules, directory / "dependency-repair.log", fresh=True
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
                        campaign.corpus_entry(case)[2],
                        "--",
                        ".",
                        ":!.devstill",
                    ],
                    cwd=repo,
                )
            )
            write(directory / "result.json", row)
            # A copy of the frozen corpus index (tens of MB); never read afterwards.
            shutil.rmtree(directory / "private-index", ignore_errors=True)
            return row

    def before_trial():
        if pi_identity() != identity:
            raise ValueError("Pi runtime changed mid-campaign")
        wait_for_endpoint(runtime["llm"]["base_url"], headers, args.endpoint_wait)

    try:
        run_trials(
            output,
            cases,
            variants,
            args.repeats,
            rows,
            trial,
            before_trial=before_trial,
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
                "coding_toolchain_unchanged": check_tool_environment(
                    tool_environment, need_rust, need_node
                )
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


def interrupt(signum, _frame):
    raise KeyboardInterrupt(f"signal {signum}")


def build_parser():
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
        "--resume",
        action="store_true",
        help="Continue an interrupted run in --output (same frozen source and inputs)",
    )
    parser.add_argument(
        "--endpoint-wait",
        type=float,
        default=1800,
        help="Seconds to wait for an unreachable model server before stopping",
    )
    parser.add_argument(
        "--sampling",
        choices=sorted(SAMPLING_PROFILES),
        default="default",
        help="default: server sampling; greedy: temperature 0 and a fixed seed for both arms",
    )
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
    parser.add_argument(
        "--baseline-ref",
        help="Devstill-only A/B: the baseline variant is the Devstill runtime at "
        "this git ref (pi is then optional)",
    )
    parser.add_argument(
        "--baseline-budget",
        choices=("configured", "trial"),
        default="configured",
        help="with --baseline-ref: the baseline runtime keeps its configured "
        "editing timeout (default; its historical behaviour) or, like the current "
        "runtime on JS corpora, gets the trial's whole wall budget",
    )
    parser.add_argument("--variant", choices=VARIANTS)
    parser.add_argument("--corpus", choices=campaign.CORPORA)
    parser.add_argument("--repo", type=Path)
    parser.add_argument("--runtime", type=Path)
    parser.add_argument("--query", type=Path)
    return parser


def main():
    parser = build_parser()
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
        # A supervisor's SIGTERM unwinds like Ctrl-C: the running worker's
        # process group is killed and the final audit is still written.
        signal.signal(signal.SIGTERM, interrupt)
        run(args)


if __name__ == "__main__":
    main()
