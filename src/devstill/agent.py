from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Self
from uuid import uuid4

import httpx
from pydantic import BaseModel, ConfigDict

from devstill.config import LlmConfig
from devstill.credentials import CredentialError, resolve_api_key


class AgentError(RuntimeError):
    pass


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AgentPing(_StrictModel):
    status: str


@dataclass(frozen=True, slots=True)
class AgentTestResult:
    model: str
    endpoint: str
    elapsed_seconds: float

    def to_dict(self) -> dict[str, str | float]:
        return {
            "status": "ok",
            "model": self.model,
            "endpoint": self.endpoint,
            "elapsed_seconds": round(self.elapsed_seconds, 3),
        }


def completion_url(base_url: str) -> str:
    normalized = base_url.rstrip("/")
    if normalized.endswith("/chat/completions"):
        return normalized
    return f"{normalized}/chat/completions"


def extract_json_object(content: str) -> dict[str, Any]:
    text = content.strip()
    fenced = re.fullmatch(
        r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL | re.IGNORECASE
    )
    if fenced:
        text = fenced.group(1).strip()
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("agent response did not contain a JSON object") from None
        try:
            value = json.loads(text[start : end + 1])
        except json.JSONDecodeError as exc:
            raise ValueError(f"agent returned invalid JSON: {exc.msg}") from exc
    if not isinstance(value, dict):
        raise TypeError("agent response must be a JSON object")
    return value


