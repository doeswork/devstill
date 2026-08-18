from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from devstill import cli
from devstill.cli import parser
from devstill.config import DevstillConfig, load_config, write_default_config
from devstill.installation import InstallMetadata
from devstill.models import IndexFileProgress, IndexProgress, IndexSummary
from devstill.updater import Release
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

    device = parser().parse_args(["device", "cuda"])
    assert device.command == "device"
    assert device.value == "cuda"

    update = parser().parse_args(["update", "--check"])
    assert update.command == "update"
    assert update.check

    retrieval = parser().parse_args(["retrieval-ignore", "add", "vendor/**"])
    assert retrieval.command == "retrieval-ignore"
    assert retrieval.retrieval_action == "add"
    assert retrieval.pattern == "vendor/**"


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


def test_retrieval_ignore_cli_add_list_remove(monkeypatch, tmp_path: Path) -> None:
    config_path = write_default_config(tmp_path / "devstill.json")
    monkeypatch.chdir(tmp_path)
    assert cli.retrieval_ignore_command("add", "vendor/**") == 0
    assert load_config(config_path).retrieval.exclude == ["vendor/**"]
    assert cli.retrieval_ignore_command("add", "vendor/**") == 0
    assert cli.retrieval_ignore_command(None, None) == 0
    assert cli.retrieval_ignore_command("remove", "vendor/**") == 0
    assert load_config(config_path).retrieval.exclude == []
    with pytest.raises(ValueError, match="not configured"):
        cli.retrieval_ignore_command("remove", "vendor/**")


def test_cli_exposes_agent_and_plan_commands() -> None:
    help_args = parser().parse_args(["help", "agent", "configure"])
    assert help_args.command == "help"
    assert help_args.topic == ["agent", "configure"]

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
            "--planning-max-output-tokens",
            "6144",
            "--planning-reasoning",
            "off",
        ]
    )
    assert configure.agent_action == "configure"
    assert configure.model == "local-model"
    assert configure.timeout == 30
    assert configure.max_output_tokens == 1024
    assert configure.planning_max_output_tokens == 6144
    assert configure.planning_reasoning == "off"

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

    deep = parser().parse_args(
        [
            "deep-plan",
            "add activation",
            "--initial-limit",
            "4",
            "--limit",
            "9",
            "--path",
            "core/",
            "--semantic-only",
        ]
    )
    assert deep.command == "deep-plan"
    assert deep.initial_limit == 4
    assert deep.limit == 9
    assert deep.path == "core/"
    assert deep.semantic_only
    defaults = parser().parse_args(["deep-plan", "query"])
    assert defaults.initial_limit == 5
    assert defaults.limit == 8
    with pytest.raises(SystemExit):
        parser().parse_args(["deep-plan", "query", "--exact", "--lexical-only"])

    execute = parser().parse_args(
        ["execute", "--from", "deep-plan", "--dry-run", "--json"]
    )
    assert execute.command == "execute"
    assert execute.source == "deep-plan"
    assert execute.dry_run
    assert execute.json


def test_help_command_explains_agent_setup(capsys) -> None:
    assert cli.show_help(["agent", "configure"]) == 0
    output = capsys.readouterr().out
    assert "already-running OpenAI-compatible" in output
    assert "http://127.0.0.1:11434/v1" in output
    assert "--model" in output
    assert "DEVSTILL_AGENT_API_KEY" in output


def test_agent_configure_without_options_shows_help_without_loading_repo(
    monkeypatch,
    capsys,
) -> None:
    def fail_if_configured():
        raise AssertionError("configuration should not be loaded")

    monkeypatch.setattr(cli, "_configured", fail_if_configured)
    args = parser().parse_args(["agent", "configure"])
    assert cli.agent_command(args) == 0
    assert "--base-url" in capsys.readouterr().out


