from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from devstill.config import DevstillConfig
from devstill.models import FileQuality, IndexProgress, IndexSummary
from devstill.planning import PlanItem, PlanLabel, PlanOutput
from devstill.tui_commands import (
    TuiCommandKind,
    TuiPreview,
    TuiWorkflow,
    help_markdown,
    matching_commands,
    parse_tui_command,
    render_search_markdown,
    safe_status,
)
from tests.test_context import make_result


class FakeWarmEmbedder:
    def __init__(self) -> None:
        self.state = "cold"
        self.warm_calls = 0
        self.closed = False

    def warm(self, on_loading=None):
        self.warm_calls += 1
        self.state = "loading"
        if on_loading is not None:
            on_loading()
        self.state = "warm"
        return self

    def close(self):
        self.closed = True
        self.state = "cold"


def mark_index_ready(root: Path) -> None:
    path = root / ".devstill/metadata.sqlite"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()


def test_tui_command_parser_and_aliases() -> None:
    plain = parse_tui_command("where is authentication checked?")
    assert plain.kind is TuiCommandKind.SEARCH
    assert plain.query == "where is authentication checked?"
    assert parse_tui_command("/s login").kind is TuiCommandKind.SEARCH
    assert parse_tui_command("/c login").kind is TuiCommandKind.CONTEXT
    assert parse_tui_command("/p login").kind is TuiCommandKind.PLAN
    assert parse_tui_command("/?").kind is TuiCommandKind.HELP
    assert parse_tui_command("/index").kind is TuiCommandKind.INDEX
    assert parse_tui_command("/files").kind is TuiCommandKind.FILES
    assert parse_tui_command("/agent").kind is TuiCommandKind.AGENT
    assert parse_tui_command("/agent test").kind is TuiCommandKind.AGENT_TEST
    assert parse_tui_command("/clear").kind is TuiCommandKind.CLEAR
    assert parse_tui_command("/quit").kind is TuiCommandKind.QUIT


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ("", "Enter a search query"),
        ("/search", "requires a query"),
        ("/context", "requires a query"),
        ("/plan", "requires a query"),
        ("/agent enable", "accepts `configure` or `test`"),
        ("/index --rebuild", "does not accept arguments"),
        ("/remove", "Unknown command"),
    ],
)
def test_tui_command_parser_rejects_unsupported_input(value: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        parse_tui_command(value)


def test_command_matching_and_help_describe_network_boundary() -> None:
    assert [spec.template for spec in matching_commands("/co")] == ["/context "]
    assert {spec.template for spec in matching_commands("/agent")} == {
        "/agent",
        "/agent configure",
        "/agent test",
    }
    assert matching_commands("ordinary text") == []
    help_text = help_markdown()
    assert "Only `/plan` and `/agent test` use the network" in help_text
    assert "`/index`" in help_text


def test_search_markdown_preserves_source_and_uses_safe_fence() -> None:
    code = "def login():\n    marker = ```\n    return marker"
    rendered = render_search_markdown([make_result(code=code)], "login")
    assert "core/views.py:10-11 — login_api (function)" in rendered
    assert code in rendered
    assert "````python" in rendered


def test_search_and_context_workflows_close_resources_and_write_artifacts(
    monkeypatch,
    tmp_path: Path,
) -> None:
    calls: dict[str, object] = {}
    embedder = FakeWarmEmbedder()
    chroma = object()

    class FakeSearch:
        def __init__(self, config_path, config, *, embedder, chroma_store):
            calls["opened"] = True
            calls.setdefault("dependencies", []).append((embedder, chroma_store))

        def query(self, query, **kwargs):
            calls.setdefault("queries", []).append((query, kwargs))
            return [
                make_result(path="core/tests.py", name="test_login"),
                make_result(path="core/views.py", name="login_api"),
            ]

        def close(self):
            calls["closed"] = int(calls.get("closed", 0)) + 1

    config_path = tmp_path / "devstill.json"
    config_path.write_text("{}", encoding="utf-8")
    mark_index_ready(tmp_path)
    monkeypatch.setattr("devstill.tui_commands.HybridSearch", FakeSearch)
    workflow = TuiWorkflow(
        config_path,
        DevstillConfig(),
        embedder=embedder,
        chroma_store=chroma,
    )

    warm_progress: list[str] = []
    workflow.warm(warm_progress.append)
    assert embedder.warm_calls == 1
    assert "Vector index open and query encoder warm" in warm_progress

    search = workflow.execute(parse_tui_command("login"))
    assert search.title == "Search"
    assert "test_login" in search.markdown
    context = workflow.execute(parse_tui_command("/context login"))
    assert context.title == "Context"
    assert "login_api" in context.markdown
    assert "test_login" not in context.markdown
    assert (
        json.loads((tmp_path / ".devstill/.context.json").read_text(encoding="utf-8"))[
            0
        ]["path"]
        == "core/views.py"
    )
    assert calls["closed"] == 2
    queries = calls["queries"]
    assert queries[0][1]["limit"] == 10
    assert queries[1][1]["limit"] == 32
    assert calls["dependencies"] == [(embedder, chroma), (embedder, chroma)]
    assert embedder.warm_calls == 1
    workflow.close()
    assert embedder.closed


def test_plan_workflow_reports_destination_and_preserves_context(
    monkeypatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "core/views.py"
    source.parent.mkdir()
    source.write_text("def register():\n    pass\n", encoding="utf-8")
    config_path = tmp_path / "devstill.json"
    config_path.write_text("{}", encoding="utf-8")
    mark_index_ready(tmp_path)
    context_path = tmp_path / ".devstill/.context.md"
    context_path.parent.mkdir(exist_ok=True)
    context_path.write_text("keep me\n", encoding="utf-8")
    config = DevstillConfig(
        llm={
            "enabled": True,
            "base_url": "http://localhost:8080/v1",
            "model": "local-model",
        }
    )
    closed: dict[str, bool] = {}

    class FakeSearch:
        def __init__(self, *_args, **_kwargs):
            pass

        def query(self, *_args, **_kwargs):
            return [make_result(path="core/views.py", name="register")]

        def close(self):
            closed["search"] = True

    class FakeAgent:
        endpoint = "http://localhost:8080/v1/chat/completions"

        def __init__(self, llm, **_kwargs):
            self.config = llm

        def close(self):
            closed["agent"] = True

    plan = PlanOutput(
        query="add activation",
        repository=str(tmp_path),
        model="local-model",
        items=[
            PlanItem(
                label=PlanLabel.ADD,
                target="core/views.py",
                reason="Add activation handling to registration.",
            )
        ],
    )
    monkeypatch.setattr("devstill.tui_commands.HybridSearch", FakeSearch)
    monkeypatch.setattr("devstill.tui_commands.AgentClient", FakeAgent)
    monkeypatch.setattr("devstill.tui_commands.generate_plan", lambda *_a, **_k: plan)
    progress: list[str | TuiPreview] = []

    result = TuiWorkflow(
        config_path,
        config,
        embedder=FakeWarmEmbedder(),
        chroma_store=object(),
    ).execute(
        parse_tui_command("/plan add activation"),
        progress.append,
    )

    assert result.title == "Plan"
    assert "🟢 Add — core/views.py" in result.markdown
    assert any(
        isinstance(message, str) and "Sending 1 source chunks" in message
        for message in progress
    )
    previews = [message for message in progress if isinstance(message, TuiPreview)]
    assert len(previews) == 1
    assert "⚪ Pending — core/views.py" in previews[0].markdown
    assert "Waiting for the agent" in previews[0].markdown
    assert closed == {"search": True, "agent": True}
    assert context_path.read_text(encoding="utf-8") == "keep me\n"
    assert (tmp_path / ".devstill/.plan.json").is_file()


def test_index_workflow_forwards_progress_and_closes_indexer(
    monkeypatch,
    tmp_path: Path,
) -> None:
    calls: dict[str, object] = {}
    embedder = FakeWarmEmbedder()
    chroma = object()

    class FakeIndexer:
        def __init__(self, *_args, embedder, chroma_store):
            calls["dependencies"] = (embedder, chroma_store)

        def run(self, progress=None):
            assert progress is not None
            progress(IndexProgress("parsing", "Assessing core/views.py", 1, 2))
            return IndexSummary(
                repository=str(tmp_path),
                discovered_files=2,
                parsed_files=1,
                search_units_embedded=3,
                good_files=2,
                index_path=str(tmp_path / ".devstill"),
            )

        def close(self):
            calls["closed"] = True

        def list_quality(self):
            return [
                FileQuality(
                    path="notes.custom",
                    status="MEH",
                    parser="generic_text",
                    reasons=["generic text fallback"],
                ),
                FileQuality(
                    path="broken.py",
                    status="BAD",
                    parser="python_ast",
                    reasons=["syntax error"],
                ),
            ]

    config_path = tmp_path / "devstill.json"
    config_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr("devstill.tui_commands.RepositoryIndexer", FakeIndexer)
    progress: list[str | IndexProgress] = []

    result = TuiWorkflow(
        config_path,
        DevstillConfig(),
        embedder=embedder,
        chroma_store=chroma,
    ).execute(
        parse_tui_command("/index"),
        progress.append,
    )

    assert "Discovered files: **2**" in result.markdown
    assert "## 🟡 MEH files (1)" in result.markdown
    assert "`notes.custom` — generic text fallback" in result.markdown
    assert "## 🔴 BAD files (1)" in result.markdown
    assert "`broken.py` — syntax error" in result.markdown
    assert IndexProgress("parsing", "Assessing core/views.py", 1, 2) in progress
    assert calls["dependencies"] == (embedder, chroma)
    assert embedder.warm_calls == 0
    assert calls["closed"]


def test_agent_config_update_preserves_warm_retrieval_runtime(tmp_path: Path) -> None:
    config_path = tmp_path / "devstill.json"
    config_path.write_text("{}", encoding="utf-8")
    embedder = FakeWarmEmbedder()
    chroma = object()
    config = DevstillConfig()
    workflow = TuiWorkflow(
        config_path,
        config,
        embedder=embedder,
        chroma_store=chroma,
    )
    workflow._runtime.dependencies(lambda _update: None, warm_encoder=True)
    updated = config.model_copy(
        update={
            "llm": config.llm.model_copy(
                update={"enabled": True, "model": "local-agent"}
            )
        }
    )

    workflow.update_config(updated)

    assert workflow.runtime_state == "warm"
    assert embedder.warm_calls == 1
    assert not embedder.closed


def test_failed_runtime_warm_can_retry(monkeypatch, tmp_path: Path) -> None:
    class FlakyEmbedder(FakeWarmEmbedder):
        def warm(self, on_loading=None):
            self.warm_calls += 1
            self.state = "loading"
            if on_loading is not None:
                on_loading()
            if self.warm_calls == 1:
                self.state = "cold"
                raise RuntimeError("model load failed")
            self.state = "warm"
            return self

    class FakeSearch:
        def __init__(self, *_args, **_kwargs):
            pass

        def query(self, *_args, **_kwargs):
            return []

        def close(self):
            pass

    config_path = tmp_path / "devstill.json"
    config_path.write_text("{}", encoding="utf-8")
    mark_index_ready(tmp_path)
    embedder = FlakyEmbedder()
    workflow = TuiWorkflow(
        config_path,
        DevstillConfig(),
        embedder=embedder,
        chroma_store=object(),
    )
    monkeypatch.setattr("devstill.tui_commands.HybridSearch", FakeSearch)

    with pytest.raises(RuntimeError, match="model load failed"):
        workflow.execute(parse_tui_command("first attempt"))

    result = workflow.execute(parse_tui_command("second attempt"))
    assert result.title == "Search"
    assert workflow.runtime_state == "warm"
    assert embedder.warm_calls == 2


def test_dirty_tui_index_rejects_retrieval_before_warming(tmp_path: Path) -> None:
    config_path = tmp_path / "devstill.json"
    config_path.write_text("{}", encoding="utf-8")
    mark_index_ready(tmp_path)
    (tmp_path / ".devstill/.indexing-dirty").write_text(
        "interrupted\n", encoding="utf-8"
    )
    embedder = FakeWarmEmbedder()
    workflow = TuiWorkflow(
        config_path,
        DevstillConfig(),
        embedder=embedder,
        chroma_store=object(),
    )

    with pytest.raises(RuntimeError, match="index recovery is required"):
        workflow.execute(parse_tui_command("authentication"))

    assert embedder.warm_calls == 0


def test_files_and_agent_status_workflows_do_not_expose_secret(
    monkeypatch,
    tmp_path: Path,
) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "main.py").write_text("print('ok')\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "main.py"], check=True)
    config_path = tmp_path / "devstill.json"
    config_path.write_text("{}", encoding="utf-8")
    config = DevstillConfig(
        llm={
            "enabled": True,
            "base_url": "http://localhost/v1",
            "model": "local-model",
        }
    )
    monkeypatch.setenv("DEVSTILL_AGENT_API_KEY", "top-secret-value")
    workflow = TuiWorkflow(config_path, config)

    files = workflow.execute(parse_tui_command("/files"))
    assert "`main.py`" in files.markdown
    agent = workflow.execute(parse_tui_command("/agent"))
    assert "DEVSTILL_AGENT_API_KEY" in agent.markdown
    assert "(**environment**)" in agent.markdown
    assert "top-secret-value" not in agent.markdown


def test_agent_test_workflow_closes_client(monkeypatch, tmp_path: Path) -> None:
    config_path = tmp_path / "devstill.json"
    config_path.write_text("{}", encoding="utf-8")
    config = DevstillConfig(
        llm={"enabled": True, "base_url": "http://localhost/v1", "model": "model"}
    )
    calls: dict[str, bool] = {}

    class FakeAgent:
        endpoint = "http://localhost/v1/chat/completions"

        def __init__(self, _llm, **_kwargs):
            pass

        def test(self):
            return SimpleNamespace(
                model="model",
                endpoint=self.endpoint,
                elapsed_seconds=0.125,
            )

        def close(self):
            calls["closed"] = True

    monkeypatch.setattr("devstill.tui_commands.AgentClient", FakeAgent)
    result = TuiWorkflow(config_path, config).execute(parse_tui_command("/agent test"))
    assert "Agent test passed" in result.markdown
    assert calls["closed"]


def test_status_tolerates_unreadable_manifest(tmp_path: Path) -> None:
    config_path = tmp_path / "devstill.json"
    config_path.write_text("{}", encoding="utf-8")
    manifest = tmp_path / ".devstill/manifests/index.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text("[]", encoding="utf-8")

    status = safe_status(config_path, DevstillConfig())

    assert status["indexed_at"] == "manifest unreadable"


def test_status_marks_dirty_index_as_recovery_required(tmp_path: Path) -> None:
    config_path = tmp_path / "devstill.json"
    config_path.write_text("{}", encoding="utf-8")
    index_root = tmp_path / ".devstill"
    index_root.mkdir()
    (index_root / "metadata.sqlite").touch()
    (index_root / ".indexing-dirty").write_text("interrupted\n", encoding="utf-8")

    status = safe_status(config_path, DevstillConfig())

    assert status["index_dirty"] is True
    assert status["index_ready"] is False
