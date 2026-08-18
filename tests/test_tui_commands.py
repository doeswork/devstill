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
    TuiCommand,
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
    deep = parse_tui_command("/deep-plan improve login")
    assert deep.kind is TuiCommandKind.DEEP_PLAN
    assert deep.query == "improve login"
    assert parse_tui_command("/?").kind is TuiCommandKind.HELP
    assert parse_tui_command("/index").kind is TuiCommandKind.INDEX
    assert parse_tui_command("/files").kind is TuiCommandKind.FILES
    assert parse_tui_command("/agent").kind is TuiCommandKind.AGENT
    assert parse_tui_command("/agent test").kind is TuiCommandKind.AGENT_TEST
    execute = parse_tui_command("/execute deep-plan")
    assert execute.kind is TuiCommandKind.EXECUTE
    assert execute.query == "deep-plan"
    assert parse_tui_command("/execute").kind is TuiCommandKind.EXECUTE
    assert parse_tui_command("/clear").kind is TuiCommandKind.CLEAR
    assert parse_tui_command("/quit").kind is TuiCommandKind.QUIT


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ("", "Enter a search query"),
        ("/search", "requires a query"),
        ("/context", "requires a query"),
        ("/plan", "requires a query"),
        ("/deep-plan", "requires a query"),
        ("/agent enable", "accepts `configure` or `test`"),
        ("/execute something", "accepts `plan` or `deep-plan`"),
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
    assert "`/execute`" in help_text
    assert "use the configured model" in help_text
    assert "`/index`" in help_text


def test_search_markdown_preserves_source_and_uses_safe_fence() -> None:
    code = "def login():\n    marker = ```\n    return marker"
    rendered = render_search_markdown([make_result(code=code)], "login")
    assert "core/views.py:10-11 — login_api (function)" in rendered
    assert code in rendered
    assert "````python" in rendered
    assert "(function) — score 0.5000\n````python" in rendered


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
    assert result.execution_offer is not None
    assert result.execution_offer.source == "plan"
    assert result.execution_offer.query == "add activation"
    assert result.execution_offer.unresolved_searches == 0
    assert "🟢 Add — core/views.py" in result.markdown
    assert any(
        isinstance(message, str) and "Sending 1 source chunks" in message
        for message in progress
    )
    previews = [message for message in progress if isinstance(message, TuiPreview)]
    assert len(previews) == 1
    assert (
        "- ⚪ Pending — core/views.py — Retrieved chunks: `register`"
        in previews[0].markdown
    )
    assert "Waiting for the agent" in previews[0].markdown
    assert closed == {"search": True, "agent": True}
    assert context_path.read_text(encoding="utf-8") == "keep me\n"
    assert (tmp_path / ".devstill/.plan.json").is_file()


