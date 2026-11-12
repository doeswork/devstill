import asyncio
import json
import threading

import pytest
from textual.widgets import Select

from devstill.config import DevstillConfig
from devstill.herdr import HerdrReporter
from devstill.tui import ComposerMode, DevstillApp, PromptTextArea
from devstill.ui_session import UiSession
from tests.test_tui import FakeWorkflow


def test_reports_are_ordered_bounded_and_released(monkeypatch):
    for key, value in {
        "HERDR_ENV": "1",
        "HERDR_BIN_PATH": "/fake/herdr",
        "HERDR_PANE_ID": "test:p1",
        "HERDR_SOCKET_PATH": "/fake/socket",
    }.items():
        monkeypatch.setenv(key, value)
    calls = []
    sent = threading.Event()

    def run(argv, **kwargs):
        assert kwargs["timeout"] < 1
        calls.append(argv)
        sent.set()

    monkeypatch.setattr("devstill.herdr.subprocess.run", run)
    reporter = HerdrReporter("a" * 32, ("devstill", "--resume", "a" * 32))
    for state in ("idle", "working", "blocked", "idle"):
        sent.clear()
        reporter.report(state)
        assert sent.wait(2)
    reporter.close()
    assert [c[c.index("--state") + 1] for c in calls[:-1]] == [
        "idle",
        "working",
        "blocked",
        "idle",
    ]
    seqs = [int(c[c.index("--seq") + 1]) for c in calls]
    assert seqs == sorted(set(seqs))
    assert calls[-1][2] == "release-agent"
    assert calls[0][-3:] == ["devstill", "--resume", "a" * 32]


def test_reporting_is_disabled_outside_herdr(monkeypatch):
    monkeypatch.setenv("HERDR_ENV", "0")
    reporter = HerdrReporter()
    reporter.report("working")
    reporter.close()
    assert reporter._thread is None


def test_session_validation_and_private_storage(tmp_path):
    config = DevstillConfig()
    path = tmp_path / "devstill.json"
    session = UiSession(path, config)
    session.save({"history": ["hello"]})
    assert session.path.stat().st_mode & 0o077 == 0
    assert UiSession(path, config, session.id).data["history"] == ["hello"]
    with pytest.raises(ValueError):
        UiSession(path, config, "../../escape")
    with pytest.raises(FileNotFoundError):
        UiSession(path, config, "0" * 32)


def test_tui_restores_transcript_draft_and_settings_without_replay(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("HERDR_ENV", "0")

    async def scenario():
        path = tmp_path / "devstill.json"
        path.write_text("{}")
        config = DevstillConfig()
        app = DevstillApp(path, config, workflow=FakeWorkflow())
        async with app.run_test() as pilot:
            await app.submit_text("/help")
            app.query_one(
                "#command-input", PromptTextArea
            ).text = "my unfinished request"
            app.query_one("#task-mode", Select).value = "search"
            await pilot.pause()
            app._sync_session()
            saved = json.loads(app._session.path.read_text())
            assert saved["entries"]
            session_id = app._session.id
        workflow = FakeWorkflow()
        restored = DevstillApp(path, config, workflow=workflow, resume=session_id)
        async with restored.run_test() as pilot:
            await pilot.pause()
            assert restored._session.id == session_id
            assert restored._history == ["/help"]
            assert (
                restored.query_one("#command-input", PromptTextArea).text
                == "my unfinished request"
            )
            assert restored.query_one("#task-mode", Select).value == "search"
            assert len(restored.query(".session-entry")) == len(saved["entries"])
            assert workflow.calls == []
            restored._busy = True
            restored._sync_session()
            assert json.loads(restored._session.path.read_text())["interrupted"]
            restored._busy = False
            restored._set_composer_mode(ComposerMode.SEARCH_PATH)
            restored._sync_session()
            assert json.loads(restored._session.path.read_text())["review_interrupted"]

    asyncio.run(scenario())


@pytest.mark.parametrize("status", ["ready", "applied"])
def test_restore_pending_patch_requires_original_digest_and_ready_state(
    tmp_path, monkeypatch, status
):
    from devstill.runtime.artifacts import digest, write

    monkeypatch.setenv("HERDR_ENV", "0")

    async def scenario():
        path = tmp_path / "devstill.json"
        path.write_text("{}")
        config = DevstillConfig()
        saved = UiSession(path, config)
        proposal = {"id": "b" * 32, "query": "fix addition", "status": "ready"}
        proposal["digest"] = digest(proposal)
        root = saved.path.parent.parent / "runs" / proposal["id"]
        write(root / "proposal.json", proposal)
        write(root / "result.json", {"status": status})
        saved.save({"pending_review": proposal})
        workflow = FakeWorkflow()
        app = DevstillApp(path, config, workflow=workflow, resume=saved.id)
        async with app.run_test() as pilot:
            await pilot.pause()
            if status == "ready":
                assert app._composer_mode is ComposerMode.TASK_REVIEW
                assert app._task_review["digest"] == proposal["digest"]
            else:
                assert app._composer_mode is ComposerMode.NORMAL
                assert app._task_review is None
            assert workflow.calls == []

    asyncio.run(scenario())


def test_reporting_failures_do_not_escape(monkeypatch):
    import subprocess

    for key, value in {
        "HERDR_ENV": "1",
        "HERDR_BIN_PATH": "/fake/herdr",
        "HERDR_PANE_ID": "test:p1",
        "HERDR_SOCKET_PATH": "/fake/socket",
    }.items():
        monkeypatch.setenv(key, value)
    attempted = threading.Event()

    def run(*args, **kwargs):
        attempted.set()
        raise subprocess.TimeoutExpired("herdr", 0.75)

    monkeypatch.setattr("devstill.herdr.subprocess.run", run)
    reporter = HerdrReporter()
    reporter.report("working")
    assert attempted.wait(2)
    reporter.close()
    assert not reporter._thread.is_alive()
