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

import httpx

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
    stop_reason: str | None = None


@dataclass(frozen=True, slots=True)
class PiResolvedLimits:
    requested_context: str | int
    context_window: int
    max_output_tokens: int
    source: str
    warning: str | None = None


PiProgress = Callable[[str], None]

_TOOL_CONTEXT_LIMIT = 160
_VISIBLE_TOOLS = frozenset(
    {"read", "edit", "write", "devstill_delete", "complete_plan"}
)
_CONTEXT_FALLBACK = 8192


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
    if tool in {"edit", "write", "devstill_delete"} and path:
        return f"Pi · {tool} — {path}"
    if tool == "complete_plan":
        return "Pi · check staged changes against Plan"
    return f"Pi · {tool}"


def _model_records(payload: object) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    records = payload.get("data") or payload.get("models") or []
    return [record for record in records if isinstance(record, dict)]


def _record_names(record: dict[str, Any]) -> set[str]:
    names = {
        str(record.get(key, "")).strip()
        for key in ("id", "name", "model")
        if record.get(key)
    }
    aliases = record.get("aliases")
    if isinstance(aliases, list):
        names.update(str(alias).strip() for alias in aliases if alias)
    return names


def _context_value(record: dict[str, Any]) -> int | None:
    containers = [record]
    for key in ("meta", "details"):
        nested = record.get(key)
        if isinstance(nested, dict):
            containers.append(nested)
    for container in containers:
        for key in (
            "n_ctx",
            "context_window",
            "contextWindow",
            "context_length",
            "max_context_length",
        ):
            value = container.get(key)
            if isinstance(value, int) and not isinstance(value, bool) and value >= 4096:
                return value
    return None