def test_deep_plan_workflow_stages_new_files_and_keeps_normal_plan(
    monkeypatch,
    tmp_path: Path,
) -> None:
    for relative in ("frontend/page.tsx", "frontend/geo.ts"):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"source for {relative}\n", encoding="utf-8")
    config_path = tmp_path / "devstill.json"
    config_path.write_text("{}", encoding="utf-8")
    mark_index_ready(tmp_path)
    normal_plan = tmp_path / ".devstill/.plan.md"
    normal_plan.write_text("keep normal plan\n", encoding="utf-8")
    config = DevstillConfig(
        llm={"enabled": True, "base_url": "http://localhost/v1", "model": "model"}
    )

    class FakeSearch:
        def __init__(self, *_args, **_kwargs):
            pass

        def query(self, query, **_kwargs):
            if query == "auto location":
                return [make_result(path="frontend/page.tsx", name="Page")]
            return [make_result(path="frontend/geo.ts", name="getLocation")]

        def close(self):
            pass

    class FakeAgent:
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
                                "reason": "Load location when the page mounts.",
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
                            "reason": "Trigger location during page initialization.",
                        },
                        {
                            "label": "Read",
                            "target": "frontend/geo.ts",
                            "reason": "Reuse the shared location helper.",
                        },
                    ]
                }
            )

        def close(self):
            pass

    monkeypatch.setattr("devstill.tui_commands.HybridSearch", FakeSearch)
    monkeypatch.setattr("devstill.tui_commands.AgentClient", FakeAgent)
    progress: list[str | TuiPreview] = []
    result = TuiWorkflow(
        config_path,
        config,
        embedder=FakeWarmEmbedder(),
        chroma_store=object(),
    ).execute(parse_tui_command("/deep-plan auto location"), progress.append)

    previews = [update for update in progress if isinstance(update, TuiPreview)]
    assert len(previews) == 3
    assert "⚪ Pending — frontend/page.tsx" in previews[0].markdown
    assert "🟣 Modify — frontend/page.tsx" in previews[1].markdown
    assert (
        "- ⚪ Pending — frontend/geo.ts — Retrieved chunks: `getLocation`"
        in previews[2].markdown
    )
    assert "🟣 Modify — frontend/page.tsx" in result.markdown
    assert "🔵 Read — frontend/geo.ts" in result.markdown
    assert result.execution_offer is not None
    assert result.execution_offer.source == "deep-plan"
    assert result.execution_offer.query == "auto location"
    assert result.execution_offer.unresolved_searches == 0
    assert normal_plan.read_text(encoding="utf-8") == "keep normal plan\n"
    assert (tmp_path / ".devstill/.deep-plan-initial.json").is_file()
    assert (tmp_path / ".devstill/.deep-plan.json").is_file()


def test_execute_workflow_forwards_handoff_instruction(
    monkeypatch,
    tmp_path: Path,
) -> None:
    calls: dict[str, object] = {}
    prepared = SimpleNamespace(result=object(), patch="diff")

    class FakeExecutor:
        def __init__(self, repo_root, artifact_root, config):
            calls["paths"] = (repo_root, artifact_root)

        def prepare(self, *, source, instruction, progress):
            calls["source"] = source
            calls["instruction"] = instruction
            progress("Pi received the handoff")
            return prepared

    monkeypatch.setattr("devstill.tui_commands.RepositoryExecutor", FakeExecutor)
    monkeypatch.setattr(
        "devstill.tui_commands.render_execution_markdown",
        lambda _result, _patch: "# Execution preview",
    )
    config_path = tmp_path / "devstill.json"
    config_path.write_text("{}", encoding="utf-8")
    progress: list[object] = []

    result = TuiWorkflow(config_path, DevstillConfig()).execute(
        TuiCommand(
            TuiCommandKind.EXECUTE,
            "/execute deep-plan",
            "deep-plan",
            "Reuse the existing geolocation helper.",
        ),
        progress.append,
    )

    assert result.prepared_execution is prepared
    assert calls["source"] == "deep-plan"
    assert calls["instruction"] == "Reuse the existing geolocation helper."
    assert progress == [
        "Preparing an isolated Pi execution workspace",
        "Pi received the handoff",
    ]