class AgentClient:
    def __init__(
        self,
        config: LlmConfig,
        *,
        client: httpx.Client | None = None,
        log_path: Path | None = None,
    ) -> None:
        self.config = config
        self.endpoint = completion_url(config.base_url)
        self.log_path = log_path
        self._owns_client = client is None
        self.client = client or httpx.Client(
            timeout=config.timeout_seconds,
            follow_redirects=True,
        )

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        try:
            api_key = resolve_api_key(self.config)
        except CredentialError as exc:
            raise AgentError(str(exc)) from exc
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        return headers

    def _write_diagnostic(self, entry: dict[str, Any]) -> bool:
        """Append a sanitized request result without ever logging authorization."""
        if self.log_path is None:
            return False
        record = {
            "timestamp": datetime.now(UTC).isoformat(),
            "endpoint": self.endpoint,
            "model": self.config.model,
            **entry,
        }
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError:
            return False
        return True

    def count_prompt_tokens(
        self, messages: list[dict[str, str]], *, reasoning: str = "auto"
    ) -> int:
        """Count the served chat template, including generation framing.

        Budgeting is opt-in because /tokenize is not part of every compatible API.
        An unavailable or invalid tokenizer fails explicitly when budgeting is enabled.
        """
        payload = {
            "model": self.config.model,
            "messages": messages,
            "add_generation_prompt": True,
        }
        if reasoning == "off":
            payload["chat_template_kwargs"] = {"enable_thinking": False}
        key = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        cache = getattr(self, "_prompt_token_cache", None)
        if cache is None:
            cache = self._prompt_token_cache = {}
        if key not in cache:
            response = self.client.post(
                self.config.base_url.removesuffix("/v1") + "/tokenize",
                headers=self._headers(),
                json=payload,
            )
            response.raise_for_status()
            body = response.json()
            count = body.get("count")
            if type(count) is not int or count < 0:
                raise AgentError("Server tokenizer returned an invalid token count")
            server_window = body.get("max_model_len")
            if type(server_window) is int and server_window > 0:
                self._tokenizer_context_window = server_window
            if len(cache) >= 256:
                cache.clear()
            cache[key] = count
        return cache[key]

    def _post_logged(
        self,
        payload: dict[str, Any],
        *,
        request_id: str,
        attempt: int,
        mode: str,
    ) -> httpx.Response:
        if self.config.context_window is not None:
            count = self.count_prompt_tokens(
                payload["messages"],
                reasoning="off" if "chat_template_kwargs" in payload else "auto",
            )
            window = min(
                self.config.context_window,
                getattr(self, "_tokenizer_context_window", self.config.context_window),
            )
            if count + payload["max_tokens"] + 256 > window:
                raise AgentError(
                    "Fully serialized prompt and output reservation exceed the configured context window"
                )
        started = time.monotonic()
        message_summary = [
            {
                "role": message.get("role", "unknown"),
                "characters": len(str(message.get("content", ""))),
            }
            for message in payload.get("messages", [])
            if isinstance(message, dict)
        ]
        base_entry = {
            "request_id": request_id,
            "attempt": attempt,
            "fallback_attempts": attempt - 1,
            "mode": mode,
            "reasoning_mode": ("off" if "chat_template_kwargs" in payload else "auto"),
            "schema_name": (
                payload.get("response_format", {}).get("json_schema", {}).get("name")
            ),
            "max_tokens": payload.get("max_tokens"),
            "messages": message_summary,
        }
        try:
            response = self.client.post(
                self.endpoint,
                headers=self._headers(),
                json=payload,
            )
        except Exception as exc:
            self._write_diagnostic(
                {
                    **base_entry,
                    "elapsed_seconds": round(time.monotonic() - started, 3),
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                }
            )
            raise
        try:
            response_body: Any = response.json()
        except ValueError:
            response_body = response.text
        finish_reason: Any = None
        usage: Any = None
        if isinstance(response_body, dict):
            usage = response_body.get("usage")
            choices = response_body.get("choices")
            if isinstance(choices, list) and choices and isinstance(choices[0], dict):
                finish_reason = choices[0].get("finish_reason")
        self._write_diagnostic(
            {
                **base_entry,
                "elapsed_seconds": round(time.monotonic() - started, 3),
                "status_code": response.status_code,
                "finish_reason": finish_reason,
                "usage": usage,
                "response": response_body,
            }
        )
        return response

    def _diagnostic_error(self, message: str) -> AgentError:
        if self.log_path is not None:
            return AgentError(f"{message}. Agent response log: {self.log_path}")
        return AgentError(message)

    @staticmethod
    def _unsupported_feature(
        response: httpx.Response,
        payload: dict[str, Any],
    ) -> Literal["reasoning", "schema"] | None:
        if response.status_code not in {400, 404, 415, 422, 501}:
            return None
        body = response.text.casefold()
        if "chat_template_kwargs" in payload and any(
            marker in body
            for marker in ("chat_template_kwargs", "enable_thinking", "thinking")
        ):
            return "reasoning"
        if "response_format" in payload and any(
            marker in body
            for marker in ("response_format", "json_schema", "structured output")
        ):
            return "schema"
        if any(marker in body for marker in ("unknown field", "unsupported")):
            if "chat_template_kwargs" in payload:
                return "reasoning"
            if "response_format" in payload:
                return "schema"
        return None

    @staticmethod
    def _content(response: httpx.Response, max_output_tokens: int) -> str:
        try:
            payload = response.json()
            choice = payload["choices"][0]
            content = choice["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise AgentError(
                "agent response did not use the Chat Completions shape"
            ) from exc
        if choice.get("finish_reason") == "length":
            completion_tokens = payload.get("usage", {}).get("completion_tokens")
            usage = (
                f" ({completion_tokens:,} completion tokens reported)"
                if isinstance(completion_tokens, int)
                else ""
            )
            raise AgentError(
                f"agent output reached the {max_output_tokens:,}-token limit{usage} "
                "before returning a complete response"
            )
        if isinstance(content, str) and content.strip():
            return content
        if isinstance(content, list):
            parts = [
                item.get("text", "")
                for item in content
                if isinstance(item, dict) and item.get("type") == "text"
            ]
            combined = "".join(parts).strip()
            if combined:
                return combined
        raise AgentError("agent returned empty message content")

    def chat(
        self,
        messages: list[dict[str, str]],
        *,
        schema: dict[str, Any] | None = None,
        schema_name: str = "devstill_output",
        max_output_tokens: int | None = None,
        reasoning: Literal["auto", "off"] = "auto",
        max_attempts: int = 3,
    ) -> str:
        if not 1 <= max_attempts <= 3:
            raise ValueError("max_attempts must be between 1 and 3")
        token_limit = (
            self.config.max_output_tokens
            if max_output_tokens is None
            else max_output_tokens
        )
        if not 64 <= token_limit <= 65536:
            raise ValueError("max_output_tokens must be between 64 and 65536")
        payload: dict[str, Any] = {
            "model": self.config.model,
            "messages": messages,
            "max_tokens": token_limit,
        }
        if reasoning == "off":
            payload["chat_template_kwargs"] = {"enable_thinking": False}
        if schema is not None:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": schema_name,
                    "strict": True,
                    "schema": schema,
                },
            }
        request_id = uuid4().hex
        try:
            attempt = 1
            while True:
                features = []
                if "response_format" in payload:
                    features.append("json_schema")
                if "chat_template_kwargs" in payload:
                    features.append("reasoning_off")
                mode = "+".join(features) if features else "plain"
                if attempt > 1:
                    mode = f"compatibility_fallback:{mode}"
                response = self._post_logged(
                    payload,
                    request_id=request_id,
                    attempt=attempt,
                    mode=mode,
                )
                unsupported = self._unsupported_feature(response, payload)
                if unsupported is None or attempt >= max_attempts:
                    break
                if unsupported == "reasoning":
                    payload.pop("chat_template_kwargs", None)
                else:
                    payload.pop("response_format", None)
                attempt += 1
            response.raise_for_status()
        except AgentError as exc:
            raise self._diagnostic_error(str(exc)) from exc
        except httpx.TimeoutException as exc:
            raise self._diagnostic_error(
                f"agent request timed out after {self.config.timeout_seconds:g} seconds"
            ) from exc
        except httpx.HTTPStatusError as exc:
            body = exc.response.text.strip().replace("\n", " ")[:500]
            detail = f": {body}" if body else ""
            raise self._diagnostic_error(
                f"agent returned HTTP {exc.response.status_code}{detail}"
            ) from exc
        except httpx.RequestError as exc:
            raise self._diagnostic_error(
                f"could not reach agent endpoint: {exc}"
            ) from exc
        try:
            return self._content(response, token_limit)
        except AgentError as exc:
            raise self._diagnostic_error(str(exc)) from exc

    def test(self) -> AgentTestResult:
        schema = {
            "type": "object",
            "additionalProperties": False,
            "properties": {"status": {"type": "string", "enum": ["ok"]}},
            "required": ["status"],
        }
        messages = [
            {
                "role": "system",
                "content": "Return JSON only and follow the supplied schema.",
            },
            {"role": "user", "content": 'Return {"status":"ok"}.'},
        ]
        started = time.monotonic()
        content = self.chat(
            messages,
            schema=schema,
            schema_name="devstill_agent_test",
            max_output_tokens=256,
        )
        try:
            ping = AgentPing.model_validate(extract_json_object(content))
        except (TypeError, ValueError) as exc:
            raise AgentError(f"agent test returned invalid output: {exc}") from exc
        if ping.status != "ok":
            raise AgentError("agent test did not return status=ok")
        return AgentTestResult(
            model=self.config.model,
            endpoint=self.endpoint,
            elapsed_seconds=time.monotonic() - started,
        )
