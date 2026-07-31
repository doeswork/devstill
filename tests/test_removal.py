from __future__ import annotations

import json
from pathlib import Path

import pytest

from devstill import cli
from devstill.config import default_config_dict, load_config, write_default_config
from devstill.removal import build_removal_plan, execute_removal
from devstill.repository import (
    MANAGED_GITIGNORE_BLOCK,
    ensure_devstill_gitignore,
    has_managed_gitignore_block,
    remove_managed_gitignore_block,
)


def configured_repository(tmp_path: Path) -> tuple[Path, object]:
    config_path = write_default_config(tmp_path / "devstill.json")
    ensure_devstill_gitignore(tmp_path / ".gitignore")
    return config_path, load_config(config_path)


def test_managed_gitignore_round_trip_and_user_owned_entry(tmp_path: Path) -> None:
    ignore = tmp_path / ".gitignore"
    ignore.write_text("*.pyc\n", encoding="utf-8")

    assert ensure_devstill_gitignore(ignore)
    assert ignore.read_text(encoding="utf-8") == f"*.pyc\n\n{MANAGED_GITIGNORE_BLOCK}"
    assert has_managed_gitignore_block(ignore)
    assert remove_managed_gitignore_block(ignore)
    assert ignore.read_text(encoding="utf-8") == "*.pyc\n"

    ignore.write_text("*.pyc\n.devstill/\n", encoding="utf-8")
    assert not ensure_devstill_gitignore(ignore)
    assert not has_managed_gitignore_block(ignore)
    assert not remove_managed_gitignore_block(ignore)
    assert ignore.read_text(encoding="utf-8") == "*.pyc\n.devstill/\n"


def test_full_removal_deletes_default_index_config_and_managed_ignore(
    tmp_path: Path,
) -> None:
    config_path, config = configured_repository(tmp_path)
    index = tmp_path / ".devstill"
    (index / "chroma").mkdir(parents=True)
    (index / "chroma" / "chroma.sqlite3").write_text("db", encoding="utf-8")
    (index / "metadata.sqlite").write_text("db", encoding="utf-8")

    plan = build_removal_plan(config_path, config, index_only=False)
    result = execute_removal(plan)

    assert result.succeeded
    assert result.config_removed
    assert result.gitignore_updated
    assert not index.exists()
    assert not config_path.exists()
    assert not has_managed_gitignore_block(tmp_path / ".gitignore")


def test_index_only_is_idempotent_and_preserves_repository_setup(
    tmp_path: Path,
) -> None:
    config_path, config = configured_repository(tmp_path)
    index = tmp_path / ".devstill"
    index.mkdir()
    (index / "metadata.sqlite").write_text("db", encoding="utf-8")
    plan = build_removal_plan(config_path, config, index_only=True)

    first = execute_removal(plan)
    second = execute_removal(plan)

    assert first.succeeded and second.succeeded
    assert not index.exists()
    assert config_path.exists()
    assert has_managed_gitignore_block(tmp_path / ".gitignore")


def test_custom_layout_removes_known_artifacts_and_preserves_unrelated_files(
    tmp_path: Path,
) -> None:
    raw = default_config_dict()
    raw["index"]["path"] = ".custom/chroma"
    config_path = tmp_path / "devstill.json"
    config_path.write_text(json.dumps(raw), encoding="utf-8")
    config = load_config(config_path)
    root = tmp_path / ".custom"
    (root / "chroma").mkdir(parents=True)
    (root / "cache").mkdir()
    (root / "chroma" / "data.sqlite").write_text("db", encoding="utf-8")
    (root / "metadata.sqlite").write_text("db", encoding="utf-8")
    (root / ".context.md").write_text("context", encoding="utf-8")
    unrelated = root / "keep-me.txt"
    unrelated.write_text("mine", encoding="utf-8")

    plan = build_removal_plan(config_path, config, index_only=True)
    result = execute_removal(plan)

    assert result.succeeded
    assert not (root / "chroma").exists()
    assert not (root / "cache").exists()
    assert not (root / "metadata.sqlite").exists()
    assert not (root / ".context.md").exists()
    assert unrelated.read_text(encoding="utf-8") == "mine"
    assert result.preserved == [root]
    assert config_path.exists()


def test_default_index_symlink_is_unlinked_without_touching_target(
    tmp_path: Path,
) -> None:
    config_path, config = configured_repository(tmp_path)
    target = tmp_path / "external-index"
    target.mkdir()
    marker = target / "keep.txt"
    marker.write_text("keep", encoding="utf-8")
    link = tmp_path / ".devstill"
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlinks are unavailable: {exc}")

    result = execute_removal(build_removal_plan(config_path, config, index_only=True))

    assert result.succeeded
    assert not link.exists()
    assert not link.is_symlink()
    assert marker.read_text(encoding="utf-8") == "keep"


def test_full_removal_unlinks_config_symlink_without_deleting_target(
    tmp_path: Path,
) -> None:
    external = tmp_path / "external"
    external.mkdir()
    target_config = write_default_config(external / "shared.json")
    config_link = tmp_path / "devstill.json"
    try:
        config_link.symlink_to(target_config)
    except OSError as exc:
        pytest.skip(f"symlinks are unavailable: {exc}")
    config = load_config(config_link)

    result = execute_removal(build_removal_plan(config_link, config, index_only=False))

    assert result.succeeded
    assert not config_link.is_symlink()
    assert target_config.is_file()


