"""Benchmark the same prepare/reload/apply operations exposed to real users."""

import json
import shutil
import time

from benchmarks.retrieval import coding_campaign as campaign
from benchmarks.retrieval.pi_comparison import write
from devstill.config import DevstillConfig, load_config, resolve_index_path
from devstill.runtime.service import RuntimeSession


def runtime_worker(args):
    runtime = DevstillConfig.model_validate(json.loads(args.runtime.read_text()))
    path = campaign.BENCH / "configs" / campaign.CORPORA[args.corpus][1]
    cfg = load_config(path)
    private = args.output / "private-index"
    campaign.copy_index(resolve_index_path(path, cfg), private)
    cfg.repo.root = str(args.repo)
    cfg.index.path = str(private / "chroma")
    cfg.llm = runtime.llm
    cfg.editing = runtime.editing
    cfg.editing.python = shutil.which("python")
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
        write(args.output / "result.json", row)
