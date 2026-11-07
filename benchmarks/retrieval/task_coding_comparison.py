"""Small disclosed coding pilot: legacy Plan/Pi versus native tasks."""

from __future__ import annotations

import json
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from benchmarks.retrieval import runner
from devstill.agent import AgentClient
from devstill.config import DevstillConfig, load_config
from devstill.executor import RepositoryExecutor
from devstill.indexer import RepositoryIndexer
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

FILES = {
    "styles.css": "button.submit { background-color: green; color: white; }\nbutton.cancel { background-color: gray; color: black; }\n",
    "labels.py": "def display_name(name):\n    return name.strip()\n",
    "backend.py": 'def greeting(name):\n    return {"message": "Hello " + name}\n',
    "routes.py": 'from backend import greeting\n\ndef handle_greeting(params):\n    return greeting(params.get("name", "world"))\n',
    "frontend.py": 'def greeting_text(payload):\n    return payload["message"]\n',
}
CASES = [
    (
        "button",
        "Change only the submit button background color from green to blue. Keep the cancel button unchanged.",
        "s=Path('styles.css').read_text(); assert 'background-color: blue' in s; assert 'background-color: gray' in s; assert 'background-color: green' not in s",
    ),
    (
        "fallback",
        "Update display_name in labels.py: trim whitespace and return Anonymous when the resulting name is empty. Keep normal names unchanged.",
        "from labels import display_name; assert display_name('  ')=='Anonymous'; assert display_name(' Ada ' )=='Ada'",
    ),
    (
        "endpoint",
        "Add an optional uppercase query parameter to handle_greeting in routes.py. Pass the option to greeting in backend.py, which must preserve its current default behavior and uppercase the message only when enabled. The route enables it only for the string true. Keep frontend behavior unchanged.",
        "from backend import greeting; from routes import handle_greeting; assert greeting('Ada')=={'message':'Hello Ada'}; assert greeting('Ada', uppercase=True)=={'message':'HELLO ADA'}; assert handle_greeting({'name':'Ada','uppercase':'true'})=={'message':'HELLO ADA'}; assert handle_greeting({'name':'Ada','uppercase':'false'})=={'message':'Hello Ada'}",
    ),
    (
        "independent",
        "Make two independent updates: display_name in labels.py should return Anonymous for whitespace-only names; greeting_text in frontend.py should return an empty string for a payload with no message. Preserve the normal behavior of each function.",
        "from labels import display_name; from frontend import greeting_text; assert display_name(' ' )=='Anonymous'; assert display_name(' Ada ' )=='Ada'; assert greeting_text({})==''; assert greeting_text({'message':'Hi'})=='Hi'",
    ),
]