def test_symlinked_gitignore_is_not_modified_and_config_is_preserved(
    tmp_path: Path,
) -> None:
    config_path = write_default_config(tmp_path / "devstill.json")
    external_ignore = tmp_path / "external.gitignore"
    external_ignore.write_text(MANAGED_GITIGNORE_BLOCK, encoding="utf-8")
    ignore_link = tmp_path / ".gitignore"
    try:
        ignore_link.symlink_to(external_ignore)
    except OSError as exc:
        pytest.skip(f"symlinks are unavailable: {exc}")

    result = execute_removal(
        build_removal_plan(
            config_path,
            load_config(config_path),
            index_only=False,
        )
    )

    assert not result.succeeded
    assert "symlinked .gitignore" in result.errors[0][1]
    assert config_path.exists()
    assert external_ignore.read_text(encoding="utf-8") == MANAGED_GITIGNORE_BLOCK


def test_custom_symlink_parent_and_broad_root_are_rejected(tmp_path: Path) -> None:
    raw = default_config_dict()
    raw["index"]["path"] = "chroma"
    config_path = tmp_path / "devstill.json"
    config_path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError, match="dangerously broad"):
        build_removal_plan(config_path, load_config(config_path), index_only=True)

    external = tmp_path / "external"
    external.mkdir()
    linked = tmp_path / "linked"
    try:
        linked.symlink_to(external, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlinks are unavailable: {exc}")
    raw["index"]["path"] = "linked/chroma"
    config_path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError, match="symlinked custom index parent"):
        build_removal_plan(config_path, load_config(config_path), index_only=True)


def test_cleanup_failure_preserves_config_for_retry(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config_path, config = configured_repository(tmp_path)
    index = tmp_path / ".devstill"
    index.mkdir()
    original_remove = __import__("devstill.removal", fromlist=["_remove_path"])
    remove_path = original_remove._remove_path

    def fail_index(path: Path) -> bool:
        if path == index:
            raise PermissionError("locked")
        return remove_path(path)

    monkeypatch.setattr("devstill.removal._remove_path", fail_index)
    result = execute_removal(build_removal_plan(config_path, config, index_only=False))

    assert not result.succeeded
    assert result.errors[0][0] == index
    assert config_path.exists()
    assert has_managed_gitignore_block(tmp_path / ".gitignore")


def test_remove_cli_dry_run_nested_discovery_and_confirmation(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    config_path, _ = configured_repository(tmp_path)
    index = tmp_path / ".devstill"
    index.mkdir(exist_ok=True)
    nested = tmp_path / "src" / "feature"
    nested.mkdir(parents=True)
    monkeypatch.chdir(nested)

    assert cli.remove_repo(False, True, False) == 0
    assert config_path.exists() and index.exists()
    assert "Dry run: no files were removed" in capsys.readouterr().out

    class InteractiveInput:
        @staticmethod
        def isatty() -> bool:
            return True

    monkeypatch.setattr(cli.sys, "stdin", InteractiveInput())
    monkeypatch.setattr("builtins.input", lambda _prompt: "")
    assert cli.remove_repo(False, False, False) == 0
    assert config_path.exists() and index.exists()
    assert "Removal cancelled" in capsys.readouterr().out


def test_remove_cli_requires_yes_noninteractively(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config_path, config = configured_repository(tmp_path)
    monkeypatch.setattr(cli, "_configured", lambda: (config_path, config))

    class NonInteractiveInput:
        @staticmethod
        def isatty() -> bool:
            return False

    monkeypatch.setattr(cli.sys, "stdin", NonInteractiveInput())
    with pytest.raises(ValueError, match="Re-run with --yes"):
        cli.remove_repo(False, False, False)
    assert config_path.exists()


def test_remove_cli_yes_executes_without_prompt(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    config_path, config = configured_repository(tmp_path)
    index = tmp_path / ".devstill"
    index.mkdir()
    monkeypatch.setattr(cli, "_configured", lambda: (config_path, config))

    assert cli.remove_repo(True, False, True) == 0

    assert not index.exists()
    assert config_path.exists()
    assert "Removed Devstill index data" in capsys.readouterr().out


def test_remove_cli_failure_returns_nonzero_and_lists_path(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    config_path, config = configured_repository(tmp_path)
    index = tmp_path / ".devstill"
    index.mkdir()
    monkeypatch.setattr(cli, "_configured", lambda: (config_path, config))

    def fail(_path: Path) -> bool:
        raise PermissionError("locked by another process")

    monkeypatch.setattr("devstill.removal._remove_path", fail)

    assert cli.remove_repo(False, False, True) == 1

    output = capsys.readouterr().out
    assert "removal was incomplete" in output
    assert str(index) in output.replace("\n", "")
    assert config_path.exists()


def test_remove_cli_flags() -> None:
    args = cli.parser().parse_args(["remove", "--index-only", "--dry-run", "--yes"])
    assert args.command == "remove"
    assert args.index_only
    assert args.dry_run
    assert args.yes
