from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from devstill.agent import AgentClient, AgentError, completion_url, extract_json_object
from devstill.config import (
    LlmConfig,
    load_config,
    update_llm_config,
    write_default_config,
)
from devstill.credentials import store_api_key


def llm_config(**updates: object) -> LlmConfig:
    return LlmConfig.model_validate(
        {
            "enabled": True,
            "base_url": "http://127.0.0.1:8080/v1",
            "model": "local-model",
            **updates,
        }
    )


def completion(content: str, status: int = 200) -> httpx.Response:
    return httpx.Response(
        status,
        json={"choices": [{"message": {"content": content}}]},
    )


def test_llm_config_defaults_and_url_safety() -> None:
    legacy = LlmConfig.model_validate(
        {
            "enabled": False,
            "base_url": "http://localhost:8080/v1/",
            "model": "",
        }
    )
    assert legacy.base_url == "http://localhost:8080/v1"
    assert legacy.api_key_env == "DEVSTILL_AGENT_API_KEY"
    assert legacy.timeout_seconds == 300
    assert legacy.max_output_tokens == 2048
    assert legacy.planning_max_output_tokens == 6144
    assert legacy.planning_reasoning == "off"

    for url in (
        "ftp://localhost/v1",
        "http://user:secret@localhost/v1",
        "http://localhost/v1?key=secret",
        "http://localhost/v1#fragment",
        "http://local host/v1",
        "http://localhost:invalid/v1",
    ):
        with pytest.raises(ValueError):
            llm_config(base_url=url)
    with pytest.raises(ValueError, match="model is required"):
        LlmConfig(enabled=True)
    with pytest.raises(ValueError, match="environment variable"):
        llm_config(api_key_env="NOT-VALID")


def test_atomic_llm_update_preserves_other_config_and_refuses_symlink(
    tmp_path: Path,
) -> None:
    config_path = write_default_config(tmp_path / "devstill.json")
    before = json.loads(config_path.read_text(encoding="utf-8"))
    updated = update_llm_config(
        config_path,
        load_config(config_path),
        enabled=True,
        base_url="http://localhost:1234/v1/",
        model="test-model",
        timeout_seconds=42,
    )
    after = json.loads(config_path.read_text(encoding="utf-8"))
    assert after["files"] == before["files"]
    assert after["embedding"] == before["embedding"]
    assert updated.llm.base_url == "http://localhost:1234/v1"
    assert after["llm"]["timeout_seconds"] == 42

    target = write_default_config(tmp_path / "target.json")
    link = tmp_path / "linked.json"
    try:
        link.symlink_to(target)
    except OSError as exc:
        pytest.skip(f"symlinks are unavailable: {exc}")
    with pytest.raises(ValueError, match="symlinked configuration"):
        update_llm_config(link, load_config(link), model="another-model")


def test_completion_url_and_json_extraction() -> None:
    assert completion_url("http://localhost:8080/v1") == (
        "http://localhost:8080/v1/chat/completions"
    )
    assert completion_url("http://localhost/chat/completions") == (
        "http://localhost/chat/completions"
    )
    assert extract_json_object('```json\n{"status":"ok"}\n```') == {"status": "ok"}
    assert extract_json_object('Result: {"status":"ok"}') == {"status": "ok"}
    with pytest.raises(ValueError, match="JSON object"):
        extract_json_object("no object")


def test_chat_uses_strict_schema_auth_and_configured_limits(monkeypatch) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return completion('{"status":"ok"}')

    monkeypatch.setenv("LOCAL_AGENT_SECRET", "super-secret")
    transport = httpx.MockTransport(handler)
    http = httpx.Client(transport=transport)
    config = llm_config(api_key_env="LOCAL_AGENT_SECRET", max_output_tokens=777)
    client = AgentClient(config, client=http)
    content = client.chat(
        [{"role": "user", "content": "ping"}],
        schema={"type": "object"},
        schema_name="ping",
    )
    assert content == '{"status":"ok"}'
    assert len(requests) == 1
    assert requests[0].headers["Authorization"] == "Bearer super-secret"
    assert "super-secret" not in str(requests[0].url)
    payload = json.loads(requests[0].content)
    assert payload["max_tokens"] == 777
    assert payload["response_format"]["type"] == "json_schema"
    assert payload["response_format"]["json_schema"]["strict"] is True
    http.close()


