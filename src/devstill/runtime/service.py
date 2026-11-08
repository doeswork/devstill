"""One shared prepare/apply engine for terminal UI, CLI and benchmarks."""

from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path
from threading import Event
from uuid import uuid4

import httpx

from devstill.agent import AgentClient, completion_url
from devstill.config import resolve_index_path, resolve_model_path, resolve_repo_root
from devstill.context import (
    context_record,
    query_requests_tests,
    select_adaptive_context_results,
)
from devstill.credentials import resolve_api_key
from devstill.embeddings import EmbeddingBackend
from devstill.runtime import workspace
from devstill.runtime.artifacts import digest, load, run_directory, write
from devstill.runtime.checks import run_checks
from devstill.runtime.context import focused_candidates, support_records
from devstill.runtime.editor import edit_loop, editor_messages, response_schema
from devstill.runtime.requirements import delivery_checks, delivery_requirements
from devstill.search import HybridSearch


def render_check_changes(execution):
    """Show checks the model replaced or that were dropped, for human review."""
    lines = []
    for command in execution.get("replaced_checks") or []:
        lines.append("Replaced failing check:\n```python\n" + command[-1] + "\n```")
    for item in execution.get("invalid_checks") or []:
        lines.append(
            f"Dropped check (fails on original source too: {item['error']}):\n"
            "```python\n" + item["command"][-1] + "\n```"
        )
    return "".join(line + "\n\n" for line in lines)


def render(proposal):
    files = ", ".join(f"`{p}`" for p in proposal.get("files", {})) or "none"
    context = ", ".join(
        f"`{p}`" for p in sorted({r["path"] for r in proposal.get("evidence", [])})
    )
    return (
        f"## Task {proposal['id']}\n\n{proposal['query']}\n\n"
        f"Status: **{proposal['status']}**\n\nContext: {context or 'none'}\n\n"
        f"Changed files: {files}\n\n"
        "Validation: generated behavior checks; independent acceptance is not implied.\n\n"
        + render_check_changes(proposal.get("execution") or {})
        + (f"Error: {proposal['error']}\n\n" if proposal.get("error") else "")
        + "```diff\n"
        + proposal.get("patch", "")
        + "\n```\n"
    )


