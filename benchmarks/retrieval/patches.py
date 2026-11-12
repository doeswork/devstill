"""Small, dependency-free unified-diff helpers shared by benchmark tools."""

from __future__ import annotations

import re

HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+\d+(?:,\d+)? @@")


def patch_files(patch):
    """Changed paths of a git diff, in order (new path for renames)."""
    files = []
    for line in patch.splitlines():
        if line.startswith("diff --git "):
            _, _, new = line.partition(" b/")
            files.append(new or line.split()[-1])
    return files


def patch_stats(patch):
    """Files, added and removed lines of a git diff (binary hunks count 0)."""
    added = removed = 0
    in_hunk = False
    for line in patch.splitlines():
        if line.startswith("diff --git "):
            in_hunk = False
        elif HUNK.match(line):
            in_hunk = True
        elif in_hunk and line.startswith("+"):
            added += 1
        elif in_hunk and line.startswith("-"):
            removed += 1
    return {"files": patch_files(patch), "added": added, "removed": removed}


def is_test_path(path):
    parts = path.lower().split("/")
    name = parts[-1]
    return (
        any(part in {"test", "tests", "testing"} for part in parts[:-1])
        or name.startswith("test_")
        or name.endswith("_test.py")
        or name in {"conftest.py", "tests.py"}
    )
