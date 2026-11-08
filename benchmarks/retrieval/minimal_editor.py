"""Benchmark-only editor: source evidence -> exact replacements -> checks -> one repair.

Runs only in disposable benchmark clones. Hidden acceptance never enters the loop.
This is a Python small-edit experiment, not a production executor or sandbox.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

import httpx
from pydantic import BaseModel, ConfigDict

from benchmarks.retrieval.context_pilot import retrieve_context
from benchmarks.retrieval.pi_comparison import write


class Edit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str
    old: str
    new: str


class Read(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str
    start: int
    end: int


class EditResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    edits: list[Edit]
    reads: list[Read]
    checks: list[list[str]]


SYSTEM = """Implement the user's request using the supplied repository excerpts.
Return only a JSON object with these arrays:
{"edits":[{"path":"relative/file.py","old":"exact original text","new":"replacement"}],
 "reads":[{"path":"relative/file.py","start":1,"end":120}],
 "checks":[["python","-c","assert ..."]]}
Prefer completing the change immediately. Each old string must occur exactly once.
Preserve indentation and unrelated behavior. Include all parts of the request.
Provide at least one meaningful behavior check. Checks run in the repo with src on
PYTHONPATH. Allowed checks: python -c CODE or python -m pytest ARGS.
Do not use hidden benchmark files. You can edit existing files or create a new file
with old="". When evidence is insufficient, request up to four reads, each at most
400 lines, with empty edits and checks. You have at most two responses total,
including any read request or repair after failed checks. Do not return a plan.
After edits are applied, you may return empty edits and corrected checks.
Use ordinary JSON escaping: decoded Python check strings must contain real newlines,
not literal backslash-n sequences. Keep checks short and focused on the request."""


def source_path(repo, name):
    relative = Path(name)
    if relative.is_absolute() or any(
        p in {"..", ".git", ".devstill"} for p in relative.parts
    ):
        raise ValueError(f"Invalid repository path: {name}")
    target = repo / relative
    if not target.resolve().is_relative_to(repo.resolve()):
        raise ValueError(f"Path leaves repository: {name}")
    return target


def apply_edits(repo, edits):
    """Validate the entire batch before writing; never guess an ambiguous match."""
    if not isinstance(edits, list) or not 1 <= len(edits) <= 20:
        raise ValueError("Expected 1..20 edits")
    staged = {}
    for edit in edits:
        path = source_path(repo, edit["path"])
        old, new = edit["old"], edit["new"]
        if not isinstance(old, str) or not isinstance(new, str) or old == new:
            raise ValueError("Edit must change text")
        content = staged.get(path, path.read_text() if path.exists() else None)
        if old == "":
            if content is not None:
                raise ValueError(f"Cannot create existing file: {edit['path']}")
            staged[path] = new
        elif content is None or content.count(old) != 1:
            raise ValueError(f"Original text must match exactly once: {edit['path']}")
        else:
            staged[path] = content.replace(old, new, 1)
    for path, content in staged.items():
        if path.suffix == ".py":
            compile(content, str(path), "exec")
    for path, content in staged.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    return [str(p.relative_to(repo)) for p in staged]


def read_sources(repo, reads):
    if not isinstance(reads, list) or not 1 <= len(reads) <= 4:
        raise ValueError("Expected 1..4 reads")
    result = []
    for read in reads:
        start, end = read["start"], read["end"]
        if (
            type(start) is not int
            or type(end) is not int
            or not 1 <= start <= end < start + 400
        ):
            raise ValueError("Read needs a 1-based range of at most 400 lines")
        lines = source_path(repo, read["path"]).read_text().splitlines()
        result.append({**read, "code": "\n".join(lines[start - 1 : end])})
    return result


def run_checks(repo, commands, deadline):
    if not isinstance(commands, list) or not 1 <= len(commands) <= 3:
        raise ValueError("Provide 1..3 behavior checks")
    # Validate all commands before executing any.
    for command in commands:
        if not isinstance(command, list) or not all(
            isinstance(x, str) for x in command
        ):
            raise ValueError("Checks must be argument arrays")
        if not (len(command) == 3 and command[:2] == ["python", "-c"]) and not (
            len(command) >= 3 and command[:3] == ["python", "-m", "pytest"]
        ):
            raise ValueError("Use python -c CODE or python -m pytest ARGS")
    results = []
    env = dict(os.environ, PYTHONPATH=str(repo / "src") + os.pathsep + str(repo))
    for command in commands:
        remaining = deadline - time.perf_counter()
        if remaining <= 0:
            raise TimeoutError("Editor deadline exhausted")
        result = subprocess.run(
            command,
            cwd=repo,
            env=env,
            capture_output=True,
            text=True,
            timeout=min(30, remaining),
            check=False,
        )
        results.append(
            {
                "command": command,
                "returncode": result.returncode,
                "output": (result.stdout + result.stderr)[-10000:],
            }
        )
    return results


def edit_loop(repo, query, records, generate, output, deadline):
    messages = [
        {"role": "system", "content": SYSTEM},
        {
            "role": "user",
            "content": query + "\n\nRepository excerpts:\n" + json.dumps(records),
        },
    ]
    changed = []
    for attempt in range(2):
        content = generate(messages, deadline)
        messages.append({"role": "assistant", "content": content})
        feedback = {}
        try:
            from devstill.agent import extract_json_object

            action = extract_json_object(content)
            if action.get("reads"):
                feedback["reads"] = read_sources(repo, action["reads"])
            if action.get("edits"):
                changed.extend(apply_edits(repo, action["edits"]))
            if action.get("checks") and changed:
                feedback["checks"] = run_checks(repo, action.get("checks"), deadline)
                if all(c["returncode"] == 0 for c in feedback["checks"]):
                    write(
                        output / "editor-trace.json",
                        messages + [{"feedback": feedback}],
                    )
                    return {
                        "status": "applied_unvalidated",
                        "model_calls": attempt + 1,
                        "changed_files": sorted(set(changed)),
                        "checks": feedback["checks"],
                    }
        except (
            ValueError,
            TypeError,
            KeyError,
            OSError,
            SyntaxError,
            subprocess.TimeoutExpired,
        ) as exc:
            feedback["error"] = str(exc)
        feedback["changed_files"] = sorted(set(changed))
        messages.append(
            {
                "role": "user",
                "content": json.dumps(feedback)
                + "\nRepair using the current files; already applied edits remain. This is your final attempt.",
            }
        )
        write(output / "editor-trace.json", messages)
    return {
        "status": "failed",
        "model_calls": 2,
        "changed_files": sorted(set(changed)),
        "error": "No checked edit completed within two generation calls",
    }


def minimal_worker(args):
    from devstill.agent import completion_url
    from devstill.config import DevstillConfig
    from devstill.credentials import resolve_api_key

    runtime = json.loads(args.runtime.read_text())
    cfg = DevstillConfig.model_validate(runtime)
    query = args.query.read_text()
    started = time.perf_counter()
    row = {
        "id": args.case[0],
        "variant": "minimal",
        "workload": args.workload,
        "corpus": args.corpus,
        "execution": {"engine": "minimal", "status": "failed"},
    }
    try:
        records, timing = retrieve_context(args, runtime, query)
        row.update(timing)
        coding_started = time.perf_counter()
        deadline = coding_started + cfg.executor.timeout_seconds
        headers = {
            "Authorization": "Bearer " + (resolve_api_key(cfg.llm) or "devstill-local")
        }
        with httpx.Client() as client:

            def generate(messages, deadline):
                remaining = deadline - time.perf_counter()
                if remaining <= 0:
                    raise TimeoutError("Editor deadline exhausted")
                response = client.post(
                    completion_url(cfg.llm.base_url),
                    headers=headers,
                    timeout=remaining,
                    json={
                        "model": cfg.llm.model,
                        "messages": messages,
                        "max_tokens": cfg.executor.max_output_tokens,
                        "chat_template_kwargs": {"enable_thinking": False},
                        "response_format": {
                            "type": "json_schema",
                            "json_schema": {
                                "name": "repository_edits",
                                "strict": True,
                                "schema": EditResponse.model_json_schema(),
                            },
                        },
                    },
                )
                response.raise_for_status()
                message = response.json()["choices"][0]
                if message.get("finish_reason") != "stop":
                    raise ValueError(
                        f"Incomplete generation: {message.get('finish_reason')}"
                    )
                return message["message"]["content"]

            row["execution"] = {
                "engine": "minimal",
                **edit_loop(args.repo, query, records, generate, args.output, deadline),
            }
        row["coding_seconds"] = (
            time.perf_counter()
            - coding_started
            + timing["retrieval_seconds"]
            + timing["context_selection_seconds"]
        )
    except Exception as exc:  # noqa: BLE001 - record every experimental failure
        row["error"] = str(exc)
    row.setdefault("coding_seconds", time.perf_counter() - started)
    write(args.output / "result.json", row)