class RuntimeSession:
    def __init__(self, config_path, config, *, embedder=None, chroma=None):
        self.config_path, self.config = Path(config_path), config
        self.repo = resolve_repo_root(self.config_path, config).resolve()
        self.root = resolve_index_path(self.config_path, config).parent / "runs"
        self.embedder = embedder
        self.chroma = chroma
        self._owns_embedder = embedder is None
        self.cancelled = Event()
        self.client = httpx.Client()
        self.closed = False

    def close(self):
        self.cancelled.set()
        self.client.close()
        if self._owns_embedder and self.embedder is not None:
            self.embedder.close()
        self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def checkpoint(self, deadline):
        if self.cancelled.is_set() or self.closed:
            raise InterruptedError("Task cancelled")
        if time.perf_counter() >= deadline:
            raise TimeoutError("Task deadline exhausted")

    def evidence(self, query):
        cfg = self.config
        requirements = delivery_requirements(self.repo, query)
        if self.embedder is None:
            self.embedder = EmbeddingBackend(
                resolve_model_path(cfg),
                max_sequence_length=cfg.embedding.max_sequence_length,
                batch_size=cfg.embedding.batch_size,
                normalize=cfg.embedding.normalize_embeddings,
                device=cfg.embedding.device,
            )
        # Reconcile current working-tree content incrementally; unchanged files are not re-embedded.
        from devstill.indexer import RepositoryIndexer

        indexer = RepositoryIndexer(
            self.config_path, cfg, embedder=self.embedder, chroma_store=self.chroma
        )
        try:
            indexer.run(
                progress=lambda event: self.checkpoint(
                    getattr(self, "_deadline", float("inf"))
                )
            )
            self.chroma = indexer.chroma
            (self.root.parent / ".tasks-need-index").unlink(missing_ok=True)
        finally:
            indexer.close()
        search = HybridSearch(
            self.config_path, cfg, embedder=self.embedder, chroma_store=self.chroma
        )
        self.chroma = search.chroma
        try:
            lexical = search.query(query, limit=12, lexical_only=True)
            exact = [r for r in lexical if r.scores.get("exact", 0) > 0]
            unique_path = {r.chunk.path for r in exact}
            candidates = (
                lexical if len(unique_path) == 1 else search.query(query, limit=12)
            )

            candidates = focused_candidates(candidates, query)

            def records_for(results):
                records, complete_paths = [], set()
                for result in results:
                    record = context_record(result)
                    path = workspace.guard(self.repo, cfg, record["path"])
                    raw = path.read_bytes()
                    if result.source_hash and workspace.sha(raw) != result.source_hash:
                        raise ValueError(f"Stale indexed source: {record['path']}")
                    if len(raw) <= 6000:
                        if record["path"] in complete_paths:
                            continue
                        record = {
                            **record,
                            "code": raw.decode(),
                            "lines": [1, len(raw.splitlines())],
                            "complete": True,
                        }
                        complete_paths.add(record["path"])
                    records.append(record)
                return records + support_records(
                    self.repo,
                    records,
                    requirements,
                    lambda name: workspace.guard(self.repo, cfg, name),
                )

            with AgentClient(cfg.llm) as counter:

                def fits(results):
                    messages = editor_messages(
                        query, records_for(results), requirements
                    )
                    if (
                        sum(len(m["content"]) for m in messages)
                        > cfg.editing.max_context_chars - 4000
                    ):
                        return False
                    if cfg.llm.context_window is None:
                        return True
                    tokens = counter.count_prompt_tokens(messages, reasoning="off")
                    return (
                        tokens + cfg.editing.max_output_tokens + 1024
                        < cfg.llm.context_window
                    )

                selected = select_adaptive_context_results(
                    candidates,
                    min_limit=1,
                    max_limit=5,
                    include_tests=query_requests_tests(query),
                    fits=fits,
                )
            for result in selected:
                path = workspace.guard(self.repo, cfg, result.chunk.path)
                if (
                    result.source_hash
                    and workspace.sha(path.read_bytes()) != result.source_hash
                ):
                    raise ValueError(
                        f"Stale indexed source: {result.chunk.path}. Run devstill index."
                    )
            if not selected:
                raise ValueError(
                    "No usable source evidence; index the repository or specify a source symbol."
                )
            return records_for(selected)
        finally:
            search.close()

    def generate(self, messages, deadline):
        self.checkpoint(deadline)
        llm = self.config.llm
        # Budget the actual repair transcript as well as the initial evidence.
        if (
            sum(len(m["content"]) for m in messages)
            > self.config.editing.max_context_chars
        ):
            raise ValueError(
                "Editor transcript exceeds the configured character budget"
            )
        if llm.context_window:
            with AgentClient(llm) as counter:
                count = counter.count_prompt_tokens(messages, reasoning="off")
            if (
                count + self.config.editing.max_output_tokens + 1024
                > llm.context_window
            ):
                raise ValueError("Editor transcript exceeds the model context budget")
        response = self.client.post(
            completion_url(llm.base_url),
            headers={
                "Authorization": "Bearer " + (resolve_api_key(llm) or "devstill-local")
            },
            timeout=min(30, max(0.01, deadline - time.perf_counter())),
            json={
                "model": llm.model,
                "messages": messages,
                "max_tokens": self.config.editing.max_output_tokens,
                "chat_template_kwargs": {"enable_thinking": False},
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "repository_edits",
                        "strict": True,
                        "schema": response_schema(messages),
                    },
                },
            },
        )
        response.raise_for_status()
        self.checkpoint(deadline)
        choice = response.json()["choices"][0]
        if choice.get("finish_reason") != "stop":
            raise ValueError(f"Incomplete generation: {choice.get('finish_reason')}")
        return choice["message"]["content"]

    def prepare(self, query, *, report=lambda message: None):
        if self.closed:
            raise ValueError("Runtime session is closed")
        if (
            not query.strip()
            or not self.config.llm.enabled
            or not self.config.llm.model
        ):
            raise ValueError("A request and configured enabled model are required")
        self.cancelled.clear()
        started = time.perf_counter()
        deadline = started + self.config.editing.timeout_seconds
        self._deadline = deadline
        run_id = uuid4().hex
        directory = run_directory(self.root, run_id)
        directory.mkdir(parents=True)
        calls = 0
        proposal = {
            "version": 1,
            "id": run_id,
            "query": query,
            "repository": str(self.repo),
            "status": "preparing",
            "files": {},
            "patch": "",
            "evidence": [],
        }
        write(
            directory / "request.json",
            {
                "query": query,
                "model": self.config.llm.model,
                "editing": self.config.editing.model_dump(),
                "engine": "minimal-runtime:1",
            },
        )

        def emit(message):
            with (directory / "events.jsonl").open("a") as stream:
                stream.write(
                    json.dumps(
                        {"elapsed": time.perf_counter() - started, "message": message}
                    )
                    + "\n"
                )
            report(message)

        try:
            emit("Retrieving source evidence")
            requirements = delivery_requirements(self.repo, query)
            proposal["requirements"] = requirements
            records = self.evidence(query)
            proposal["evidence"] = records
            write(directory / "evidence.json", records)
            self.checkpoint(deadline)
            with tempfile.TemporaryDirectory(prefix="devstill-task-") as temporary:
                work = Path(temporary)
                sources = workspace.snapshot(self.repo, work, self.config)
                proposal["sources"] = sources
                for record in records:
                    if (
                        record["path"] not in sources
                        or record["code"] not in (work / record["path"]).read_text()
                    ):
                        raise ValueError(
                            f"Evidence no longer matches source: {record['path']}"
                        )

                def generate(messages, limit):
                    nonlocal calls
                    self.checkpoint(limit)
                    calls += 1
                    emit(f"Generating edits ({calls}/2)")
                    write(directory / f"request-{calls}.json", messages)
                    content = self.generate(messages, limit)
                    write(directory / f"response-{calls}.json", {"content": content})
                    return content

                def checks(repo, commands, limit):
                    emit("Checking candidate in isolated workspace")
                    before_checks = {
                        p.relative_to(work).as_posix(): workspace.sha(p.read_bytes())
                        for p in work.rglob("*")
                        if p.is_file() and "__pycache__" not in p.parts
                    }
                    results = run_checks(
                        repo,
                        commands,
                        limit,
                        lambda: self.checkpoint(limit),
                        self.config.editing.python,
                    )
                    for name, expected in before_checks.items():
                        path = workspace.source_path(work, name)
                        if (
                            not path.is_file()
                            or workspace.sha(path.read_bytes()) != expected
                        ):
                            raise RuntimeError(f"Check modified source: {name}")
                    return results

                outcome = edit_loop(
                    work,
                    query,
                    records,
                    generate,
                    directory,
                    deadline,
                    checkpoint=lambda: self.checkpoint(deadline),
                    check_runner=checks,
                    path_guard=lambda name: workspace.guard(work, self.config, name),
                    requirements=requirements,
                    required_checks=delivery_checks(requirements),
                )
                proposal["execution"] = outcome
                if outcome["status"] != "applied_unvalidated":
                    raise ValueError(outcome.get("error", "Editor did not finish"))
                files, patch = workspace.changes(
                    self.repo, work, sources, outcome["changed_files"]
                )
                if not files:
                    raise ValueError("No changed source produced")
                self.checkpoint(deadline)
                proposal.update(files=files, patch=patch, status="ready")
                emit("Patch ready for review")
        except Exception as exc:  # noqa: BLE001 - persist failed/cancelled task state
            proposal.update(
                status="cancelled" if isinstance(exc, InterruptedError) else "failed",
                error=str(exc),
            )
            emit(proposal["error"])
        proposal["model_calls"] = calls
        proposal["elapsed_seconds"] = time.perf_counter() - started
        proposal["digest"] = digest(proposal)
        write(directory / "proposal.json", proposal)
        write(
            directory / "result.json",
            {"id": run_id, "status": proposal["status"], "digest": proposal["digest"]},
        )
        (directory / "change.patch").write_text(proposal["patch"])
        return proposal

    def apply(self, run_id, expected_digest):
        if self.closed or self.cancelled.is_set():
            raise ValueError("Runtime session closed or cancelled")
        return workspace.apply(
            self.repo, self.root, run_id, expected_digest, self.config
        )

    def load(self, run_id):
        return load(self.root, run_id)
