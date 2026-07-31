from __future__ import annotations

import subprocess

from devstill.git_files import discover_repository_files


def test_git_discovery_respects_ignores_and_tracked_files(
    configured_repo: tuple,
) -> None:
    repo, _, config = configured_repo
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / ".gitignore").write_text("ignored.py\n.devstill/\n", encoding="utf-8")
    (repo / "tracked.py").write_text("tracked = True\n", encoding="utf-8")
    (repo / "ignored.py").write_text("still_tracked = True\n", encoding="utf-8")
    (repo / "notes.md").write_text("notes\n", encoding="utf-8")
    (repo / ".devstill").mkdir()
    (repo / ".devstill" / "hidden.py").write_text("hidden = True\n", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(repo), "add", "tracked.py", "notes.md"], check=True
    )
    subprocess.run(["git", "-C", str(repo), "add", "-f", "ignored.py"], check=True)

    found = [
        path.relative_to(repo).as_posix()
        for path in discover_repository_files(repo, config)
    ]

    assert found == ["ignored.py", "notes.md", "tracked.py"]


def test_fallback_scanner_warns_and_excludes_internal_directories(
    configured_repo: tuple,
) -> None:
    repo, _, config = configured_repo
    (repo / "source.py").write_text("value = 1\n", encoding="utf-8")
    (repo / ".venv").mkdir()
    (repo / ".venv" / "bad.py").write_text("bad = 1\n", encoding="utf-8")

    with __import__("pytest").warns(UserWarning, match="not a Git work tree"):
        found = discover_repository_files(repo, config)

    assert [path.name for path in found] == ["source.py"]
