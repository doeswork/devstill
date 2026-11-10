"""One shared prepare/apply engine for terminal UI, CLI and benchmarks."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path, PurePosixPath
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
from devstill.runtime.agent_loop import agent_loop
from devstill.runtime.artifacts import digest, load, run_directory, write
from devstill.runtime.checks import run_checks
from devstill.runtime.context import focused_candidates, support_records
from devstill.runtime.editor import edit_loop, editor_messages, response_schema
from devstill.runtime.jstest import JsGuard, pristine_copy
from devstill.runtime.loop_context import build_sections, guard_test_files
from devstill.runtime.requirements import delivery_checks, delivery_requirements
from devstill.runtime.toolchain import detect, link_dependencies
from devstill.runtime.tools import TestGuard, Workspace
from devstill.search import HybridSearch

DOC_SUFFIXES = {".rst", ".md", ".markdown", ".txt", ".adoc"}
SECRET_ENV = re.compile(r"KEY|TOKEN|SECRET|PASSW|CREDENTIAL|AUTH", re.IGNORECASE)
DOC_WORDS = re.compile(
    r"\b(docs?|documentation|readme|change ?log|release notes|sphinx|autodoc"
    r"|api reference|\.rst|\.md)\b",
    re.IGNORECASE,
)


def is_prose(chunk):
    path = PurePosixPath(chunk.path)
    return path.suffix.lower() in DOC_SUFFIXES or (
        bool(path.parts) and path.parts[0] in {"docs", "doc"}
    )


def code_first(results, query):
    """Rank code ahead of prose (changelogs, docs) unless the request is about docs."""
    if DOC_WORDS.search(query):
        return results

    def prose(result):
        path = PurePosixPath(result.chunk.path)
        return path.suffix.lower() in DOC_SUFFIXES or (
            path.parts and path.parts[0] in {"docs", "doc"}
        )

    return [r for r in results if not prose(r)] + [r for r in results if prose(r)]


PATH_TOKEN = re.compile(r"(?<![\w./-])((?:[\w.-]+/)+[\w.-]+\.\w+|[\w-]+\.(?:py|toml|cfg|ini|rst|md|txt|json|yaml|yml))(?![\w/])")


def named_file_records(repo, config, query, edge_lines=40):
    """Records for repository files the request names explicitly.

    Small files are shown whole; larger ones show their head (imports, setup)
    and their end, where new tests or entries are usually appended.
    """
    records = []
    for name in dict.fromkeys(PATH_TOKEN.findall(query)):
        name = name.strip("`'\",").rstrip(".").replace("\\", "/")
        name = name.removeprefix("./")
        try:
            path = workspace.guard(repo, config, name)
        except (ValueError, OSError):
            continue
        if not path.is_file() or git_ignored(repo, name):
            continue
        try:
            text = path.read_text()
        except (OSError, UnicodeDecodeError):
            continue
        lines = text.split("\n")
        if lines and lines[-1] == "":
            lines = lines[:-1]
        total = len(lines)
        if len(text) <= 6000 or total <= edge_lines * 2:
            spans = [(1, total)]
        else:
            spans = [(1, edge_lines), (total - edge_lines + 1, total)]
        for start, end in spans:
            records.append(
                {
                    "path": name,
                    "lines": [start, end],
                    "symbol": name,
                    "kind": "named_file",
                    "code": "\n".join(lines[start - 1 : end]),
                }
            )
    return records


def git_ignored(repo, name):
    """True when git ignores the path (ignored files never reach the model)."""
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), "check-ignore", "-q", "--", name],
            capture_output=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


QUALIFIED = re.compile(r"\b[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+\b")


def qualified_targets(ranked, chosen, query):
    """Code chunks named by a module-qualified reference (e.g. pkg.mod.func)."""
    from devstill.context import is_test_chunk

    names = set()
    for match in QUALIFIED.findall(query):
        parts = match.split(".")
        names.update(p for p in parts[1:] if len(p) >= 4)
    if not names:
        return []
    seen = {id(r) for r in chosen}
    found = []
    for result in ranked:
        chunk = result.chunk
        last = (chunk.qualname or chunk.name or "").split(".")[-1]
        if (
            id(result) not in seen
            and last in names
            and not is_prose(chunk)
            and not is_test_chunk(chunk)
        ):
            found.append(result)
            seen.add(id(result))
    return found[:2]


def alternates(ranked, chosen, query, files=2):
    """Best chunk from up to `files` other code files, in rank order."""
    from devstill.context import is_test_chunk

    seen = {r.chunk.path for r in chosen}
    extra = []
    tests = query_requests_tests(query)
    for result in code_first(ranked, query):
        chunk = result.chunk
        path = chunk.path
        if path in seen or (is_test_chunk(chunk) and not tests):
            continue
        if PurePosixPath(path).suffix.lower() in DOC_SUFFIXES:
            continue
        # A whole-class chunk rarely fits the budget; take a function or
        # method-sized chunk from the same file instead.
        if chunk.end_line - chunk.start_line > 80:
            continue
        seen.add(path)
        extra.append(result)
        if len(extra) >= files:
            break
    return extra


def needs_token_count(config, size):
    """Ask the server's tokenizer only when a prompt could plausibly overflow.

    `size` is the prompt's UTF-8 byte length. Byte-level tokenizers never emit
    more tokens than bytes, so a prompt whose byte size plus the output reserve
    fits the window cannot overflow; skipping /tokenize saves a round trip.
    """
    window = config.llm.context_window
    if not window:
        return False
    return size + config.editing.max_output_tokens + 1024 >= window


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


def render_agent_checks(execution):
    """The loop agent's own verification runs, for human review."""
    commands = execution.get("commands") or []
    if not commands:
        return ""
    rows = []
    for command in commands[-6:]:
        text = command["command"]
        label = (
            f"python check ({len(text.splitlines()) - 1} lines)"
            if text.startswith("python:\n")
            else f"test check ({len(text.splitlines()) - 1} lines)"
            if text.startswith("test:\n")
            else text.strip().splitlines()[0][:100]
        )
        rows.append(f"- {'passed' if command['ok'] else 'failed'}: {label}")
    shown = "Agent checks (latest last):\n" + "\n".join(rows) + "\n\n"
    last = next(
        (
            c
            for c in reversed(commands)
            if c["ok"] and c["command"].startswith(("python:\n", "test:\n"))
        ),
        None,
    )
    if last:
        code = "\n".join(last["command"].splitlines()[1:21])
        fence = "python" if last["command"].startswith("python:\n") else "ts"
        shown += f"Latest passing check:\n```{fence}\n" + code + "\n```\n\n"
    return shown