def run(output):
    output.mkdir(parents=True, exist_ok=False)
    base = Path(tempfile.mkdtemp(prefix="devstill-coding-comparison-"))
    subprocess.run(["git", "init", "--quiet", str(base)], check=True)
    for name, text in FILES.items():
        (base / name).write_text(text)
    original = load_config(Path("devstill.json"))
    data = original.model_dump(mode="json")
    data["repo"]["root"] = "."
    data["index"]["path"] = ".devstill/chroma"
    data["index"]["collection"] = "coding_comparison"
    data["files"]["include"] = ["**/*.py", "**/*.css"]
    data["files"]["exclude"] = [".devstill/**", "check.py", "devstill.json"]
    data["retrieval"]["exclude"] = []
    data["llm"].update(planning_max_output_tokens=4096, max_output_tokens=4096)
    data["executor"].update(
        timeout_seconds=60,
        max_output_tokens=4096,
        max_tool_calls=12,
        validation_commands=[sys.executable + " check.py"],
        task_validation_commands=[],
        task_concurrency=1,
    )
    config = DevstillConfig.model_validate(data)
    path = base / "devstill.json"
    path.write_text(config.model_dump_json())
    indexer = RepositoryIndexer(path, config)
    try:
        indexer.run()
    finally:
        indexer.close()
    rows = []
    engine = HybridSearch(path, config)
    engine.embedder.warm()
    try:
        for index, (case_id, query, check) in enumerate(CASES):
            for variant in ["old", "task"] if index % 2 == 0 else ["task", "old"]:
                repo = base.parent / (base.name + "-" + case_id + "-" + variant)
                repo.mkdir()
                subprocess.run(["git", "init", "--quiet", str(repo)], check=True)
                for name, text in FILES.items():
                    (repo / name).write_text(text)
                (repo / "check.py").write_text(
                    "from pathlib import Path\n" + check + "\n"
                )
                root = repo / ".devstill"
                root.mkdir()
                # Acceptance script is withheld from retrieval and the model.
                row = {
                    "id": case_id,
                    "variant": variant,
                    "repository": str(repo),
                    "query": query,
                }
                start = time.perf_counter()
                pool = engine.query(query, limit=12 if variant == "old" else 24)
                try:
                    with AgentClient(
                        config.llm, log_path=root / "definition.jsonl"
                    ) as client:
                        if variant == "old":
                            selected = select_planning_context(
                                pool, client=client, query=query, repository=str(repo)
                            )
                            plan = generate_plan(
                                client,
                                query=query,
                                repository=str(repo),
                                repo_root=repo,
                                results=selected,
                                retrieval_exclude=[],
                            )
                            write_plan_artifacts(
                                root, render_plan_markdown(plan), render_plan_json(plan)
                            )
                        else:
                            draft = create_task_draft(
                                client,
                                query=query,
                                repo=repo,
                                candidates=pool,
                                mode="yolo",
                                excluded=[],
                            )
                            save_task_draft(root, draft)
                    row["review_seconds"] = time.perf_counter() - start
                    progress = []
                    if variant == "old":
                        executor = RepositoryExecutor(repo, root, config)
                        prepared = executor.prepare(
                            source="plan", progress=progress.append
                        )
                        try:
                            result = executor.apply(prepared, progress=progress.append)
                        finally:
                            prepared.close()
                        row["execution"] = result.to_dict()
                    else:
                        row["execution"] = run_tasks(
                            draft,
                            repo=repo,
                            artifact_root=root,
                            config=config,
                            progress=progress.append,
                        )
                    row["progress"] = progress
                except Exception as exc:  # noqa: BLE001 - preserve failures in benchmark reports
                    row["error"] = str(exc)
                row["seconds"] = time.perf_counter() - start
                # Score actual behavior independently of executor status or generated claims.
                check_result = subprocess.run(
                    [sys.executable, "check.py"],
                    cwd=repo,
                    text=True,
                    capture_output=True,
                    check=False,
                    timeout=10,
                )
                row["acceptance_pass"] = check_result.returncode == 0
                row["check_output"] = (
                    check_result.stdout + check_result.stderr
                ).strip()
                logs = [
                    json.loads(line)
                    for log in root.rglob("*.jsonl")
                    if log.name in {"definition.jsonl", "agent.jsonl"}
                    for line in log.read_text().splitlines()
                ]
                row["direct_http_requests"] = len(logs)
                row["direct_prompt_tokens"] = sum(
                    (r.get("usage") or {}).get("prompt_tokens", 0) for r in logs
                )
                row["direct_output_tokens"] = sum(
                    (r.get("usage") or {}).get("completion_tokens", 0) for r in logs
                )
                pi = root / "logs/pi-execute.jsonl"
                if pi.exists():
                    events = [json.loads(line) for line in pi.read_text().splitlines()]
                    row["pi_event_count"] = len(events)
                    row["pi_event_keys"] = list(events[0]) if events else []
                    row["pi_messages"] = [
                        e for e in events if "message_end" in str(e.get("type", ""))
                    ]
                rows.append(row)
                (output / "results.json").write_text(json.dumps(rows, indent=2) + "\n")
                print(
                    case_id,
                    variant,
                    "acceptance",
                    row["acceptance_pass"],
                    "seconds",
                    round(row["seconds"], 2),
                    "error",
                    row.get("error", ""),
                    flush=True,
                )
    finally:
        engine.close()
    runner.write_json(
        output / "provenance.json",
        {
            "kind": "Four small synthetic development cases; author-written acceptance checks, not independent feature acceptance",
            "base_repository": str(base),
            "source_sha256": runner.code_hash(
                list((runner.ROOT / "src/devstill").rglob("*.py"))
            ),
            "cases": CASES,
            "files": FILES,
            "policy": "Native methods, equal per-generation output cap4096. Aggregate model-call/token budgets differ and are reported; no matched-total-budget causal claim. Legacy Pi timeout60s/tools12.",
        },
    )

    summarize(rows, output)


def summarize(rows, output):
    for row in rows:
        assistants = [
            e["message"]
            for e in row.get("pi_messages", [])
            if e.get("message", {}).get("role") == "assistant"
        ]
        row["pi_assistant_turns"] = len(assistants)
        row["logical_generation_turns"] = row["direct_http_requests"] + len(assistants)
    summary = {}
    for variant in ["old", "task"]:
        group = [r for r in rows if r["variant"] == variant]
        summary[variant] = {
            "cases": len(group),
            "acceptance_pass": sum(r["acceptance_pass"] for r in group),
            "median_seconds": statistics.median(r["seconds"] for r in group),
            "median_logical_generation_turns": statistics.median(
                r["logical_generation_turns"] for r in group
            ),
            "usage_note": "Pi reported zero token usage; full old coding token totals are unavailable."
            if variant == "old"
            else "Direct usage recorded.",
        }
    runner.write_json(output / "scored-results.json", rows)
    runner.write_json(output / "summary.json", summary)


if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    run(a.output)
