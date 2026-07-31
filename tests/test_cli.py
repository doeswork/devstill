from __future__ import annotations

import json

from devstill import cli
from devstill.cli import parser
from devstill.config import DevstillConfig
from tests.test_context import make_result


def test_cli_exposes_index_and_search_options() -> None:
    index = parser().parse_args(["index", "--rebuild", "--verbose", "--json"])
    assert index.command == "index"
    assert index.rebuild and index.verbose and index.json

    search = parser().parse_args(
        [
            "search",
            "authentication",
            "--semantic-only",
            "--kind",
            "function",
            "--path",
            "src/",
            "--limit",
            "5",
        ]
    )
    assert search.command == "search"
    assert search.semantic_only
    assert search.kind == "function"
    assert search.path == "src/"
    assert search.limit == 5


def test_cli_exposes_context_defaults_and_options() -> None:
    context = parser().parse_args(
        [
            "context",
            "authentication flow",
            "--path",
            "core/",
            "--kind",
            "function",
            "--include-tests",
            "--semantic-only",
        ]
    )
    assert context.command == "context"
    assert context.limit == 5
    assert context.path == "core/"
    assert context.kind == "function"
    assert context.include_tests
    assert context.semantic_only
    assert not context.json


def test_context_command_outputs_compact_json(monkeypatch, tmp_path, capsys) -> None:
    calls = {}

    class FakeSearch:
        def __init__(self, config_path, config):
            calls["config_path"] = config_path

        def query(self, query, **kwargs):
            calls["query"] = query
            calls.update(kwargs)
            return [
                make_result(path="core/tests.py", name="test_login"),
                make_result(name="login_api"),
            ]

        def close(self):
            calls["closed"] = True

    config_path = tmp_path / "devstill.json"
    context_root = tmp_path / ".devstill"
    context_root.mkdir()
    (context_root / ".context.md").write_text("stale markdown", encoding="utf-8")
    (context_root / ".context.json").write_text("stale json", encoding="utf-8")
    monkeypatch.setattr(cli, "_configured", lambda: (config_path, DevstillConfig()))
    monkeypatch.setattr(cli, "HybridSearch", FakeSearch)
    args = parser().parse_args(["context", "login flow", "--limit", "1", "--json"])
    assert cli.context_command(args) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == [
        {
            "path": "core/views.py",
            "lines": [10, 11],
            "symbol": "login_api",
            "kind": "function",
            "code": "def login_api():\n    return True",
        }
    ]
    assert (
        json.loads((context_root / ".context.json").read_text(encoding="utf-8"))
        == payload
    )
    markdown = (context_root / ".context.md").read_text(encoding="utf-8")
    assert "stale markdown" not in markdown
    assert "core/views.py:10-11 — login_api (function)" in markdown
    assert calls["limit"] == 32
    assert calls["closed"]


def test_search_json_keeps_diagnostic_schema(monkeypatch, tmp_path, capsys) -> None:
    class FakeSearch:
        def __init__(self, config_path, config):
            pass

        def query(self, query, **kwargs):
            return [make_result()]

        def close(self):
            pass

    monkeypatch.setattr(
        cli,
        "_configured",
        lambda: (tmp_path / "devstill.json", DevstillConfig()),
    )
    monkeypatch.setattr(cli, "HybridSearch", FakeSearch)
    args = parser().parse_args(["search", "login", "--json"])
    assert cli.search_command(args) == 0
    payload = json.loads(capsys.readouterr().out)
    assert "id" in payload[0]
    assert "content_hash" in payload[0]
    assert "parser_metadata" in payload[0]
    assert "scores" in payload[0]