def test_agent_configure_rejects_literal_usage_placeholders(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        cli,
        "_configured",
        lambda: (tmp_path / "devstill.json", DevstillConfig()),
    )
    args = parser().parse_args(
        ["agent", "configure", "--base-url", "URL", "--model", "MODEL"]
    )
    with pytest.raises(ValueError, match="placeholder, not an endpoint"):
        cli.agent_command(args)


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
            "--planning-max-output-tokens",
            "5000",
            "--planning-reasoning",
            "auto",
        ]
    )
    assert cli.agent_command(args) == 0
    configured = load_config(config_path)
    assert configured.llm.enabled
    assert configured.llm.base_url == "http://localhost:9000/v1"
    assert configured.llm.model == "local-12b"
    assert configured.llm.max_output_tokens == 2048
    assert configured.llm.planning_max_output_tokens == 5000
    assert configured.llm.planning_reasoning == "auto"
    output = capsys.readouterr().out
    assert "LOCAL_AGENT_KEY (environment)" in output
    assert "General max output tokens: 2048" in output
    assert "Planning max output tokens: 5000" in output
    assert "Pi max output tokens: 49152" in output
    assert "Planning reasoning: auto" in output
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
        def __init__(self, llm, **_kwargs):
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


def test_execute_cli_dry_run_is_machine_readable_and_does_not_apply(
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    config = DevstillConfig(
        llm={"enabled": True, "base_url": "http://localhost/v1", "model": "model"}
    )
    calls: dict[str, object] = {}

    class FakeResult:
        status = "preview"

        @staticmethod
        def to_dict():
            return {"status": "preview", "changed_files": ["app.py"]}

    class FakePrepared:
        result = FakeResult()
        patch = "diff --git a/app.py b/app.py\n"

        def close(self):
            calls["closed"] = True

    class FakeExecutor:
        def __init__(self, repo_root, artifact_root, passed_config):
            calls["repo_root"] = repo_root
            calls["artifact_root"] = artifact_root
            calls["config"] = passed_config

        def prepare(self, *, source, progress):
            calls["source"] = source
            progress("Pi preview")
            return FakePrepared()

        def apply(self, *_args, **_kwargs):
            calls["applied"] = True

    monkeypatch.setattr(
        cli, "_configured", lambda: (tmp_path / "devstill.json", config)
    )
    monkeypatch.setattr(cli, "RepositoryExecutor", FakeExecutor)
    args = parser().parse_args(
        ["execute", "--from", "deep-plan", "--dry-run", "--json"]
    )
    assert cli.execute_command(args) == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out) == {
        "status": "preview",
        "changed_files": ["app.py"],
    }
    assert "Pi preview" in captured.err
    assert calls["source"] == "deep-plan"
    assert calls["closed"]
    assert "applied" not in calls


def test_execute_cli_rejects_conflicting_confirmation_flags() -> None:
    with pytest.raises(ValueError, match="cannot be used together"):
        cli.execute_command(parser().parse_args(["execute", "--dry-run", "--yes"]))


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
        def __init__(self, llm, **_kwargs):
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
                            "label": "New",
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
    assert [item["label"] for item in payload["items"]] == ["Add", "New"]
    assert "Sending 1 source chunks" in captured.err
    assert "http://localhost:8080/v1/chat/completions" in captured.err
    assert calls["limit"] == 12
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

        def __init__(self, llm, **_kwargs):
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


