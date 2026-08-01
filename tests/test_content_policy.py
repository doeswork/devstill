from __future__ import annotations

import json
import subprocess
from pathlib import Path

from devstill import cli
from devstill.config import DevstillConfig
from devstill.git_files import discover_repository_inventory
from devstill.parsers import parse_source_file


def _git_repo(tmp_path: Path, files: dict[str, bytes]) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    for name, raw in files.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    subprocess.run(["git", "-C", str(repo), "add", "-f", "."], check=True)
    return repo


def test_inventory_separates_assets_secrets_noisy_binary_and_empty(
    tmp_path: Path,
) -> None:
    repo = _git_repo(
        tmp_path,
        {
            "README.md": b"# Project\n",
            "notes.TXT": b"Useful notes\n",
            "icon.SVG": b'<svg xmlns="http://www.w3.org/2000/svg"/>\n',
            "photo.png": b"\x89PNG\r\n\x1a\n\x00data",
            "package-lock.json": b"{}\n",
            "devstill.json": b"{}\n",
            ".env": b"SECRET=value\n",
            ".env.example": b"SECRET=\n",
            ".env.development.example": b"SECRET=\n",
            "empty.py": b"",
            "whitespace.py": b" \n\t\n",
            "opaque.bin": b"\x00binary",
        },
    )

    inventory = discover_repository_inventory(repo, DevstillConfig())
    decisions = {item.relative: item.decision for item in inventory.files}

    assert decisions["README.md"].category == "documentation"
    assert decisions["notes.TXT"].category == "documentation"
    assert decisions[".env.example"].category == "configuration"
    assert decisions[".env.development.example"].category == "configuration"
    for path in ("icon.SVG", "photo.png", "devstill.json", ".env"):
        assert decisions[path].disposition == "ignored"
    assert decisions["package-lock.json"].category == "generated"
    assert decisions["package-lock.json"].disposition == "ignored"
    assert decisions["empty.py"].category == "empty"
    assert decisions["empty.py"].disposition == "skipped"
    assert decisions["whitespace.py"].category == "empty"
    assert decisions["opaque.bin"].category == "binary"
    assert decisions["opaque.bin"].disposition == "skipped"


def test_user_exclude_wins_and_include_ignored_is_path_specific(tmp_path: Path) -> None:
    repo = _git_repo(
        tmp_path,
        {
            "architecture.svg": b"<svg><text>auth flow</text></svg>\n",
            "private.svg": b"<svg/>\n",
            "app.min.js": b"const generated = true;\n",
        },
    )
    config = DevstillConfig.model_validate(
        {
            "files": {
                "scan_all_text": True,
                "include_ignored": ["architecture.svg", "private.svg"],
                "exclude": ["private.svg"],
            }
        }
    )

    inventory = discover_repository_inventory(repo, config)
    decisions = {item.relative: item.decision for item in inventory.files}

    assert "private.svg" not in decisions
    assert decisions["architecture.svg"].disposition == "indexed"
    assert decisions["architecture.svg"].category == "unknown_text"
    assert decisions["app.min.js"].disposition == "ignored"
    result = parse_source_file(
        repo / "architecture.svg",
        repo,
        include_ignored=config.files.include_ignored,
    )
    assert result.quality is not None
    assert result.quality.status == "MEH"
    assert result.quality.disposition == "indexed"


def test_index_noisy_files_does_not_enable_assets(tmp_path: Path) -> None:
    repo = _git_repo(
        tmp_path,
        {"icon.svg": b"<svg/>\n", "bundle.min.js": b"const x = 1;\n"},
    )
    config = DevstillConfig.model_validate(
        {"files": {"scan_all_text": True, "index_noisy_files": True}}
    )
    decisions = {
        item.relative: item.decision
        for item in discover_repository_inventory(repo, config).files
    }
    assert decisions["icon.svg"].disposition == "ignored"
    assert decisions["bundle.min.js"].disposition == "indexed"
    assert decisions["bundle.min.js"].noisy


def test_files_json_and_ignored_views(monkeypatch, tmp_path: Path, capsys) -> None:
    repo = _git_repo(
        tmp_path,
        {"README.md": b"# Docs\n", "icon.svg": b"<svg/>\n"},
    )
    monkeypatch.setattr(
        cli,
        "_configured",
        lambda: (repo / "devstill.json", DevstillConfig()),
    )

    assert cli.files_command(as_json=True, count=False) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["files"] == ["README.md"]
    assert payload["ignored_count"] == 1
    assert payload["ignored_files"][0]["path"] == "icon.svg"

    assert cli.files_command(as_json=False, count=False, ignored=True) == 0
    output = capsys.readouterr().out
    assert "icon.svg\tasset\timage/vector asset (.svg)" in output


def test_git_visible_symlink_is_not_followed(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path, {"real.md": b"# Real\n"})
    link = repo / "linked.md"
    link.symlink_to("real.md")
    subprocess.run(["git", "-C", str(repo), "add", "linked.md"], check=True)

    inventory = discover_repository_inventory(repo, DevstillConfig())

    assert [item.relative for item in inventory.files] == ["real.md"]
