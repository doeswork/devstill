import json
from pathlib import Path

import pytest

from devstill.runtime import workspace
from devstill.runtime.service import RuntimeSession


def test_review_persists_and_apply_reloads_without_generation(session, monkeypatch):
    proposal = session.prepare("Change x to 2")
    assert proposal["status"] == "ready", proposal
    assert (session.repo / "a.py").read_text() == "x = 1\n"
    monkeypatch.setattr(
        session, "generate", lambda *a: pytest.fail("Apply generated again")
    )
    with RuntimeSession(session.config_path, session.config) as reopened:
        saved = reopened.load(proposal["id"])
        assert saved["digest"] == proposal["digest"]
        assert reopened.apply(saved["id"], saved["digest"])["status"] == "applied"
        assert reopened.apply(saved["id"], saved["digest"])["status"] == "applied"
    assert (session.repo / "a.py").read_text() == "x = 2\n"


def test_stale_review_and_changed_proposal_are_rejected(session):
    proposal = session.prepare("Change x")
    (session.repo / "a.py").write_text("x = 3\n")
    with pytest.raises(ValueError, match="Source changed"):
        session.apply(proposal["id"], proposal["digest"])
    assert (session.repo / "a.py").read_text() == "x = 3\n"
    path = session.root / proposal["id"] / "proposal.json"
    value = json.loads(path.read_text())
    value["query"] = "different request"
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="digest"):
        session.load(proposal["id"])


def test_failed_checks_leave_live_source_untouched(session, monkeypatch):
    calls = []

    def generate(*args):
        calls.append(1)
        return json.dumps(
            {
                "edits": [{"path": "a.py", "old": "x = 1", "new": "x = 2"}],
                "reads": [],
                "checks": [["python", "-c", "assert 1 == 2"]],
            }
        )

    monkeypatch.setattr(session, "generate", generate)
    result = session.prepare("Change x")
    assert result["status"] == "failed" and len(calls) == 2
    assert (session.repo / "a.py").read_text() == "x = 1\n"
    with pytest.raises(ValueError, match="cannot be applied"):
        session.apply(result["id"], result["digest"])


def test_check_cannot_change_proposed_source_and_claim_success(session, monkeypatch):
    monkeypatch.setattr(
        session,
        "generate",
        lambda *a: json.dumps(
            {
                "edits": [{"path": "a.py", "old": "x = 1", "new": "x = 2"}],
                "reads": [],
                "checks": [
                    [
                        "python",
                        "-c",
                        "from pathlib import Path; Path('a.py').write_text('x = 99'); assert 1 == 1",
                    ]
                ],
            }
        ),
    )
    result = session.prepare("Change x")
    assert result["status"] == "failed"
    assert (session.repo / "a.py").read_text() == "x = 1\n"


def test_rollback_preserves_originals_on_second_write_failure(session, monkeypatch):
    (session.repo / "b.py").write_text("y = 1\n")
    monkeypatch.setattr(
        session,
        "generate",
        lambda *a: json.dumps(
            {
                "edits": [
                    {"path": f"{name}.py", "old": f"{var} = 1", "new": f"{var} = 2"}
                    for name, var in [("a", "x"), ("b", "y")]
                ],
                "reads": [],
                "checks": [["python", "-c", "import a,b; assert a.x == b.y == 2"]],
            }
        ),
    )
    proposal = session.prepare("Change both")
    assert proposal["status"] == "ready"
    original = workspace.replace

    def replace(path, raw, mode):
        if path.name == "b.py":
            raise OSError("disk error")
        original(path, raw, mode)

    monkeypatch.setattr(workspace, "replace", replace)
    with pytest.raises(OSError, match="disk error"):
        session.apply(proposal["id"], proposal["digest"])
    assert (session.repo / "a.py").read_text() == "x = 1\n"
    assert (session.repo / "b.py").read_text() == "y = 1\n"
    assert not list(session.root.glob("*/apply-journal.json"))


def test_symlink_and_excluded_targets_rejected(session):
    (session.repo / "link.py").symlink_to(session.repo / "a.py")
    with pytest.raises(ValueError, match="Symlink"):
        workspace.guard(session.repo, session.config, "link.py")
    with pytest.raises(ValueError):
        workspace.guard(session.repo, session.config, ".env")


def test_cancelled_preparation_never_writes_live_tree(session, monkeypatch):
    def generate(*args):
        session.cancelled.set()
        return "{}"

    monkeypatch.setattr(session, "generate", generate)
    proposal = session.prepare("Change x")
    assert proposal["status"] == "cancelled"
    assert (session.repo / "a.py").read_text() == "x = 1\n"