def test_deep_plan_command_refines_context_and_keeps_plan_artifacts(
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    for relative in ("frontend/page.tsx", "frontend/geo.ts"):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"source for {relative}\n", encoding="utf-8")
    artifact_root = tmp_path / ".devstill"
    artifact_root.mkdir()
    (artifact_root / ".plan.md").write_text("normal plan\n", encoding="utf-8")
    (artifact_root / ".plan.json").write_text("normal json\n", encoding="utf-8")
    config = DevstillConfig(
        llm={"enabled": True, "base_url": "http://localhost/v1", "model": "model"}
    )
    queries: list[tuple[str, dict]] = []

    class FakeSearch:
        def __init__(self, *_args):
            pass

        def query(self, query, **kwargs):
            queries.append((query, kwargs))
            if query == "auto location":
                return [make_result(path="frontend/page.tsx", name="Page")]
            return [make_result(path="frontend/geo.ts", name="getLocation")]

        def close(self):
            pass

    class FakeAgentClient:
        endpoint = "http://localhost/v1/chat/completions"

        def __init__(self, llm, **_kwargs):
            self.config = llm

        def chat(self, _messages, **kwargs):
            if kwargs["schema_name"] == "devstill_deep_plan_initial":
                return json.dumps(
                    {
                        "items": [
                            {
                                "label": "Modify",
                                "target": "frontend/page.tsx",
                                "reason": "Load geolocation when the page mounts.",
                            }
                        ]
                    }
                )
            return json.dumps(
                {
                    "items": [
                        {
                            "label": "Modify",
                            "target": "frontend/page.tsx",
                            "reason": "Trigger geolocation during page initialization.",
                        },
                        {
                            "label": "Read",
                            "target": "frontend/geo.ts",
                            "reason": "Reuse the shared browser geolocation helper.",
                        },
                    ]
                }
            )

        def close(self):
            pass

    monkeypatch.setattr(
        cli, "_configured", lambda: (tmp_path / "devstill.json", config)
    )
    monkeypatch.setattr(cli, "HybridSearch", FakeSearch)
    monkeypatch.setattr(cli, "AgentClient", FakeAgentClient)

    args = parser().parse_args(
        [
            "deep-plan",
            "auto location",
            "--initial-limit",
            "1",
            "--limit",
            "2",
            "--json",
        ]
    )
    assert cli.deep_plan_command(args) == 0

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert [item["target"] for item in payload["items"]] == [
        "frontend/page.tsx",
        "frontend/geo.ts",
    ]
    assert [query for query, _kwargs in queries] == [
        "auto location",
        "Load geolocation when the page mounts.",
    ]
    assert (artifact_root / ".plan.md").read_text(encoding="utf-8") == ("normal plan\n")
    assert (artifact_root / ".plan.json").read_text(encoding="utf-8") == (
        "normal json\n"
    )
    assert (artifact_root / ".deep-plan-initial.json").is_file()
    assert (artifact_root / ".deep-plan.json").is_file()
    assert "Initial Deep Plan complete" in captured.err
    assert "2 Deep Plan chunks" in captured.err


def test_deep_plan_rejects_invalid_limits() -> None:
    with pytest.raises(ValueError, match="initial-limit"):
        cli.deep_plan_command(
            parser().parse_args(
                ["deep-plan", "query", "--initial-limit", "0", "--limit", "10"]
            )
        )
    with pytest.raises(ValueError, match="at least --initial-limit"):
        cli.deep_plan_command(
            parser().parse_args(
                ["deep-plan", "query", "--initial-limit", "5", "--limit", "4"]
            )
        )


def test_failed_deep_refinement_updates_initial_but_preserves_previous_final(
    monkeypatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "page.py"
    source.write_text("def page(): pass\n", encoding="utf-8")
    artifact_root = tmp_path / ".devstill"
    artifact_root.mkdir()
    initial_path = artifact_root / ".deep-plan-initial.md"
    final_path = artifact_root / ".deep-plan.md"
    initial_path.write_text("old initial\n", encoding="utf-8")
    final_path.write_text("old final\n", encoding="utf-8")
    config = DevstillConfig(
        llm={"enabled": True, "base_url": "http://localhost/v1", "model": "model"}
    )

    class FakeSearch:
        def __init__(self, *_args):
            pass

        def query(self, *_args, **_kwargs):
            return [make_result(path="page.py", name="page")]

        def close(self):
            pass

    class FakeAgent:
        endpoint = "http://localhost/v1/chat/completions"

        def __init__(self, llm, **_kwargs):
            self.config = llm
            self.calls = 0

        def chat(self, _messages, **_kwargs):
            self.calls += 1
            if self.calls == 1:
                return json.dumps(
                    {
                        "items": [
                            {
                                "label": "Modify",
                                "target": "page.py",
                                "reason": "Load location when the page mounts.",
                            }
                        ]
                    }
                )
            return "not valid json"

        def close(self):
            pass

    monkeypatch.setattr(
        cli, "_configured", lambda: (tmp_path / "devstill.json", config)
    )
    monkeypatch.setattr(cli, "HybridSearch", FakeSearch)
    monkeypatch.setattr(cli, "AgentClient", FakeAgent)

    with pytest.raises(ValueError, match="after one repair"):
        cli.deep_plan_command(
            parser().parse_args(
                [
                    "deep-plan",
                    "auto location",
                    "--initial-limit",
                    "1",
                    "--limit",
                    "1",
                ]
            )
        )

    assert "Initial Pass" in initial_path.read_text(encoding="utf-8")
    assert final_path.read_text(encoding="utf-8") == "old final\n"


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
    reported: list[IndexProgress] = []
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

        def run(self, rebuild=False, verbose=False, progress=None):
            assert progress is not None
            events = [
                IndexProgress("discovery", "Discovering repository files"),
                IndexProgress(
                    "checking",
                    "Checking 10 repository files",
                    5,
                    10,
                    files=IndexFileProgress(
                        total=10,
                        checked=5,
                        changed=2,
                        unchanged=3,
                        parsed=1,
                    ),
                ),
                IndexProgress("complete", "Index update complete", 1, 1),
            ]
            for event in events:
                reported.append(event)
                progress(event)
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
    captured = capsys.readouterr()
    human = captured.out
    assert "Index quality:" in human
    assert "GOOD: 7" in human
    assert "MEH:  2" in human
    assert "BAD:   1" in human
    assert "Binary: 3" in human
    assert [event.stage for event in reported] == [
        "discovery",
        "checking",
        "complete",
    ]

    assert cli.index_command(False, True, True) == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["good_files"] == 7
    assert payload["binary_files_skipped"] == 3
    assert len(reported) == 6


def test_device_command_persists_validated_project_setting(
    monkeypatch,
    tmp_path,
    capsys,
) -> None:
    config_path = write_default_config(tmp_path / "devstill.json")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        cli,
        "inspect_device",
        lambda requested, **_kwargs: SimpleNamespace(
            requested=requested,
            resolved="cuda",
            gpu_name="Test GPU",
        ),
    )

    assert cli.device_command("cuda") == 0
    assert load_config(config_path).embedding.device == "cuda"
    output = capsys.readouterr().out
    assert "Resolved device: cuda" in output
    assert "Test GPU" in output


