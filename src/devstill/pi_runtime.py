from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from devstill.config import ExecutorConfig, LlmConfig
from devstill.credentials import CredentialError, resolve_api_key


class PiError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class PiStatus:
    ready: bool
    pi_path: str | None
    pi_version: str | None
    node_path: str | None
    node_version: str | None
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class PiRunResult:
    events: list[dict[str, Any]]
    tool_calls: int
    elapsed_seconds: float


PiProgress = Callable[[str], None]

_TOOL_CONTEXT_LIMIT = 160
_PATTERN_CONTEXT_LIMIT = 80
_VISIBLE_TOOLS = frozenset({"read", "grep", "find", "ls", "edit", "write"})


def _bounded_context(value: object, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    compact = " ".join(value.split())
    if not compact:
        return None
    if len(compact) <= limit:
        return compact
    return f"{compact[: limit - 1]}…"


def _tool_activity(tool: str, args: object) -> str:
    """Describe an allowlisted Pi tool call without exposing payload contents."""
    if tool not in _VISIBLE_TOOLS:
        return "Pi · tool"
    if not isinstance(args, dict):
        return f"Pi · {tool}"
    path = _bounded_context(args.get("path"), _TOOL_CONTEXT_LIMIT)
    pattern = _bounded_context(args.get("pattern"), _PATTERN_CONTEXT_LIMIT)
    if tool == "read" and path:
        offset = args.get("offset")
        limit = args.get("limit")
        window = []
        if isinstance(offset, int):
            window.append(f"offset {offset}")
        if isinstance(limit, int):
            window.append(f"limit {limit}")
        suffix = f" ({', '.join(window)})" if window else ""
        return f"Pi · read — {path}{suffix}"
    if tool in {"grep", "find"}:
        subject = f"“{pattern}”" if pattern else "pattern"
        location = f" in {path}" if path else ""
        return f"Pi · {tool} — {subject}{location}"
    if tool in {"ls", "edit", "write"} and path:
        return f"Pi · {tool} — {path}"
    return f"Pi · {tool}"


def _candidate(name: str, fallback: Path) -> str | None:
    discovered = shutil.which(name)
    if discovered:
        return discovered
    return str(fallback) if fallback.is_file() else None


def _command_version(
    path: str | None,
    *,
    environment: dict[str, str] | None = None,
) -> str | None:
    if path is None:
        return None
    try:
        completed = subprocess.run(
            [path, "--version"],
            capture_output=True,
            check=False,
            text=True,
            timeout=5,
            env=environment,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    value = (completed.stdout or completed.stderr).strip().splitlines()
    return value[0] if completed.returncode == 0 and value else None


def pi_environment() -> dict[str, str]:
    environment = dict(os.environ)
    node_bin = Path.home() / ".local/share/pi-node/current/bin"
    local_bin = Path.home() / ".local/bin"
    prefixes = [str(path) for path in (local_bin, node_bin) if path.is_dir()]
    if prefixes:
        environment["PATH"] = os.pathsep.join(
            [*prefixes, environment.get("PATH", "")]
        ).rstrip(os.pathsep)
    return environment


def inspect_pi() -> PiStatus:
    home = Path.home()
    pi_path = _candidate("pi", home / ".local/bin/pi")
    node_path = _candidate("node", home / ".local/share/pi-node/current/bin/node")
    pi_version = _command_version(pi_path, environment=pi_environment())
    node_version = _command_version(node_path)
    if pi_path is None:
        return PiStatus(
            False, None, None, node_path, node_version, "Pi is not installed"
        )
    if node_path is None:
        return PiStatus(
            False,
            pi_path,
            pi_version,
            None,
            None,
            "Node.js is not available for Pi",
        )
    if pi_version is None:
        return PiStatus(
            False,
            pi_path,
            None,
            node_path,
            node_version,
            "Pi did not report a version",
        )
    try:
        completed = subprocess.run(
            [pi_path, "--help"],
            capture_output=True,
            check=False,
            text=True,
            timeout=10,
            env=pi_environment(),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return PiStatus(False, pi_path, pi_version, node_path, node_version, str(exc))
    help_text = f"{completed.stdout}\n{completed.stderr}"
    required = (
        "--mode",
        "--no-session",
        "--no-context-files",
        "--no-extensions",
        "--no-skills",
        "--tools",
        "--extension",
        "--thinking",
        "--provider",
        "--model",
    )
    missing = [flag for flag in required if flag not in help_text]
    if completed.returncode or missing:
        reason = (
            f"Pi is missing required options: {', '.join(missing)}"
            if missing
            else "Pi help check failed"
        )
        return PiStatus(False, pi_path, pi_version, node_path, node_version, reason)
    return PiStatus(True, pi_path, pi_version, node_path, node_version)


def write_provider_config(
    directory: Path,
    llm: LlmConfig,
    executor: ExecutorConfig | None = None,
) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    api_key = f"${llm.api_key_env}" if llm.api_key_env else "devstill-local"
    pi_max_tokens = (executor or ExecutorConfig()).max_output_tokens
    payload = {
        "providers": {
            "devstill": {
                "baseUrl": llm.base_url,
                "api": "openai-completions",
                "apiKey": api_key,
                "models": [
                    {
                        "id": llm.model,
                        "name": llm.model,
                        "reasoning": False,
                        "input": ["text"],
                        "contextWindow": 131072,
                        "maxTokens": pi_max_tokens,
                        "cost": {
                            "input": 0,
                            "output": 0,
                            "cacheRead": 0,
                            "cacheWrite": 0,
                        },
                        "compat": {
                            "supportsDeveloperRole": False,
                            "supportsReasoningEffort": False,
                            "supportsUsageInStreaming": False,
                            "maxTokensField": "max_tokens",
                        },
                    }
                ],
            }
        }
    }
    destination = directory / "models.json"
    destination.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return destination


def _stream_reader(
    stream: Any,
    output: queue.Queue[tuple[str, str | None]],
    channel: str,
) -> None:
    try:
        for line in iter(stream.readline, ""):
            output.put((channel, line.rstrip("\n")))
    finally:
        output.put((channel, None))


class PiRunner:
    def __init__(self, pi_path: str | None = None) -> None:
        self.pi_path = pi_path

    def run(
        self,
        *,
        workspace: Path,
        config_dir: Path,
        policy_extension: Path,
        prompt: str,
        llm: LlmConfig,
        executor: ExecutorConfig,
        environment: dict[str, str],
        log_path: Path,
        progress: PiProgress | None = None,
    ) -> PiRunResult:
        notify = progress or (lambda _message: None)
        status = inspect_pi() if self.pi_path is None else None
        executable = self.pi_path or (status.pi_path if status else None)
        if status is not None and not status.ready:
            raise PiError(
                f"{status.reason}. Re-run Devstill setup or set DEVSTILL_SKIP_PI=1 "
                "for a search-only installation."
            )
        if executable is None:
            raise PiError("Pi executable is unavailable")
        args = [
            executable,
            "--mode",
            "rpc",
            "--no-session",
            "--no-context-files",
            "--no-extensions",
            "--no-skills",
            "--extension",
            str(policy_extension),
            "--tools",
            "read,grep,find,ls,edit,write",
            "--thinking",
            "off",
            "--provider",
            "devstill",
            "--model",
            llm.model,
        ]
        child_environment = pi_environment()
        child_environment.update(environment)
        child_environment["PI_CODING_AGENT_DIR"] = str(config_dir)
        child_environment["PI_SKIP_VERSION_CHECK"] = "1"
        child_environment["PI_TELEMETRY"] = "0"
        child_environment["DEVSTILL_PI_WORKSPACE"] = str(workspace)
        child_environment["DEVSTILL_PI_MAX_TOOL_CALLS"] = str(executor.max_tool_calls)
        try:
            api_key = resolve_api_key(llm)
        except CredentialError as exc:
            raise PiError(str(exc)) from exc
        child_environment[llm.api_key_env] = api_key or "devstill-local"

        notify(f"Starting Pi with {llm.model}; reasoning is off")
        started = time.monotonic()
        try:
            process = subprocess.Popen(
                args,
                cwd=workspace,
                env=child_environment,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )
        except OSError as exc:
            raise PiError(f"Could not start Pi: {exc}") from exc
        assert process.stdout is not None
        assert process.stderr is not None
        assert process.stdin is not None
        try:
            process.stdin.write(
                json.dumps({"type": "prompt", "message": prompt}, ensure_ascii=False)
                + "\n"
            )
            process.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            process.kill()
            process.wait()
            raise PiError(f"Pi RPC input failed: {exc}") from exc
        messages: queue.Queue[tuple[str, str | None]] = queue.Queue()
        threads = [
            threading.Thread(
                target=_stream_reader,
                args=(process.stdout, messages, "stdout"),
                daemon=True,
            ),
            threading.Thread(
                target=_stream_reader,
                args=(process.stderr, messages, "stderr"),
                daemon=True,
            ),
        ]
        for thread in threads:
            thread.start()
        events: list[dict[str, Any]] = []
        stderr: list[str] = []
        completed_streams: set[str] = set()
        deadline = started + executor.timeout_seconds
        timed_out = False
        agent_ended = False
        while not agent_ended and len(completed_streams) < 2:
            if time.monotonic() >= deadline:
                timed_out = True
                process.terminate()
                break
            try:
                channel, line = messages.get(timeout=0.1)
            except queue.Empty:
                if process.poll() is not None and all(
                    not thread.is_alive() for thread in threads
                ):
                    break
                continue
            if line is None:
                completed_streams.add(channel)
                continue
            if channel == "stderr":
                stderr.append(line)
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                events.append({"type": "unparsed_output", "text": line})
                continue
            if isinstance(event, dict):
                events.append(event)
                if event.get("type") == "tool_execution_start":
                    tool = str(event.get("toolName", "tool"))
                    notify(_tool_activity(tool, event.get("args")))
                if event.get("type") == "agent_end":
                    agent_ended = True
        try:
            process.stdin.close()
        except OSError:
            pass
        forced_stop = False
        if timed_out:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        else:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                forced_stop = True
                process.terminate()
                process.wait(timeout=5)
        elapsed = time.monotonic() - started
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("w", encoding="utf-8") as handle:
            for event in events:
                handle.write(json.dumps(event, ensure_ascii=False) + "\n")
            for line in stderr:
                handle.write(
                    json.dumps({"type": "stderr", "text": line}, ensure_ascii=False)
                    + "\n"
                )
        if timed_out:
            raise PiError(
                f"Pi exceeded the {executor.timeout_seconds:g}-second timeout. "
                f"Event log: {log_path}"
            )
        if process.returncode and not forced_stop:
            detail = stderr[-1] if stderr else f"exit status {process.returncode}"
            raise PiError(f"Pi execution failed: {detail}. Event log: {log_path}")
        if not agent_ended:
            raise PiError(f"Pi ended without an agent_end event. Event log: {log_path}")
        tool_calls = sum(
            event.get("type") == "tool_execution_start" for event in events
        )
        notify(f"Pi finished after {tool_calls} tool calls in {elapsed:.1f}s")
        return PiRunResult(events, tool_calls, elapsed)
