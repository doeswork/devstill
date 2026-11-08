"""Copy eligible working-tree files and journal only the proposed changes on apply."""

import base64
import difflib
import fcntl
import hashlib
import os
import stat
import tempfile
from contextlib import contextmanager

from devstill.content_policy import classify_content, matches_patterns
from devstill.git_files import discover_repository_inventory
from devstill.retrieval_policy import matches_retrieval_exclude
from devstill.runtime.artifacts import load, run_directory, write
from devstill.runtime.editor import source_path


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


# Supporting files (fixtures, empty package markers) copied for the agent loop
# so repository tests can run; sensitive and Devstill metadata never are.
SUPPORT_MAX_BYTES = 2_000_000
NEVER_COPIED = {"sensitive", "metadata"}


def guard(repo, config, name):
    path = source_path(repo, name)
    if matches_patterns(name, config.files.exclude) or matches_retrieval_exclude(
        name, config.retrieval.exclude
    ):
        raise ValueError(f"Excluded source: {name}")
    raw = path.read_bytes() if path.exists() else b"new source"
    decision = classify_content(path, name, raw, config)
    # Empty files (e.g. package __init__.py markers) are valid edit targets.
    if decision.disposition != "indexed" and decision.category != "empty":
        raise ValueError(f"Unsupported or excluded edit target: {name}")
    return path


def snapshot(repo, workspace, config, support=False):
    """Copy eligible sources; with support=True also small non-sensitive files."""
    manifest = {}
    inventory = discover_repository_inventory(repo, config)
    for item in inventory.files if support else inventory.eligible:
        if matches_retrieval_exclude(item.relative, config.retrieval.exclude):
            continue
        eligible = item.decision.disposition == "indexed"
        if not eligible and (
            item.decision.category in NEVER_COPIED
            or item.path.stat().st_size > SUPPORT_MAX_BYTES
        ):
            continue
        path = source_path(repo, item.relative)
        raw = path.read_bytes()
        target = workspace / item.relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
        mode = stat.S_IMODE(path.stat().st_mode)
        target.chmod(mode)
        manifest[item.relative] = {"sha256": sha(raw), "mode": mode}
        if not eligible and item.decision.category != "empty":
            # Fixtures and assets: copied for tests, never proposed or edited.
            # Empty files (package markers) stay editable sources.
            manifest[item.relative]["support"] = True
    return manifest


def changes(repo, workspace, before, allowed, strict=True):
    """Collect proposed file contents and their unified diff.

    strict (structured editor): reject check-side mutations, deletions and
    unreported source writes. Lenient (agent loop): scratch files and caches
    created by commands are ignored, deletions are not proposed, and eligible
    tracked files changed by a command (e.g. sed -i) are proposed as edits.
    """
    observed = {}
    allowed = set(allowed)
    for path in workspace.rglob("*"):
        relative = path.relative_to(workspace)
        if any(
            p in {"__pycache__", ".pytest_cache", ".git", ".devstill"}
            for p in relative.parts
        ):
            continue
        if path.is_symlink():
            if strict:
                raise ValueError("A check created a symlink")
            continue
        if not path.is_file():
            continue
        name = relative.as_posix()
        if not strict and name not in allowed:
            # Only the agent's own changes (edit/write, or tracked files its
            # commands changed) are proposed; test runs and scratch files are not.
            continue
        raw = path.read_bytes()
        observed[name] = raw
        if strict and name not in allowed and (
            name not in before or sha(raw) != before[name]["sha256"]
        ):
            raise ValueError(f"Check changed unproposed source: {name}")
    if strict and set(before) - set(observed):
        raise ValueError("File deletion is unsupported")
    result, patches = {}, []
    for name in sorted(allowed):
        if name not in observed:
            continue
        old = source_path(repo, name).read_bytes() if name in before else b""
        if name in before and sha(old) != before[name]["sha256"]:
            raise ValueError(f"Source changed during preparation: {name}")
        new = observed[name]
        if old == new:
            continue
        result[name] = {
            "content": base64.b64encode(new).decode(),
            "mode": before.get(name, {}).get("mode", 0o644),
            "sha256": sha(new),
        }
        patches.extend(
            difflib.unified_diff(
                old.decode().splitlines(keepends=True),
                new.decode().splitlines(keepends=True),
                fromfile="a/" + name,
                tofile="b/" + name,
            )
        )
    return result, "".join(patches)


def replace(path, raw, mode):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(name, mode)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


@contextmanager
def writer_lock(root):
    root.mkdir(parents=True, exist_ok=True)
    with (root / ".writer.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError("Another minimal run is applying changes") from exc
        yield


def apply(repo, root, run_id, expected_digest, config):
    with writer_lock(root):
        directory = run_directory(root, run_id)
        proposal = load(root, run_id)
        if (
            proposal["repository"] != str(repo.resolve())
            or proposal["digest"] != expected_digest
        ):
            raise ValueError("Proposal repository or approved digest does not match")
        status_path = directory / "result.json"
        import json

        state = json.loads(status_path.read_text())
        if state["status"] == "applied":
            return state
        if state["status"] != "ready":
            raise ValueError(f"Proposal cannot be applied: {state['status']}")
        if any(root.glob("*/apply-journal.json")):
            raise ValueError(
                "An interrupted application needs recovery; inspect apply-journal.json"
            )
        # Conservative initial policy: validation is tied to the entire copied source.
        for name, entry in proposal["sources"].items():
            if entry.get("support"):
                continue  # fixtures/assets copied for tests; never proposed
            path = source_path(repo, name)
            if (
                not path.is_file()
                or sha(path.read_bytes()) != entry["sha256"]
                or stat.S_IMODE(path.stat().st_mode) != entry["mode"]
            ):
                raise ValueError(f"Source changed after preparation: {name}")
        backups = {}
        for name in proposal["files"]:
            path = guard(repo, config, name)
            if name not in proposal["sources"] and path.exists():
                raise ValueError(f"New target already exists: {name}")
            backups[name] = (
                base64.b64encode(path.read_bytes()).decode() if path.exists() else None
            )
        journal = directory / "apply-journal.json"
        write(journal, {"before": backups, "after": proposal["files"]})
        written = []
        try:
            for name, entry in proposal["files"].items():
                path = guard(repo, config, name)
                current = (
                    base64.b64encode(path.read_bytes()).decode()
                    if path.exists()
                    else None
                )
                if current != backups[name]:
                    raise ValueError(f"Concurrent edit: {name}")
                replace(path, base64.b64decode(entry["content"]), entry["mode"])
                written.append(name)
            execution = proposal.get("execution") or {}
            if execution.get("engine") == "loop":
                label = (
                    "agent_check_passed" if execution.get("verified") else "unvalidated"
                )
            else:
                label = "generated_checks_passed"
            state = {**state, "status": "applied", "validation": label}
            write(status_path, state)
            (root.parent / ".tasks-need-index").write_text(run_id)
            journal.unlink()
            return state
        except BaseException:
            recovered = True
            for name in reversed(written):
                path = source_path(repo, name)
                if (
                    not path.exists()
                    or sha(path.read_bytes()) != proposal["files"][name]["sha256"]
                ):
                    recovered = False
                    continue
                try:
                    if backups[name] is None:
                        path.unlink()
                    else:
                        replace(
                            path,
                            base64.b64decode(backups[name]),
                            proposal["sources"][name]["mode"],
                        )
                except OSError:
                    recovered = False
            write(
                status_path,
                {**state, "status": "failed" if recovered else "recovery_required"},
            )
            if recovered:
                journal.unlink()
            raise