def render(proposal):
    files = ", ".join(f"`{p}`" for p in proposal.get("files", {})) or "none"
    context = ", ".join(
        f"`{p}`" for p in sorted({r["path"] for r in proposal.get("evidence", [])})
    )
    execution = proposal.get("execution") or {}
    if execution.get("engine") == "loop":
        validation = (
            f"Validation: agent checks below ({execution.get('model_calls', '?')} model "
            "calls); independent acceptance is not implied.\n\n"
        )
        details = render_agent_checks(execution)
        if execution.get("summary"):
            details += f"Summary: {execution['summary']}\n\n"
    else:
        validation = (
            "Validation: generated behavior checks; independent acceptance is not "
            "implied.\n\n"
        )
        details = render_check_changes(execution)
    return (
        f"## Task {proposal['id']}\n\n{proposal['query']}\n\n"
        f"Status: **{proposal['status']}**\n\nContext: {context or 'none'}\n\n"
        f"Changed files: {files}\n\n"
        + validation
        + details
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

    def evidence(self, query, budget_chars=None, lexical_first=False):
        cfg = self.config
        budget_chars = budget_chars or cfg.editing.max_context_chars
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
            if lexical_first:
                # The loop can search on its own, so a cold embedding-model
                # load (several seconds) is only worth paying when lexical
                # search finds no code at all; a warm session uses hybrid.
                from devstill.context import is_test_chunk

                warm = getattr(self.embedder, "_model", None) is not None
                code_hit = any(
                    not is_prose(r.chunk) and not is_test_chunk(r.chunk) for r in lexical
                )
                candidates = (
                    search.query(query, limit=12)
                    if warm or not code_hit
                    else lexical
                )
                candidates = code_first(candidates, query)
            else:
                candidates = (
                    lexical if len(unique_path) == 1 else search.query(query, limit=12)
                )

            ranked = candidates
            candidates = focused_candidates(candidates, query)
            alternate_ids = set()
            if lexical_first:
                # Module-qualified names (pkg.mod.func) count as named targets;
                # also name the next-best code locations: a wrong primary file
                # otherwise costs several exploratory calls.
                candidates = candidates + qualified_targets(ranked, candidates, query)
                extra = alternates(ranked, candidates, query)
                alternate_ids = {id(r) for r in extra}
                candidates = candidates + extra

            def records_for(results):
                records, complete_paths = [], set()
                for result in results:
                    record = context_record(result)
                    if id(result) in alternate_ids:
                        record["alternate"] = True
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
                support = support_records(
                    self.repo,
                    records,
                    requirements,
                    lambda name: workspace.guard(self.repo, cfg, name),
                )
                return records + [{**record, "support": True} for record in support]

            with AgentClient(cfg.llm) as counter:

                def fits(results):
                    if lexical_first:
                        # Size of what the loop actually renders, not the
                        # structured editor's prompt format.
                        size = len(query) + sum(
                            len(r["code"]) + 120 for r in records_for(results)
                        )
                        return size <= budget_chars
                    messages = editor_messages(
                        query, records_for(results), requirements
                    )
                    chars = sum(len(m["content"]) for m in messages)
                    if chars > budget_chars - 4000:
                        return False
                    size = sum(len(m["content"].encode()) for m in messages)
                    if not needs_token_count(cfg, size):
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
            if lexical_first and not selected and candidates:
                # Nothing fits whole (e.g. very large class chunks): keep the
                # best match; the loop's context renderer trims it to budget.
                selected = candidates[:1]
            for result in selected:
                path = workspace.guard(self.repo, cfg, result.chunk.path)
                if (
                    result.source_hash
                    and workspace.sha(path.read_bytes()) != result.source_hash
                ):
                    raise ValueError(
                        f"Stale indexed source: {result.chunk.path}. Run devstill index."
                    )
            named = named_file_records(self.repo, cfg, query) if lexical_first else []
            if not selected and not named and not lexical_first:
                raise ValueError(
                    "No usable source evidence; index the repository or specify a source symbol."
                )
            records = records_for(selected) if selected else []
            # Named files keep their head/tail view; the context renderer merges
            # overlapping ranges of the same file.
            return named + records
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
        if needs_token_count(
            self.config, sum(len(m["content"].encode()) for m in messages)
        ):
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

    def run_loop(
        self, work, query, records, requirements, chat, directory, deadline, emit
    ):
        """Run the tool loop; the workspace copy is made while the model reads.

        Returns (outcome, sources). The snapshot copies the very bytes the
        evidence was checked against, so the first model call does not wait
        for it; the first tool call (or delivery check) does.
        """
        cfg = self.config
        for record in records:
            path = workspace.source_path(self.repo, record["path"])
            if not path.is_file() or record["code"].replace("\r\n", "\n") not in (
                path.read_text()
            ):
                raise ValueError(f"Evidence no longer matches source: {record['path']}")
        # Commands never see credentials (API keys, tokens) from this process.
        env = {
            key: value
            for key, value in os.environ.items()
            if not SECRET_ENV.search(key) and key != cfg.llm.api_key_env
        }
        interpreter = (
            cfg.editing.python
            or shutil.which("python")
            or shutil.which("python3")
            or sys.executable
        )
        shims = Path(tempfile.mkdtemp(prefix="devstill-bin-"))
        # `python` in commands is the project interpreter (a shell shim keeps
        # virtualenv detection, unlike a symlink in another directory).
        (shims / "python").write_text(f'#!/bin/sh\nexec "{interpreter}" "$@"\n')
        (shims / "python").chmod(0o755)
        env["PATH"] = os.pathsep.join(
            (str(shims), str(Path(interpreter).parent), env.get("PATH", ""))
        )
        env["PYTHONPATH"] = os.pathsep.join(
            str(work / part)
            for part in ("src", "lib", "")
            if part == "" or (self.repo / part).is_dir()
        )
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        # Python unless the request is about JS/TS code in a package.json project.
        toolchain = detect(self.repo, records, env.get("PATH"))
        if toolchain.js:
            # Installed dependencies are linked, never copied or proposed.
            link_dependencies(self.repo, work, toolchain.dependencies)

        def allowed(name):
            try:
                workspace.guard(self.repo, cfg, name)
            except (ValueError, OSError):
                return False
            return True
        tools = Workspace(
            work,
            guard=lambda name: workspace.guard(work, cfg, name),
            resolve=lambda name: workspace.source_path(work, name),
            env=env,
            python=interpreter,
        )
        sections = (
            build_sections(
                self.repo,
                records,
                cfg.editing.loop_context_chars,
                allowed=allowed,
                language="js",
            )
            if toolchain.js
            else build_sections(
                self.repo, records, cfg.editing.loop_context_chars, allowed=allowed
            )
        )

        def checks(repo, commands, limit):
            return run_checks(
                repo,
                commands,
                limit,
                lambda: self.checkpoint(limit),
                interpreter,
            )

        def original(name):
            path = workspace.source_path(self.repo, name)
            return path.read_bytes() if path.is_file() else None

        guard = None
        baselines = None  # pristine copy for JS guard baselines
        if cfg.editing.regression_tests and toolchain.js:
            if toolchain.runner or toolchain.typescript:
                guard = JsGuard(tools, toolchain, original)
                baselines = Path(tempfile.mkdtemp(prefix="devstill-js-")) / "pristine"
        elif cfg.editing.regression_tests:
            suite = [
                name for name in ("tests", "test") if (self.repo / name).is_dir()
            ]
            guard = TestGuard(
                tools,
                lambda changed: guard_test_files(
                    work, changed, original, allowed=allowed
                ),
                original,
                suite=suite,
            )
        state = {}
        try:
            return self._loop_with_snapshot(
                work, query, sections, requirements, chat, directory, deadline, emit,
                tools, checks, guard, state, original, toolchain, baselines,
            )
        finally:
            shutil.rmtree(shims, ignore_errors=True)
            if baselines is not None:
                guard.cleanup()
                shutil.rmtree(baselines.parent, ignore_errors=True)

    def _loop_with_snapshot(
        self, work, query, sections, requirements, chat, directory, deadline, emit,
        tools, checks, guard, state, original, toolchain=None, baselines=None,
    ):
        cfg = self.config
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(workspace.snapshot, self.repo, work, cfg, True)
            if baselines is not None:

                def pristine():
                    # Baselines for the JS guard run in their own pristine
                    # copy, started while the model reads.
                    try:
                        guard.start(
                            pristine_copy(
                                self.repo, baselines, cfg, toolchain.dependencies
                            )
                        )
                    except Exception as exc:  # noqa: BLE001 - guard results stay unknown
                        emit(f"Test baselines unavailable: {exc}")

                pool.submit(pristine)

            def ready():
                if "sources" not in state:
                    state["sources"] = pending.result()
                    tools.tracked = [
                        name
                        for name, entry in state["sources"].items()
                        if not entry.get("support")
                    ]

            outcome = agent_loop(
                tools,
                query,
                sections,
                chat,
                directory,
                deadline,
                checkpoint=lambda: self.checkpoint(deadline),
                requirements=requirements,
                required_checks=delivery_checks(requirements),
                check_runner=checks,
                max_turns=cfg.editing.max_turns,
                bash_timeout=cfg.editing.bash_timeout_seconds,
                report=emit,
                before_tools=ready,
                guard=guard,
                originals=original,
                toolchain=toolchain if toolchain is not None and toolchain.js else None,
            )
            ready()
        return outcome, state["sources"]

    def chat(self, messages, tools, deadline):
        """One tool-calling completion; the deadline bounds the whole request."""
        self.checkpoint(deadline)
        llm = self.config.llm
        remaining = deadline - time.perf_counter()
        if remaining <= 0:
            raise TimeoutError("Editor deadline exhausted")
        response = self.client.post(
            completion_url(llm.base_url),
            headers={
                "Authorization": "Bearer " + (resolve_api_key(llm) or "devstill-local")
            },
            # Cancellation is checked between calls; cap a single call's wait.
            timeout=max(0.01, min(remaining, 120.0)),
            json={
                "model": llm.model,
                "messages": messages,
                "tools": tools,
                "max_tokens": self.config.editing.turn_output_tokens,
                "chat_template_kwargs": {"enable_thinking": False},
            },
        )
        response.raise_for_status()
        self.checkpoint(deadline)
        payload = response.json()
        choice = payload["choices"][0]
        return {
            "message": choice.get("message") or {},
            "finish_reason": choice.get("finish_reason"),
            "usage": payload.get("usage") or {},
        }

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

        loop = self.config.editing.engine == "loop"
        try:
            emit("Retrieving source evidence")
            requirements = delivery_requirements(self.repo, query)
            proposal["requirements"] = requirements
            records = (
                self.evidence(
                    query, self.config.editing.loop_context_chars, lexical_first=True
                )
                if loop
                else self.evidence(query)
            )
            proposal["evidence"] = records
            write(directory / "evidence.json", records)
            self.checkpoint(deadline)
            with tempfile.TemporaryDirectory(prefix="devstill-task-") as temporary:
                # Resolved: path checks refuse symlinked ancestors (e.g. TMPDIR).
                work = Path(temporary).resolve()
                if not loop:
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

                if loop:

                    def chat(messages, tools, limit):
                        nonlocal calls
                        self.checkpoint(limit)
                        calls += 1
                        write(directory / f"request-{calls}.json", messages)
                        reply = self.chat(messages, tools, limit)
                        write(directory / f"response-{calls}.json", reply)
                        return reply

                    outcome, sources = self.run_loop(
                        work,
                        query,
                        records,
                        requirements,
                        chat,
                        directory,
                        deadline,
                        emit,
                    )
                    proposal["sources"] = sources
                else:
                    outcome = edit_loop(
                        work,
                        query,
                        records,
                        generate,
                        directory,
                        deadline,
                        checkpoint=lambda: self.checkpoint(deadline),
                        check_runner=checks,
                        path_guard=lambda name: workspace.guard(
                            work, self.config, name
                        ),
                        requirements=requirements,
                        required_checks=delivery_checks(requirements),
                    )
                proposal["execution"] = outcome
                if outcome["status"] != "applied_unvalidated":
                    raise ValueError(outcome.get("error", "Editor did not finish"))
                files, patch = workspace.changes(
                    self.repo, work, sources, outcome["changed_files"], strict=not loop
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
