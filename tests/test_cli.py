from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from devstill import cli
from devstill.cli import parser
from devstill.config import DevstillConfig, load_config, write_default_config
from devstill.models import IndexSummary
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

    files = parser().parse_args(["files", "--ignored", "--json"])
    assert files.command == "files"
    assert files.ignored
    assert files.json


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


def test_cli_exposes_agent_and_plan_commands() -> None:
    show = parser().parse_args(["agent"])
    assert show.command == "agent"
    assert show.agent_action is None

    configure = parser().parse_args(
        [
            "agent",
            "configure",
            "--base-url",
            "http://localhost:8080/v1",
            "--model",
            "local-model",
            "--api-key-env",
            "LOCAL_SECRET",
            "--timeout",
            "30",
            "--max-output-tokens",
            "1024",
        ]
    )
    assert configure.agent_action == "configure"
    assert configure.model == "local-model"
    assert configure.timeout == 30
    assert configure.max_output_tokens == 1024

    plan = parser().parse_args(
        [
            "plan",
            "add activation",
            "--path",
            "core/",
            "--kind",
            "function",
            "--include-tests",
            "--lexical-only",
        ]
    )
    assert plan.command == "plan"
    assert plan.limit == 5
    assert plan.include_tests
    assert plan.lexical_only
    with pytest.raises(SystemExit):
        parser().parse_args(["plan", "query", "--exact", "--semantic-only"])