def test_device_command_rejects_unavailable_cuda_without_writing(
    monkeypatch,
    tmp_path,
) -> None:
    config_path = write_default_config(tmp_path / "devstill.json")
    monkeypatch.chdir(tmp_path)

    def unavailable(*_args, **_kwargs):
        raise RuntimeError("CUDA was requested")

    monkeypatch.setattr(cli, "inspect_device", unavailable)
    with pytest.raises(RuntimeError, match="CUDA was requested"):
        cli.device_command("cuda")
    assert load_config(config_path).embedding.device == "auto"


def test_update_check_and_confirmed_update(monkeypatch, tmp_path, capsys) -> None:
    metadata = InstallMetadata(
        version="0.1.0",
        requested_device="auto",
        resolved_device="cpu",
        torch_index_url="https://download.pytorch.org/whl/cpu",
        venv=str(tmp_path),
    )
    release = Release(
        version="9.0.0",
        tag="v9.0.0",
        tarball_url="https://api.github.com/repos/doeswork/devstill/tarball/v9.0.0",
    )
    monkeypatch.setattr(cli, "__version__", "0.1.0")
    monkeypatch.setattr(cli, "is_editable_install", lambda: False)
    monkeypatch.setattr(cli, "load_install_metadata", lambda: metadata)
    monkeypatch.setattr(cli, "validate_self_contained_install", lambda _value: None)
    monkeypatch.setattr(cli, "fetch_latest_release", lambda: release)
    installed: list[tuple[Release, InstallMetadata]] = []
    monkeypatch.setattr(
        cli,
        "install_release",
        lambda selected, details: installed.append((selected, details)),
    )

    assert cli.update_command(check_only=True, assume_yes=False) == 0
    assert not installed
    assert "An update is available" in capsys.readouterr().out

    assert cli.update_command(check_only=False, assume_yes=True) == 0
    assert installed == [(release, metadata)]
    assert "Updated Devstill to 9.0.0" in capsys.readouterr().out


def test_update_rejects_conflicting_noninteractive_flags() -> None:
    with pytest.raises(ValueError, match="cannot be used together"):
        cli.update_command(check_only=True, assume_yes=True)


def test_update_refuses_editable_checkout_before_loading_metadata(monkeypatch) -> None:
    monkeypatch.setattr(cli, "is_editable_install", lambda: True)
    monkeypatch.setattr(
        cli,
        "load_install_metadata",
        lambda: pytest.fail("editable installs should stop before metadata loading"),
    )

    with pytest.raises(ValueError, match="git pull"):
        cli.update_command(check_only=True, assume_yes=False)
