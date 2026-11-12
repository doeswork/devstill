"""Bounded exact-file context for reviewed feature task inputs and writes."""

import re
from dataclasses import replace

from devstill.executor import _safe_target
from devstill.models import ChunkPolicy, SearchResult, SourceChunk
from devstill.parsers.base import parse_source_file
from devstill.tasks import digest_file


def task_candidates(repo, task, candidates):
    paths = list(
        dict.fromkeys(
            [i.path for i in task.inputs]
            + [w.path for w in task.writes if w.operation != "create"]
        )
    )
    if len(paths) > 5:
        raise ValueError(
            "Task requires more than five existing context files; narrow its scope"
        )
    pinned = []
    words = set(re.findall(r"[a-zA-Z_][a-zA-Z_0-9]+", task.outcome.lower()))
    for path in paths:
        found = [r for r in candidates if r.chunk.path == path]
        if not found:
            target = _safe_target(repo, path, must_exist=True)
            if target.stat().st_size > 1024 * 1024:
                raise ValueError(f"Required context file exceeds size limit: {path}")
            parsed = parse_source_file(
                target, repo, policy=ChunkPolicy(max_chars=4000, max_lines=120)
            )
            chunks = [
                c for c in parsed.chunks if c.parser_metadata.get("searchable", True)
            ]
            if not chunks and target.stat().st_size == 0:
                chunks = [
                    SourceChunk(
                        id=f"feature-empty:{path}",
                        path=path,
                        filename=target.name,
                        language="text",
                        kind="file",
                        name=None,
                        qualname=None,
                        parent_id=None,
                        start_line=1,
                        end_line=1,
                        code="",
                        leading_comments=None,
                        content_hash=digest_file(target),
                    )
                ]
            chunks.sort(
                key=lambda c: len(
                    words
                    & set(
                        re.findall(
                            r"[a-zA-Z_][a-zA-Z_0-9]+",
                            ((c.qualname or c.name or "") + " " + c.code).lower(),
                        )
                    )
                ),
                reverse=True,
            )
            found = [
                SearchResult(c, 1.0, {"path": 1.0}, digest_file(target))
                for c in chunks[:2]
            ]
        if not found:
            raise ValueError(f"Required context has no readable source chunks: {path}")
        pinned.append(replace(found[0], scores={**found[0].scores, "path": 1.0}))
    seen = {r.chunk.id for r in pinned}
    # Reviewed tasks already name their scope. Broad semantic neighbors can make
    # Quick propose unrelated test/doc edits. Keep only scoped context, or two
    # convention examples for a task creating files with no existing inputs.
    extra = [r for r in candidates if r.chunk.id not in seen and r.chunk.path in paths]
    return [*pinned, *extra][:12] if paths else candidates[:2]