def test_agent_configure_from_nested_directory_and_secret_redaction(
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    config_path = write_default_config(tmp_path / "devstill.json")
    nested = tmp_path / "src" / "feature"
    nested.mkdir(parents=True)
    monkeypatch.chdir(nested)
    monkeypatch.setenv("LOCAL_AGENT_KEY", "do-not-print-this-secret")
    args = parser().parse_args(
        [
            "agent",
            "configure",
            "--base-url",
            "http://localhost:9000/v1/",
            "--model",
            "local-12b",
            "--api-key-env",
            "LOCAL_AGENT_KEY",
        ]
    )
    assert cli.agent_command(args) == 0
    configured = load_config(config_path)
    assert configured.llm.enabled
    assert configured.llm.base_url == "http://localhost:9000/v1"
    assert configured.llm.model == "local-12b"
    output = capsys.readouterr().out
    assert "LOCAL_AGENT_KEY (set)" in output
    assert "do-not-print-this-secret" not in output

    assert cli.agent_command(parser().parse_args(["agent", "disable"])) == 0
    assert not load_config(config_path).llm.enabled
    assert cli.agent_command(parser().parse_args(["agent", "enable"])) == 0
    assert load_config(config_path).llm.enabled


def test_agent_test_uses_configured_client(monkeypatch, tmp_path, capsys) -> None:
    config = DevstillConfig(
        llm={
            "enabled": True,
            "base_url": "http://localhost:8080/v1",
            "model": "local-model",
        }
    )
    calls: dict[str, object] = {}

    class FakeAgentClient:
        def __init__(self, llm):
            calls["config"] = llm

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            calls["closed"] = True

        def test(self):
            return SimpleNamespace(
                model="local-model",
                endpoint="http://localhost:8080/v1/chat/completions",
                elapsed_seconds=0.125,
            )

    monkeypatch.setattr(
        cli, "_configured", lambda: (tmp_path / "devstill.json", config)
    )
    monkeypatch.setattr(cli, "AgentClient", FakeAgentClient)
    assert cli.agent_command(parser().parse_args(["agent", "test"])) == 0
    assert calls["closed"]
    assert "Agent OK" in capsys.readouterr().out


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


def test_plan_command_outputs_json_writes_artifacts_and_preserves_context(
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    source = tmp_path / "core" / "views.py"
    source.parent.mkdir()
    source.write_text("def register_api():\n    pass\n", encoding="utf-8")
    context_root = tmp_path / ".devstill"
    context_root.mkdir()
    (context_root / ".context.md").write_text("keep markdown\n", encoding="utf-8")
    (context_root / ".context.json").write_text("keep json\n", encoding="utf-8")
    config = DevstillConfig(
        llm={
            "enabled": True,
            "base_url": "http://localhost:8080/v1",
            "model": "local-12b",
        }
    )
    calls: dict[str, object] = {}

    class FakeSearch:
        def __init__(self, config_path, config):
            pass

        def query(self, query, **kwargs):
            calls["query"] = query
            calls.update(kwargs)
            return [
                make_result(path="core/tests.py", name="test_register"),
                make_result(path="core/views.py", name="register_api"),
            ]

        def close(self):
            calls["search_closed"] = True

    class FakeAgentClient:
        def __init__(self, llm):
            self.config = llm
            self.endpoint = "http://localhost:8080/v1/chat/completions"

        def chat(self, messages, **kwargs):
            calls["messages"] = messages
            calls["schema"] = kwargs["schema"]
            return json.dumps(
                {
                    "items": [
                        {
                            "label": "Add",
                            "target": "core/views.py",
                            "reason": "Add activation-code handling to registration.",
                        },
                        {
                            "label": "New File",
                            "target": "core/services/activation.py",
                            "reason": "Create a focused activation service.",
                        },
                    ]
                }
            )

        def close(self):
            calls["agent_closed"] = True

    monkeypatch.setattr(
        cli, "_configured", lambda: (tmp_path / "devstill.json", config)
    )
    monkeypatch.setattr(cli, "HybridSearch", FakeSearch)
    monkeypatch.setattr(cli, "AgentClient", FakeAgentClient)
    args = parser().parse_args(
        ["plan", "add activation", "--limit", "1", "--lexical-only", "--json"]
    )
    assert cli.plan_command(args) == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["query"] == "add activation"
    assert payload["repository"] == str(tmp_path)
    assert payload["model"] == "local-12b"
    assert [item["label"] for item in payload["items"]] == ["Add", "New File"]
    assert "Sending 1 source chunks" in captured.err
    assert "http://localhost:8080/v1/chat/completions" in captured.err
    assert calls["limit"] == 32
    assert calls["lexical_only"] is True
    assert calls["search_closed"] and calls["agent_closed"]
    assert (
        json.loads((context_root / ".plan.json").read_text(encoding="utf-8")) == payload
    )
    assert "Add — core/views.py" in (context_root / ".plan.md").read_text(
        encoding="utf-8"
    )
    assert (context_root / ".context.md").read_text(encoding="utf-8") == (
        "keep markdown\n"
    )
    assert (context_root / ".context.json").read_text(encoding="utf-8") == "keep json\n"


def test_failed_plan_preserves_previous_artifacts(monkeypatch, tmp_path: Path) -> None:
    context_root = tmp_path / ".devstill"
    context_root.mkdir()
    plan_markdown = context_root / ".plan.md"
    plan_json = context_root / ".plan.json"
    plan_markdown.write_text("previous markdown\n", encoding="utf-8")
    plan_json.write_text("previous json\n", encoding="utf-8")
    config = DevstillConfig(
        llm={"enabled": True, "base_url": "http://localhost/v1", "model": "model"}
    )

    class FakeSearch:
        def __init__(self, *_args):
            pass

        def query(self, *_args, **_kwargs):
            return []

        def close(self):
            pass

    class FakeAgentClient:
        endpoint = "http://localhost/v1/chat/completions"

        def __init__(self, llm):
            self.config = llm

        def close(self):
            pass

    monkeypatch.setattr(
        cli, "_configured", lambda: (tmp_path / "devstill.json", config)
    )
    monkeypatch.setattr(cli, "HybridSearch", FakeSearch)
    monkeypatch.setattr(cli, "AgentClient", FakeAgentClient)
    monkeypatch.setattr(
        cli,
        "generate_plan",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("invalid plan")),
    )
    with pytest.raises(ValueError, match="invalid plan"):
        cli.plan_command(parser().parse_args(["plan", "query"]))
    assert plan_markdown.read_text(encoding="utf-8") == "previous markdown\n"
    assert plan_json.read_text(encoding="utf-8") == "previous json\n"


def test_parse_json_includes_parser_and_quality(monkeypatch, tmp_path, capsys) -> None:
    path = tmp_path / "notes.custom"
    path.write_text("# Notes\n\nUseful context.\n", encoding="utf-8")
    monkeypatch.setattr(
        cli,
        "_configured",
        lambda: (tmp_path / "devstill.json", DevstillConfig()),
    )

    assert cli.parse_command(str(path), as_json=True) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["parser"] == "plain_text"
    assert payload["binary"] is False
    assert payload["quality"]["status"] == "MEH"
    assert payload["quality"]["path"] == "notes.custom"


def test_index_quality_human_and_json_output(monkeypatch, tmp_path, capsys) -> None:
    summary = IndexSummary(
        repository=str(tmp_path),
        good_files=7,
        meh_files=2,
        bad_files=1,
        binary_files_skipped=3,
        index_path=str(tmp_path / ".devstill"),
    )

    class FakeIndexer:
        def __init__(self, config_path, config):
            pass

        def run(self, rebuild=False, verbose=False):
            return summary

        def close(self):
            pass

    monkeypatch.setattr(
        cli,
        "_configured",
        lambda: (tmp_path / "devstill.json", DevstillConfig()),
    )
    monkeypatch.setattr(cli, "RepositoryIndexer", FakeIndexer)

    assert cli.index_command(False, False, False) == 0
    human = capsys.readouterr().out
    assert "Index quality:" in human
    assert "GOOD: 7" in human
    assert "MEH:  2" in human
    assert "BAD:   1" in human
    assert "Binary: 3" in human

    assert cli.index_command(False, True, True) == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["good_files"] == 7
    assert payload["binary_files_skipped"] == 3
