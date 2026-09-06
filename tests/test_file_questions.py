from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import ClassVar

import pytest

from devstill import file_questions
from devstill.agent import AgentError
from devstill.config import DevstillConfig, LlmConfig
from devstill.file_questions import prepare_file_questions
from devstill.planning import PlanItem, PlanLabel, PlanOutput


@pytest.fixture
def question_context(tmp_path: Path):
    config = DevstillConfig(llm=LlmConfig(enabled=True, model="local-model"))
    path = tmp_path / "player.js"
    content = (
        "// start of the complete file\r\n"
        + "const camera = '視点';\r\n" * 600
        + "// final line\r\n"
    )
    path.write_bytes(content.encode("utf-8"))
    plan = PlanOutput(
        query="Press c to switch first- and third-person cameras",
        repository=str(tmp_path),
        model="local-model",
        refinements=["Keep the existing movement controls."],
        items=[
            PlanItem(
                label=PlanLabel.MODIFY,
                target="player.js",
                reason="Expose camera state.",
            ),
            PlanItem(
                label=PlanLabel.MODIFY,
                target="game.js",
                reason="Add the keyboard toggle.",
            ),
            PlanItem(
                label=PlanLabel.SEARCH,
                target="Camera tests",
                reason="Find existing coverage.",
            ),
        ],
    )
    return config, plan, path, content


@pytest.fixture
def agent(monkeypatch):
    class FakeAgent:
        requests: ClassVar[list] = []
        fail = False

        def __init__(self, config, *, log_path):
            self.config = config
            self.log_path = log_path

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def chat(self, messages, **options):
            self.requests.append((deepcopy(messages), options))
            if self.fail:
                raise AgentError("context window exceeded")
            return "The file owns camera state; its explanation matches the requested toggle."

    monkeypatch.setattr(file_questions, "AgentClient", FakeAgent)
    return FakeAgent


def test_full_file_complete_plan_and_conversation_are_sent(
    question_context, agent, tmp_path
):
    config, plan, path, content = question_context
    session = prepare_file_questions(tmp_path, config, tmp_path / ".devstill", plan, 0)
    before = plan.model_dump()
    assert not agent.requests
    review = session.respond()
    messages, options = agent.requests[0]
    assert [message["role"] for message in messages] == ["system", "user"]
    assert "has not asked that question yet" in messages[0]["content"]
    context = json.loads(messages[1]["content"].split("CONTEXT_JSON:\n", 1)[1])
    assert context["selected_file"]["content"] == content
    assert len(content) > config.index.max_chunk_chars
    assert context["current_plan"] == plan.model_dump(mode="json")
    assert context["selected_file"]["complete"] is True
    assert options == {
        "max_output_tokens": config.llm.max_output_tokens,
        "reasoning": config.llm.planning_reasoning,
    }

    path.write_text("// changed externally", encoding="utf-8")
    session.respond("Does this change collision detection?")
    followup = agent.requests[1][0]
    assert followup[:2] == messages
    assert followup[-2] == {"role": "assistant", "content": review}
    assert followup[-1] == {
        "role": "user",
        "content": "Does this change collision detection?",
    }
    session.respond("Which symbol should I inspect?")
    assert len(agent.requests[2][0]) == 6
    assert plan.model_dump() == before
    assert path.read_text() == "// changed externally"
    assert not (tmp_path / ".devstill/.plan.json").exists()
    fresh = prepare_file_questions(tmp_path, config, tmp_path / ".devstill", plan, 0)
    assert "// changed externally" in fresh.messages[1]["content"]


def test_request_failure_keeps_history_retryable(question_context, agent, tmp_path):
    config, plan, _, _ = question_context
    session = prepare_file_questions(tmp_path, config, tmp_path / ".devstill", plan, 0)
    session.respond()
    before = deepcopy(session.messages)
    agent.fail = True
    with pytest.raises(AgentError, match="context window exceeded"):
        session.respond("Why this file?")
    assert session.messages == before
    agent.fail = False
    session.respond("Why this file?")
    assert session.messages[-2] == {"role": "user", "content": "Why this file?"}
    assert len(session.messages) == len(before) + 2


@pytest.mark.parametrize(
    "kind",
    [
        "missing",
        "new",
        "search",
        "outside",
        "symlink",
        "binary",
        "sensitive",
        "excluded",
        "disabled",
    ],
)
def test_unreadable_or_ineligible_files_do_not_contact_agent(
    question_context, agent, tmp_path, kind
):
    config, plan, path, _ = question_context
    item = plan.items[0]
    if kind == "missing":
        path.unlink()
    elif kind == "new":
        item.label = PlanLabel.NEW
    elif kind == "search":
        item.label = PlanLabel.SEARCH
    elif kind == "outside":
        item.target = "../outside.js"
    elif kind == "symlink":
        other = tmp_path / "other.js"
        other.write_text("secret reference")
        path.unlink()
        path.symlink_to(other)
    elif kind == "binary":
        path.write_bytes(b"\0\1\2")
    elif kind == "sensitive":
        path.rename(tmp_path / ".env")
        item.target = ".env"
    elif kind == "excluded":
        config.retrieval.exclude = ["player.js"]
    elif kind == "disabled":
        config.llm.enabled = False
    with pytest.raises((ValueError, RuntimeError)):
        prepare_file_questions(tmp_path, config, tmp_path / ".devstill", plan, 0)
    assert not agent.requests


def test_empty_file_can_be_reviewed(question_context, agent, tmp_path):
    config, plan, path, _ = question_context
    path.write_bytes(b"")
    session = prepare_file_questions(tmp_path, config, tmp_path / ".devstill", plan, 0)
    session.respond()
    context = json.loads(
        agent.requests[0][0][1]["content"].split("CONTEXT_JSON:\n", 1)[1]
    )
    assert context["selected_file"]["content"] == ""
