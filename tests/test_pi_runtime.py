from __future__ import annotations

import json
import stat
import subprocess
from pathlib import Path

import pytest

from devstill.config import ExecutorConfig, LlmConfig
from devstill.pi_runtime import PiError, PiRunner, inspect_pi, write_provider_config


def test_provider_config_references_secret_environment_without_value(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("LOCAL_SECRET", "never-write-this")
    config = LlmConfig(
        enabled=True,
        base_url="http://127.0.0.1:1234/v1",
        model="qwen-local",
        api_key_env="LOCAL_SECRET",
    )
    path = write_provider_config(tmp_path, config)
    rendered = path.read_text(encoding="utf-8")
    payload = json.loads(rendered)
    provider = payload["providers"]["devstill"]
    assert provider["baseUrl"] == "http://127.0.0.1:1234/v1"
    assert provider["apiKey"] == "$LOCAL_SECRET"
    assert provider["models"][0]["id"] == "qwen-local"
    assert provider["models"][0]["contextWindow"] == 131072
    assert provider["models"][0]["maxTokens"] == 49152
    assert "never-write-this" not in rendered


def test_provider_config_uses_executor_token_budget(tmp_path: Path) -> None:
    path = write_provider_config(
        tmp_path,
        LlmConfig(model="qwen-local"),
        ExecutorConfig(max_output_tokens=48000),
    )

    payload = json.loads(path.read_text(encoding="utf-8"))

    assert payload["providers"]["devstill"]["models"][0]["maxTokens"] == 48000


def executable_script(tmp_path: Path, source: str) -> Path:
    path = tmp_path / "fake-pi"
    path.write_text("#!/usr/bin/env python3\n" + source, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def test_pi_runner_streams_tools_and_writes_event_log(
    tmp_path: Path,
    monkeypatch,
) -> None:
    script = executable_script(
        tmp_path,
        "import json\n"
        "print(json.dumps({'type':'agent_start'}), flush=True)\n"
        "print(json.dumps({'type':'tool_execution_start','toolName':'read',"
        "'args':{'path':'src/app.py','offset':10,'limit':20}}), flush=True)\n"
        "print(json.dumps({'type':'tool_execution_start','toolName':'grep',"
        "'args':{'path':'src','pattern':'authenticate'}}), flush=True)\n"
        "print(json.dumps({'type':'tool_execution_start','toolName':'write',"
        "'args':{'path':'src/new.py','content':'private write payload'}}), flush=True)\n"
        "print(json.dumps({'type':'tool_execution_start','toolName':'shell',"
        "'args':{'command':'private shell payload'}}), flush=True)\n"
        "print(json.dumps({'type':'agent_end','messages':[]}), flush=True)\n",
    )
    monkeypatch.setenv("LOCAL_SECRET", "do-not-log")
    llm = LlmConfig(
        enabled=True,
        base_url="http://localhost:1234/v1",
        model="local-model",
        api_key_env="LOCAL_SECRET",
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    log = tmp_path / "events.jsonl"
    progress: list[str] = []
    result = PiRunner(str(script)).run(
        workspace=workspace,
        config_dir=tmp_path / "config",
        policy_extension=tmp_path / "policy.ts",
        prompt="implement",
        llm=llm,
        executor=ExecutorConfig(timeout_seconds=10),
        environment={"DEVSTILL_PI_WRITABLE_PATHS": "[]"},
        log_path=log,
        progress=progress.append,
    )
    assert result.tool_calls == 4
    assert "Pi · read — src/app.py (offset 10, limit 20)" in progress
    assert "Pi · grep — “authenticate” in src" in progress
    assert "Pi · write — src/new.py" in progress
    assert "Pi · tool" in progress
    assert not any("private write payload" in message for message in progress)
    assert not any("private shell payload" in message for message in progress)
    assert any("Pi finished after 4 tool calls in" in message for message in progress)
    assert "do-not-log" not in log.read_text(encoding="utf-8")


def test_pi_runner_rejects_missing_terminal_event(tmp_path: Path) -> None:
    script = executable_script(
        tmp_path,
        "import json\nprint(json.dumps({'type':'agent_start'}), flush=True)\n",
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    with pytest.raises(PiError, match="without an agent_end"):
        PiRunner(str(script)).run(
            workspace=workspace,
            config_dir=tmp_path / "config",
            policy_extension=tmp_path / "policy.ts",
            prompt="implement",
            llm=LlmConfig(
                enabled=True,
                base_url="http://localhost/v1",
                model="model",
            ),
            executor=ExecutorConfig(timeout_seconds=10),
            environment={"DEVSTILL_PI_WRITABLE_PATHS": "[]"},
            log_path=tmp_path / "events.jsonl",
        )


def test_inspect_pi_verifies_required_headless_options(monkeypatch) -> None:
    monkeypatch.setattr(
        "devstill.pi_runtime.shutil.which",
        lambda name: f"/fake/{name}",
    )

    def fake_run(args, **_kwargs):
        if args[1] == "--version":
            output = "v22.19.0" if args[0].endswith("node") else "0.83.0"
            return subprocess.CompletedProcess(args, 0, output, "")
        return subprocess.CompletedProcess(
            args,
            0,
            "--mode --no-session --no-context-files --no-extensions --no-skills "
            "--tools --extension --thinking --provider --model",
            "",
        )

    monkeypatch.setattr("devstill.pi_runtime.subprocess.run", fake_run)
    status = inspect_pi()
    assert status.ready
    assert status.pi_version == "0.83.0"
    assert status.node_version == "v22.19.0"


def test_inspect_pi_reports_missing_required_option(monkeypatch) -> None:
    monkeypatch.setattr(
        "devstill.pi_runtime.shutil.which",
        lambda name: f"/fake/{name}",
    )

    def fake_run(args, **_kwargs):
        if args[1] == "--version":
            return subprocess.CompletedProcess(args, 0, "version", "")
        return subprocess.CompletedProcess(args, 0, "--mode --no-session", "")

    monkeypatch.setattr("devstill.pi_runtime.subprocess.run", fake_run)
    status = inspect_pi()
    assert not status.ready
    assert "--tools" in str(status.reason)