def test_chat_supports_planning_limit_and_reasoning_off() -> None:
    payloads: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payloads.append(json.loads(request.content))
        return completion('{"status":"ok"}')

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = AgentClient(llm_config(), client=http)
    client.chat(
        [],
        schema={"type": "object"},
        max_output_tokens=6144,
        reasoning="off",
    )

    assert payloads[0]["max_tokens"] == 6144
    assert payloads[0]["chat_template_kwargs"] == {"enable_thinking": False}
    http.close()


def test_chat_uses_remembered_token_when_environment_is_unset() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return completion('{"status":"ok"}')

    config = llm_config()
    store_api_key(config, "remembered-secret")
    http = httpx.Client(transport=httpx.MockTransport(handler))

    AgentClient(config, client=http).chat([])

    assert requests[0].headers["Authorization"] == "Bearer remembered-secret"
    http.close()


def test_chat_retries_without_response_format_for_compatible_server() -> None:
    payloads: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payloads.append(json.loads(request.content))
        if len(payloads) == 1:
            return httpx.Response(400, text="unsupported response_format json_schema")
        return completion('{"status":"ok"}')

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = AgentClient(llm_config(), client=http)
    assert client.chat([], schema={"type": "object"}) == '{"status":"ok"}'
    assert "response_format" in payloads[0]
    assert "response_format" not in payloads[1]
    http.close()


def test_chat_retries_without_reasoning_extension_for_compatible_server() -> None:
    payloads: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payloads.append(json.loads(request.content))
        if len(payloads) == 1:
            return httpx.Response(400, text="unknown field chat_template_kwargs")
        return completion('{"status":"ok"}')

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = AgentClient(llm_config(), client=http)
    assert (
        client.chat([], schema={"type": "object"}, reasoning="off") == '{"status":"ok"}'
    )
    assert "chat_template_kwargs" in payloads[0]
    assert "chat_template_kwargs" not in payloads[1]
    assert "response_format" in payloads[1]
    http.close()


def test_agent_log_records_reasoning_compatibility_fallback(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        if "chat_template_kwargs" in payload:
            return httpx.Response(400, text="unknown field chat_template_kwargs")
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {"content": '{"status":"ok"}'},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"completion_tokens": 8},
            },
        )

    log_path = tmp_path / "agent.jsonl"
    http = httpx.Client(transport=httpx.MockTransport(handler))
    AgentClient(llm_config(), client=http, log_path=log_path).chat(
        [],
        schema={"type": "object"},
        reasoning="off",
        max_output_tokens=6144,
    )

    records = [
        json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()
    ]
    assert [record["fallback_attempts"] for record in records] == [0, 1]
    assert [record["reasoning_mode"] for record in records] == ["off", "auto"]
    assert records[1]["max_tokens"] == 6144
    assert records[1]["finish_reason"] == "stop"
    assert records[1]["usage"] == {"completion_tokens": 8}
    http.close()


def test_chat_negotiates_reasoning_and_schema_with_three_attempt_cap() -> None:
    payloads: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payloads.append(json.loads(request.content))
        if len(payloads) == 1:
            return httpx.Response(400, text="unsupported response_format")
        if len(payloads) == 2:
            return httpx.Response(400, text="unsupported chat_template_kwargs")
        return completion('{"status":"ok"}')

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = AgentClient(llm_config(), client=http)
    assert (
        client.chat([], schema={"type": "object"}, reasoning="off") == '{"status":"ok"}'
    )
    assert len(payloads) == 3
    assert "response_format" in payloads[0]
    assert "chat_template_kwargs" in payloads[0]
    assert "response_format" not in payloads[1]
    assert "chat_template_kwargs" in payloads[1]
    assert "response_format" not in payloads[2]
    assert "chat_template_kwargs" not in payloads[2]
    http.close()