def test_generated_schema_and_prompt_match_hardened_reference():
    from benchmarks.retrieval import minimal_editor as reference
    from devstill.runtime import editor

    assert "python_lines" in editor.SYSTEM
    schema = editor.EditResponse.model_json_schema()
    baseline = reference.EditResponse.model_json_schema()
    for field in ("edits", "reads"):
        assert schema["properties"][field] == baseline["properties"][field]
    # Read-only requests can omit checks; edit/completion still validates 1..3.
    assert (
        schema["properties"]["checks"]["items"]
        == baseline["properties"]["checks"]["items"]
    )
    assert schema["properties"]["checks"]["maxItems"] == 3
    assert "check_repairs" in editor.WireResponse.model_json_schema()["required"]


def test_production_runtime_has_no_legacy_or_benchmark_imports():
    import ast

    root = Path(__file__).parents[1] / "src/devstill/runtime"
    for file in root.glob("*.py"):
        for node in ast.walk(ast.parse(file.read_text())):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert not node.module.startswith(
                    ("benchmarks", "devstill.tui", "devstill.planning", "devstill.pi")
                )


def test_real_evidence_packer_is_independent_of_plan_generation(session, monkeypatch):
    from devstill.runtime.service import RuntimeSession
    from tests.test_context import make_result

    result = make_result(path="a.py", name="x", kind="assignment", code="x = 1")
    result.scores = {"exact": 1.0}
    queries = []

    class Search:
        def __init__(self, *args, **kwargs):
            self.chroma = object()

        def query(self, *a, **kw):
            queries.append(kw)
            return [result]

        def close(self):
            pass

    class Indexer:
        def __init__(self, *args, **kwargs):
            self.chroma = object()

        def run(self, **kwargs):
            pass

        def close(self):
            pass

    monkeypatch.setattr("devstill.runtime.service.HybridSearch", Search)
    monkeypatch.setattr("devstill.indexer.RepositoryIndexer", Indexer)
    session.embedder = object()
    session._owns_embedder = False
    evidence = RuntimeSession.evidence(session, "change x")
    assert evidence[0]["path"] == "a.py"
    assert evidence[0]["complete"] and evidence[0]["code"] == "x = 1\n"
    assert len(queries) == 1 and queries[0]["lexical_only"]


def test_checks_only_repair_in_production_runtime(session, monkeypatch):
    actions = iter(
        [
            {
                "edits": [{"path": "a.py", "old": "x = 1", "new": "x = 2"}],
                "reads": [],
                "checks": [["python", "-c", "import a; assert a.x == 3"]],
            },
            {
                "edits": [],
                "reads": [],
                "checks": [["python", "-c", "import a; assert a.x == 2"]],
                "check_repairs": [
                    {
                        "index": 0,
                        "reason": "Original check expected 3 although the requested value is 2",
                    }
                ],
            },
        ]
    )
    monkeypatch.setattr(session, "generate", lambda *args: json.dumps(next(actions)))
    proposal = session.prepare("Change x")
    assert proposal["status"] == "ready"
    assert proposal["model_calls"] == 2
    assert (session.repo / "a.py").read_text() == "x = 1\n"


def test_headless_entrypoint_does_not_import_textual(monkeypatch):
    import sys

    monkeypatch.setattr(sys, "argv", ["devstill", "edit", "--help"])
    # Check in a fresh interpreter; the test suite itself has UI modules loaded.
    import subprocess

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; sys.argv=['devstill','edit','--help']; from devstill.entrypoint import main\ntry: main()\nexcept SystemExit: pass\nassert 'textual' not in sys.modules",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_tui_review_applies_saved_minimal_digest(tmp_path):
    import asyncio

    from textual.widgets import Button

    from devstill.tui_commands import TuiCommandKind, TuiResult
    from tests.test_tui import FakeWorkflow, build_app

    class Workflow(FakeWorkflow):
        def execute(self, command, progress=None):
            self.calls.append(command)
            if command.kind == TuiCommandKind.EDIT:
                return TuiResult(
                    "Review",
                    "Actual diff",
                    task_review={
                        "id": "a" * 32,
                        "digest": "approved",
                        "query": "change x",
                        "status": "ready",
                    },
                )
            assert command.kind == TuiCommandKind.EDIT_APPLY
            assert command.instruction == "approved"
            return TuiResult(
                "Applied", "Applied saved patch", task_state={"status": "applied"}
            )

    async def exercise():
        workflow = Workflow()
        app = build_app(tmp_path, workflow)
        async with app.run_test(size=(120, 40)) as pilot:
            await app.submit_text("/edit change x")
            await pilot.pause()
            assert str(app.query_one("#run-task", Button).label) == "Apply patch"
            await pilot.click("#run-task")
            await pilot.pause()
            assert len(workflow.calls) == 2

    asyncio.run(exercise())


