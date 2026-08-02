from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Self
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

    def _post_logged(
        self,
        payload: dict[str, Any],
        *,
        request_id: str,
        attempt: int,
        mode: str,
    ) -> httpx.Response:
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
            "mode": mode,
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
        self._write_diagnostic(
            {
                **base_entry,
                "elapsed_seconds": round(time.monotonic() - started, 3),
                "status_code": response.status_code,
                "response": response_body,
            }
        )
        return response

    def _diagnostic_error(self, message: str) -> AgentError:
        if self.log_path is not None:
            return AgentError(f"{message}. Agent response log: {self.log_path}")
        return AgentError(message)

    @staticmethod
    def _schema_unsupported(response: httpx.Response) -> bool:
        if response.status_code not in {400, 404, 415, 422, 501}:
            return False
        body = response.text.casefold()
        return any(
            marker in body
            for marker in (
                "response_format",
                "json_schema",
                "structured output",
                "unknown field",
                "unsupported",
            )
        )

    @staticmethod
    def _content(response: httpx.Response) -> str:
        try:
            payload = response.json()
            content = payload["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise AgentError(
                "agent response did not use the Chat Completions shape"
            ) from exc
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
    ) -> str:
        payload: dict[str, Any] = {
            "model": self.config.model,
            "messages": messages,
            "max_tokens": self.config.max_output_tokens,
        }
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
            response = self._post_logged(
                payload,
                request_id=request_id,
                attempt=1,
                mode="json_schema" if schema is not None else "plain",
            )
            if schema is not None and self._schema_unsupported(response):
                payload.pop("response_format", None)
                response = self._post_logged(
                    payload,
                    request_id=request_id,
                    attempt=2,
                    mode="schema_fallback",
                )
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
            return self._content(response)
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
        content = self.chat(messages, schema=schema, schema_name="devstill_agent_test")
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
