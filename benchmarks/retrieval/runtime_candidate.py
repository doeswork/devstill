"""Benchmark the same prepare/reload/apply operations exposed to real users."""

import json
import os
import shutil
import time

from pydantic import BaseModel

from benchmarks.retrieval import coding_campaign as campaign
from benchmarks.retrieval.pi_comparison import write
from devstill.config import DevstillConfig, load_config, resolve_index_path
from devstill.runtime.service import RuntimeSession

# Set by the harness for JS corpora only: when the worker is stopped (Unix
# seconds; devstill.runtime.service.DEADLINE_ENV).
DEADLINE_ENV = "DEVSTILL_DEADLINE"
# Loop outcome fields copied into result.json (instrumentation only; never
# part of acceptance).
LOOP_FIELDS = (
    "status",
    "finished",
    "failure_stage",
    "final_stage",
    "snapshot_turn",
    "stop_reason",
    "final_finish_seconds",
    "shadow",
    "verified",
    "js_stats",
)
# Runtime files copied into the trial directory when present.
RUN_FILES = ("events.jsonl", "agent-trace.json")


def known_fields(model, data, dropped=None, prefix=""):
    """`data` without keys `model` does not declare (recursively): a runtime
    configuration from newer code validates under an older Devstill (the
    A/B baseline at an earlier git ref). The dotted names of removed keys
    are appended to `dropped`."""
    if not isinstance(data, dict):
        return data
    kept = {}
    for name, value in data.items():
        field = model.model_fields.get(name)
        if field is None:
            if dropped is not None:
                dropped.append(prefix + name)
            continue
        annotation = field.annotation
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            value = known_fields(annotation, value, dropped, f"{prefix}{name}.")
        kept[name] = value
    return kept


def baseline_arm(args):
    """The worker runs the A/B baseline: the Devstill runtime at an earlier
    git ref (pi_comparison --baseline-ref)."""
    return args.variant == "baseline" and bool(getattr(args, "baseline_ref", None))


def editing_budget(editing, environ=None):
    """JS corpora (the harness sets DEADLINE_ENV): Devstill gets the trial's
    whole wall budget, so editing.timeout_seconds becomes the time left
    until the worker is stopped (the runtime's own deadline then follows the
    harness stop). Returns the effective budget for the record; Python
    corpora keep their configuration and get None."""
    raw = (os.environ if environ is None else environ).get(DEADLINE_ENV)
    try:
        stop = float(raw)
    except (TypeError, ValueError):
        return None
    configured = editing.timeout_seconds
    editing.timeout_seconds = min(3600.0, max(1.0, stop - time.time()))
    try:
        from devstill.config import js_turn_limit
    except ImportError:  # an earlier Devstill (A/B baseline): a fixed cap
        turns = editing.max_turns
    else:
        turns = js_turn_limit(editing)
    return {
        # Requested (the loop's real budget: result.json loop.budget).
        "timeout_seconds": round(editing.timeout_seconds, 3),
        "configured_timeout_seconds": configured,
        "max_model_calls": turns,
        "worker_stop_unix": stop,
    }


def runtime_worker(args):
    data = json.loads(args.runtime.read_text())
    dropped = []
    if baseline_arm(args):
        # Only the older runtime drops the fields it does not know; the
        # current runtime keeps strict validation (a typo stops the run).
        data = known_fields(DevstillConfig, data, dropped)
    runtime = DevstillConfig.model_validate(data)
    case = {"id": args.case[0], "corpus": args.corpus}
    path = campaign.BENCH / "configs" / campaign.corpus_entry(case)[1]
    cfg = load_config(path)
    private = args.output / "private-index"
    campaign.copy_index(resolve_index_path(path, cfg), private)
    cfg.repo.root = str(args.repo)
    cfg.index.path = str(private / "chroma")
    cfg.llm = runtime.llm
    cfg.editing = runtime.editing
    cfg.editing.python = shutil.which("python")
    # The baseline arm keeps its configured editing timeout unless the A/B
    # protocol gives it the trial budget too (--baseline-budget trial).
    budget = (
        editing_budget(cfg.editing)
        if not baseline_arm(args)
        or getattr(args, "baseline_budget", "configured") == "trial"
        else None
    )
    started = time.perf_counter()
    with RuntimeSession(path, cfg) as session:
        proposal = session.prepare(args.query.read_text())
        # Reload immutable saved review even in automatically approved benchmark mode.
        if proposal["status"] == "ready":
            saved = session.load(proposal["id"])
            result = session.apply(saved["id"], saved["digest"])
        else:
            result = {"status": "failed"}
        row = {
            "id": args.case[0],
            "variant": "runtime",
            "workload": args.workload,
            "corpus": args.corpus,
            "coding_seconds": time.perf_counter() - started,
            "execution": {"engine": "minimal_runtime", **result},
            "proposal_id": proposal["id"],
            "runtime_model_calls": proposal["model_calls"],
            "review_policy": "saved and reloaded patch, automatic approval, no human delay",
        }
        if proposal.get("error"):
            row["error"] = proposal["error"]
        if proposal.get("execution", {}).get("failure_stage"):
            row["failure_stage"] = proposal["execution"]["failure_stage"]
            row["last_error"] = proposal["execution"].get("last_error")
        if budget is not None:
            row["editing_budget"] = budget
        if dropped:
            row["config_fields_dropped"] = dropped
        execution = proposal.get("execution") or {}
        row["loop"] = {k: execution[k] for k in LOOP_FIELDS if k in execution}
        loop_budget = (execution.get("js_stats") or {}).get("loop_budget")
        if loop_budget is not None:
            # What the loop really had, from its start (after startup and
            # retrieval): editing_budget is the requested timeout.
            row["loop"]["budget"] = loop_budget
        run = session.root / proposal["id"]
        for name in RUN_FILES:
            if (run / name).is_file():
                shutil.copyfile(run / name, args.output / f"runtime-{name}")
        write(args.output / "result.json", row)