def test_create_file_and_preserve_crlf(session, monkeypatch):
    (session.repo / "a.py").write_bytes(b"x = 1\r\ny = 3\r\n")
    monkeypatch.setattr(
        session,
        "generate",
        lambda *a: json.dumps(
            {
                "edits": [
                    {"path": "a.py", "old": "x = 1\ny = 3", "new": "x = 2\ny = 3"},
                    {"path": "new.py", "old": "", "new": "z = 4\n"},
                ],
                "reads": [],
                "checks": [
                    ["python", "-c", "import a,new; assert a.x == 2 and new.z == 4"]
                ],
            }
        ),
    )
    proposal = session.prepare("Change x and create module")
    assert proposal["status"] == "ready", proposal
    session.apply(proposal["id"], proposal["digest"])
    assert (session.repo / "a.py").read_bytes() == b"x = 2\r\ny = 3\r\n"
    assert (session.repo / "new.py").read_text() == "z = 4\n"


def test_check_deadline_terminates_process(session):
    import time

    from devstill.runtime.checks import run_checks

    started = time.perf_counter()
    with pytest.raises(TimeoutError):
        run_checks(
            session.repo,
            [["python", "-c", "import time; time.sleep(10); assert False"]],
            started + 0.15,
        )
    assert time.perf_counter() - started < 2


def test_stdio_retains_one_session_and_apply_has_no_model_call(
    session, monkeypatch, capsys
):
    import io
    import sys
    from types import SimpleNamespace

    from devstill.runtime.cli import command

    monkeypatch.setattr("devstill.runtime.cli.RuntimeSession", lambda *a: session)
    request = {"id": "first", "action": "review", "query": "change x"}
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(request) + "\n"))
    assert (
        command(SimpleNamespace(command="session"), session.config_path, session.config)
        == 0
    )
    response = json.loads(capsys.readouterr().out)
    assert response["id"] == "first" and response["result"]["status"] == "ready"
    assert (session.repo / "a.py").read_text() == "x = 1\n"


@pytest.mark.parametrize(
    "code", ["import a\\nassert a.x == 2", "assert (False or True)", "assert True"]
)
def test_invalid_generated_check_never_applies_proposed_edit(
    session, monkeypatch, code
):
    monkeypatch.setattr(
        session,
        "generate",
        lambda *a: json.dumps(
            {
                "edits": [{"path": "a.py", "old": "x = 1", "new": "x = 2"}],
                "reads": [],
                "checks": [["python", "-c", code]],
            }
        ),
    )
    result = session.prepare("Change x")
    assert result["status"] == "failed" and result["model_calls"] == 2
    assert not result["files"]
    trace = json.loads((session.root / result["id"] / "editor-trace.json").read_text())
    assert not json.loads(trace[-1]["content"].split("\nYou must")[0])["edits_applied"]
    assert (session.repo / "a.py").read_text() == "x = 1\n"


def test_line_checks_preserve_python_string_escapes():
    from devstill.runtime.editor import decode_response, validate_checks

    action = decode_response(
        {
            "edits": [],
            "reads": [],
            "checks": [
                {
                    "python_lines": [
                        "value = 'a\\nb'",
                        "assert value.splitlines() == ['a', 'b']",
                    ]
                }
            ],
        }
    )
    validate_checks(action["checks"])
    exec(action["checks"][0][2], {})  # noqa: S102 - fixed test code


def test_quick_defaults_to_own_editor():
    from devstill.config import DevstillConfig

    assert DevstillConfig().editing.enabled


def test_repair_reads_show_post_edit_source(session, monkeypatch):
    calls = []

    def generate(*args):
        calls.append(args)
        if len(calls) == 1:
            return json.dumps(
                {
                    "edits": [{"path": "a.py", "old": "x = 1", "new": "x = 2"}],
                    "reads": [{"path": "a.py", "start": 1, "end": 1}],
                    "checks": [{"python_lines": ["import a", "assert a.x == 3"]}],
                }
            )
        return json.dumps(
            {
                "edits": [{"path": "a.py", "old": "x = 2", "new": "x = 3"}],
                "reads": [],
                "checks": [{"python_lines": ["import a", "assert a.x == 3"]}],
            }
        )

    monkeypatch.setattr(session, "generate", generate)
    proposal = session.prepare("Set x to 3")
    assert proposal["status"] == "ready"
    trace = json.loads(
        (session.root / proposal["id"] / "editor-trace.json").read_text()
    )
    feedback = [m["content"] for m in trace if m.get("role") == "user"][-1]
    assert "x = 2" in feedback
    assert "x = 1" not in feedback
    assert (session.repo / "a.py").read_text() == "x = 1\n"
