from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from devstill.config import LlmConfig
from devstill.credentials import (
    CredentialError,
    api_key_source,
    credential_id,
    credentials_path,
    delete_api_key,
    resolve_api_key,
    saved_api_key,
    store_api_key,
)


def agent_config(base_url: str = "http://localhost:8080/v1") -> LlmConfig:
    return LlmConfig(enabled=True, base_url=base_url, model="local-model")


def test_saved_token_round_trip_uses_private_permissions() -> None:
    config = agent_config()

    path = store_api_key(config, "saved-secret")

    assert path == credentials_path()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert saved_api_key(config) == "saved-secret"
    assert resolve_api_key(config) == "saved-secret"
    assert api_key_source(config) == "saved"
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["credentials"][credential_id(config)]["base_url"] == (
        "http://localhost:8080/v1"
    )
    assert delete_api_key(config)
    assert not path.exists()
    assert not delete_api_key(config)


def test_environment_token_overrides_saved_token(monkeypatch) -> None:
    config = agent_config()
    store_api_key(config, "saved-secret")
    monkeypatch.setenv(config.api_key_env, "environment-secret")

    assert resolve_api_key(config) == "environment-secret"
    assert api_key_source(config) == "environment"


def test_credentials_are_separate_per_endpoint() -> None:
    first = agent_config("http://localhost:8080/v1")
    second = agent_config("http://localhost:9000/v1")
    store_api_key(first, "first-secret")
    store_api_key(second, "second-secret")

    assert saved_api_key(first) == "first-secret"
    assert saved_api_key(second) == "second-secret"
    assert delete_api_key(first)
    assert saved_api_key(second) == "second-secret"


def test_insecure_permissions_and_symlinks_are_refused(tmp_path: Path) -> None:
    config = agent_config()
    path = store_api_key(config, "secret")
    path.chmod(0o644)
    with pytest.raises(CredentialError, match="chmod 600"):
        saved_api_key(config)

    target = tmp_path / "target.json"
    target.write_text('{"version": 1, "credentials": {}}', encoding="utf-8")
    target.chmod(0o600)
    path.unlink()
    try:
        path.symlink_to(target)
    except OSError as exc:
        pytest.skip(f"symlinks are unavailable: {exc}")
    with pytest.raises(CredentialError, match="symlinked credential"):
        saved_api_key(config)


@pytest.mark.parametrize("token", ["", "line one\nline two", "nul\0token"])
def test_invalid_tokens_are_rejected(token: str) -> None:
    with pytest.raises(ValueError, match="non-empty single line"):
        store_api_key(agent_config(), token)