def test_manual_plan_labels_are_validated_and_persisted(
    tmp_path: Path,
) -> None:
    target = tmp_path / "frontend/page.tsx"
    target.parent.mkdir(parents=True)
    target.write_text("export function Page() {}\n", encoding="utf-8")
    config_path = tmp_path / "devstill.json"
    config_path.write_text("{}", encoding="utf-8")
    workflow = TuiWorkflow(
        config_path,
        DevstillConfig(),
        embedder=FakeWarmEmbedder(),
        chroma_store=object(),
    )
    reviewed = PlanOutput(
        query="auto load location",
        repository=str(tmp_path),
        model="local-model",
        items=[
            PlanItem(
                label=PlanLabel.MODIFY,
                target="frontend/page.tsx",
                reason="Trigger geolocation when the page mounts.",
            )
        ],
    )

    offer, markdown = workflow.save_manual_plan("deep-plan", reviewed)

    assert offer.source == "deep-plan"
    assert offer.unresolved_searches == 0
    assert offer.plan == reviewed
    assert "🟣 Modify — frontend/page.tsx" in markdown
    saved = PlanOutput.model_validate_json(
        (tmp_path / ".devstill/.deep-plan.json").read_text(encoding="utf-8")
    )
    assert saved == reviewed
    assert not (tmp_path / ".devstill/.plan.json").exists()

    unresolved = reviewed.model_copy(
        update={
            "items": [
                PlanItem(
                    label=PlanLabel.SEARCH,
                    target="frontend/geo",
                    reason="Locate the geolocation helper.",
                )
            ]
        }
    )
    with pytest.raises(RuntimeError, match="Search entries"):
        workflow.save_manual_plan("deep-plan", unresolved)
    assert (
        PlanOutput.model_validate_json(
            (tmp_path / ".devstill/.deep-plan.json").read_text(encoding="utf-8")
        )
        == reviewed
    )

    partial_offer, partial_markdown = workflow.save_manual_plan(
        "deep-plan",
        unresolved,
        allow_unresolved=True,
    )
    assert partial_offer.unresolved_searches == 1
    assert partial_offer.plan == unresolved
    assert "⚪ Search — frontend/geo" in partial_markdown
    assert (
        PlanOutput.model_validate_json(
            (tmp_path / ".devstill/.deep-plan.json").read_text(encoding="utf-8")
        )
        == unresolved
    )
    workflow.close()


def test_review_removal_persists_empty_plan_and_temporary_suppression(
    tmp_path: Path,
) -> None:
    target = tmp_path / "app.py"
    target.write_text("value = 1\n", encoding="utf-8")
    config_path = tmp_path / "devstill.json"
    config_path.write_text("{}", encoding="utf-8")
    workflow = TuiWorkflow(
        config_path,
        DevstillConfig(),
        embedder=FakeWarmEmbedder(),
        chroma_store=object(),
    )
    plan = PlanOutput(
        query="change app",
        repository=str(tmp_path),
        model="local-model",
        items=[
            PlanItem(label=PlanLabel.MODIFY, target="app.py", reason="Change it.")
        ],
    )
    offer, _ = workflow.save_manual_plan("plan", plan)
    removed = workflow.remove_review_item(offer, 0)
    assert removed.offer.plan is not None
    assert removed.offer.plan.items == []
    assert removed.offer.suppressed_paths == frozenset({"app.py"})
    saved = PlanOutput.model_validate_json(
        (tmp_path / ".devstill/.plan.json").read_text(encoding="utf-8")
    )
    assert saved.items == []
    workflow.close()


def test_review_permanent_exclusion_updates_config_and_keeps_removal(
    tmp_path: Path,
) -> None:
    (tmp_path / "app.py").write_text("value = 1\n", encoding="utf-8")
    config_path = tmp_path / "devstill.json"
    config_path.write_text("{}", encoding="utf-8")
    workflow = TuiWorkflow(
        config_path,
        DevstillConfig(),
        embedder=FakeWarmEmbedder(),
        chroma_store=object(),
    )
    plan = PlanOutput(
        query="change app",
        repository=str(tmp_path),
        model="local-model",
        items=[
            PlanItem(label=PlanLabel.MODIFY, target="app.py", reason="Change it.")
        ],
    )
    offer, _ = workflow.save_manual_plan("plan", plan)
    result = workflow.permanently_exclude_review_item(offer, 0)
    assert result.offer.plan is not None and not result.offer.plan.items
    assert json.loads(config_path.read_text())["retrieval"]["exclude"] == ["app.py"]
    workflow.close()


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
    assert "General max output tokens: **2048**" in agent.markdown
    assert "Planning max output tokens: **6144**" in agent.markdown
    assert "Pi max output tokens: **49152**" in agent.markdown
    assert "Planning reasoning: **off**" in agent.markdown
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