def resolve_pi_limits(
    llm: LlmConfig,
    executor: ExecutorConfig,
    *,
    client: httpx.Client | None = None,
) -> PiResolvedLimits:
    requested = executor.context_window
    warning: str | None = None
    if isinstance(requested, int):
        context_window = requested
        source = "configured"
    else:
        context_window = _CONTEXT_FALLBACK
        source = "fallback"
        if not llm.enabled or not llm.model:
            return PiResolvedLimits(
                requested_context=requested,
                context_window=context_window,
                max_output_tokens=min(
                    executor.max_output_tokens,
                    max(1024, context_window // 3),
                ),
                source=source,
            )
        owned = client is None
        http = client or httpx.Client(timeout=min(llm.timeout_seconds, 5.0))
        try:
            headers: dict[str, str] = {}
            api_key = resolve_api_key(llm)
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"
            response = http.get(f"{llm.base_url}/models", headers=headers)
            response.raise_for_status()
            records = _model_records(response.json())
            matches = [record for record in records if llm.model in _record_names(record)]
            selected = matches[0] if len(matches) == 1 else None
            if selected is None and len(records) == 1:
                selected = records[0]
            detected = _context_value(selected) if selected is not None else None
            if detected is not None:
                context_window = detected
                source = "model metadata"
            else:
                warning = (
                    "The model endpoint did not advertise a usable context window; "
                    f"using the conservative {context_window:,}-token fallback."
                )
        except (CredentialError, httpx.HTTPError, ValueError, TypeError) as exc:
            warning = (
                f"Could not detect the model context window ({exc}); using the "
                f"conservative {context_window:,}-token fallback."
            )
        finally:
            if owned:
                http.close()
    resolved_output = min(
        executor.max_output_tokens,
        max(1024, context_window // 3),
    )
    return PiResolvedLimits(
        requested_context=requested,
        context_window=context_window,
        max_output_tokens=resolved_output,
        source=source,
        warning=warning,
    )


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
    limits: PiResolvedLimits | None = None,
) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    api_key = f"${llm.api_key_env}" if llm.api_key_env else "devstill-local"
    selected_executor = executor or ExecutorConfig()
    resolved = limits or PiResolvedLimits(
        requested_context=selected_executor.context_window,
        context_window=(
            selected_executor.context_window
            if isinstance(selected_executor.context_window, int)
            else _CONTEXT_FALLBACK
        ),
        max_output_tokens=min(
            selected_executor.max_output_tokens,
            max(
                1024,
                (
                    selected_executor.context_window
                    if isinstance(selected_executor.context_window, int)
                    else _CONTEXT_FALLBACK
                )
                // 3,
            ),
        ),
        source="configured" if isinstance(selected_executor.context_window, int) else "fallback",
    )
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
                        "contextWindow": resolved.context_window,
                        "maxTokens": resolved.max_output_tokens,
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
    (directory / "settings.json").write_text(
        json.dumps({"compaction": {"enabled": False}}, indent=2) + "\n",
        encoding="utf-8",
    )
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


def _assistant_stop(events: list[dict[str, Any]]) -> tuple[str | None, str | None]:
    for event in reversed(events):
        if event.get("type") != "agent_end":
            continue
        messages_value = event.get("messages")
        if not isinstance(messages_value, list):
            continue
        for message in reversed(messages_value):
            if not isinstance(message, dict):
                continue
            reason = message.get("stopReason")
            error = message.get("errorMessage")
            if isinstance(reason, str):
                return reason, error if isinstance(error, str) else None
    return None, None


def _provider_error_hint(message: str) -> str:
    lowered = message.casefold()
    if "chat_template_kwargs" in lowered or "enable_thinking" in lowered:
        return " Set executor.reasoning to auto if this endpoint cannot disable thinking."
    if "tool_choice" in lowered or "required" in lowered:
        return " Pi execution requires OpenAI-compatible required tool selection."
    return ""


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
        attempt: int = 1,
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
            "read,edit,write,devstill_delete,complete_plan",
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

        context = child_environment.get("DEVSTILL_PI_CONTEXT_WINDOW", "unknown")
        output = child_environment.get("DEVSTILL_PI_MAX_OUTPUT_TOKENS", "unknown")
        reasoning = executor.reasoning
        notify(
            f"Starting Pi with {llm.model}; strict Plan tools; context {context}; "
            f"output cap {output}; reasoning {reasoning}"
        )
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
        controlled_stop: str | None = None
        active_tools = 0
        action_calls = 0
        mutation_attempts = 0
        last_failed_check: int | None = None
        plan_complete = False
        verification_stalled = False
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
                    active_tools += 1
                    if tool != "complete_plan":
                        action_calls += 1
                    if tool in {"edit", "write", "devstill_delete"}:
                        mutation_attempts += 1
                        verification_stalled = False
                    notify(_tool_activity(tool, event.get("args")))
                if event.get("type") == "tool_execution_end":
                    active_tools = max(0, active_tools - 1)
                    if event.get("toolName") == "complete_plan":
                        result = event.get("result")
                        details = result.get("details") if isinstance(result, dict) else None
                        unmet = details.get("unmet") if isinstance(details, dict) else None
                        if event.get("isError") is False and unmet == []:
                            plan_complete = True
                        else:
                            if isinstance(unmet, list):
                                reasons = [
                                    context for value in unmet[:3]
                                    if (context := _bounded_context(value, _TOOL_CONTEXT_LIMIT))
                                ]
                                if reasons:
                                    notify("Pi · Plan check needs changes — " + "; ".join(reasons))
                            verification_stalled = last_failed_check == mutation_attempts
                            last_failed_check = mutation_attempts
                    # Wait for the current tool batch to finish before stopping:
                    # killing a running write could leave an incomplete file.
                    if active_tools == 0:
                        if plan_complete:
                            controlled_stop = "plan_complete"
                            notify("Pi · staged Plan checks passed")
                        elif action_calls >= executor.max_tool_calls:
                            controlled_stop = "tool_limit"
                            notify(
                                f"Pi · {executor.max_tool_calls}-tool-call limit reached; "
                                "checking staged changes"
                            )
                        elif verification_stalled:
                            controlled_stop = "verification_stalled"
                            notify("Pi · Plan check repeated without edits; checking staged changes")
                        if controlled_stop is not None:
                            break
                if event.get("type") == "agent_end":
                    agent_ended = True
        try:
            process.stdin.close()
        except OSError:
            pass
        forced_stop = controlled_stop is not None
        if controlled_stop is not None and process.poll() is None:
            process.terminate()
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
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
        elapsed = time.monotonic() - started
        if controlled_stop is not None:
            events.append({"type": "devstill_stop", "reason": controlled_stop})
        log_path.parent.mkdir(parents=True, exist_ok=True)
        mode = "w" if attempt == 1 else "a"
        with log_path.open(mode, encoding="utf-8") as handle:
            handle.write(
                json.dumps({"type": "devstill_attempt", "attempt": attempt}) + "\n"
            )
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
            raise PiError(
                f"Pi execution failed: {detail}.{_provider_error_hint(detail)} "
                f"Event log: {log_path}"
            )
        if not agent_ended and controlled_stop is None:
            raise PiError(f"Pi ended without an agent_end event. Event log: {log_path}")
        tool_calls = sum(
            event.get("type") == "tool_execution_start" for event in events
        )
        stop_reason, provider_error = _assistant_stop(events)
        if stop_reason == "error" and controlled_stop is None:
            detail = provider_error or "the provider returned an error"
            raise PiError(
                f"Pi provider request failed: {detail}.{_provider_error_hint(detail)} "
                f"Event log: {log_path}"
            )
        notify(f"Pi finished after {tool_calls} tool calls in {elapsed:.1f}s")
        return PiRunResult(events, tool_calls, elapsed, controlled_stop or stop_reason)