@pytest.mark.parametrize("content", ["", '{"items":['])
def test_chat_reports_truncated_empty_or_partial_content(content: str) -> None:
    response = httpx.Response(
        200,
        json={
            "choices": [{"message": {"content": content}, "finish_reason": "length"}],
            "usage": {"completion_tokens": 6144},
        },
    )
    http = httpx.Client(transport=httpx.MockTransport(lambda _request: response))
    with pytest.raises(
        AgentError,
        match=r"6,144-token limit \(6,144 completion tokens reported\)",
    ):
        AgentClient(llm_config(), client=http).chat([], max_output_tokens=6144)
    http.close()


@pytest.mark.parametrize(
    ("handler", "message"),
    [
        (lambda _request: httpx.Response(401, text="unauthorized"), "HTTP 401"),
        (lambda _request: completion(""), "empty message content"),
        (
            lambda _request: httpx.Response(200, json={"choices": []}),
            "Chat Completions",
        ),
    ],
)
def test_chat_reports_http_and_shape_errors(handler, message: str) -> None:
    http = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(AgentError, match=message):
        AgentClient(llm_config(), client=http).chat([])
    http.close()


def test_chat_reports_timeout_and_agent_test_validates_output() -> None:
    request = httpx.Request("POST", "http://localhost/v1/chat/completions")

    def timeout(_request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("too slow", request=request)

    timed_http = httpx.Client(transport=httpx.MockTransport(timeout))
    with pytest.raises(AgentError, match="timed out after 300 seconds"):
        AgentClient(llm_config(), client=timed_http).chat([])
    timed_http.close()

    ok_http = httpx.Client(
        transport=httpx.MockTransport(
            lambda _request: completion('```json\n{"status":"ok"}\n```')
        )
    )
    result = AgentClient(llm_config(), client=ok_http).test()
    assert result.model == "local-model"
    assert result.endpoint.endswith("/chat/completions")
    assert result.elapsed_seconds >= 0
    ok_http.close()

    bad_http = httpx.Client(
        transport=httpx.MockTransport(lambda _request: completion('{"status":"wrong"}'))
    )
    with pytest.raises(AgentError, match="status=ok"):
        AgentClient(llm_config(), client=bad_http).test()
    bad_http.close()


def test_agent_test_uses_small_general_output_limit() -> None:
    payloads: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payloads.append(json.loads(request.content))
        return completion('{"status":"ok"}')

    http = httpx.Client(transport=httpx.MockTransport(handler))
    AgentClient(llm_config(max_output_tokens=8192), client=http).test()
    assert payloads[0]["max_tokens"] == 256
    assert "chat_template_kwargs" not in payloads[0]
    http.close()


def test_agent_diagnostic_log_preserves_raw_response_without_secret(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("LOCAL_AGENT_SECRET", "never-write-this-token")
    response_payload = {
        "choices": [
            {
                "message": {"content": "", "reasoning_content": "thinking"},
                "finish_reason": "length",
            }
        ],
        "usage": {"completion_tokens": 2048},
    }
    http = httpx.Client(
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(200, json=response_payload)
        )
    )
    log_path = tmp_path / ".devstill/logs/agent.jsonl"
    client = AgentClient(
        llm_config(api_key_env="LOCAL_AGENT_SECRET"),
        client=http,
        log_path=log_path,
    )

    with pytest.raises(AgentError, match=r"Agent response log: .*agent\.jsonl"):
        client.chat(
            [{"role": "user", "content": "private source context"}],
            schema={"type": "object"},
            schema_name="diagnostic_test",
        )

    records = [
        json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()
    ]
    assert len(records) == 1
    assert records[0]["status_code"] == 200
    assert records[0]["schema_name"] == "diagnostic_test"
    assert records[0]["max_tokens"] == 2048
    assert records[0]["reasoning_mode"] == "auto"
    assert records[0]["fallback_attempts"] == 0
    assert records[0]["finish_reason"] == "length"
    assert records[0]["usage"] == {"completion_tokens": 2048}
    assert records[0]["messages"] == [{"role": "user", "characters": 22}]
    assert records[0]["response"] == response_payload
    log_text = log_path.read_text(encoding="utf-8")
    assert "never-write-this-token" not in log_text
    assert "private source context" not in log_text
    http.close()
