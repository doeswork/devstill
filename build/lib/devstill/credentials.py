from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
from pathlib import Path
from typing import Any

from devstill.config import LlmConfig

CREDENTIALS_PATH_ENV = "DEVSTILL_CREDENTIALS_PATH"
CREDENTIALS_VERSION = 1


class CredentialError(RuntimeError):
    pass


def credentials_path() -> Path:
    configured = os.environ.get(CREDENTIALS_PATH_ENV)
    if configured:
        expanded = Path(os.path.expandvars(configured)).expanduser()
        return Path(os.path.abspath(expanded))
    config_home = os.environ.get("XDG_CONFIG_HOME")
    base = (
        Path(os.path.expandvars(config_home)).expanduser()
        if config_home
        else Path.home() / ".config"
    )
    return Path(os.path.abspath(base / "devstill" / "credentials.json"))


def credential_id(config: LlmConfig) -> str:
    identity = f"{config.base_url}\0{config.api_key_env}".encode()
    return hashlib.sha256(identity).hexdigest()


def _empty_store() -> dict[str, Any]:
    return {"version": CREDENTIALS_VERSION, "credentials": {}}


def _load(path: Path) -> dict[str, Any]:
    if path.is_symlink():
        raise CredentialError(f"Refusing to read symlinked credential file: {path}")
    if not path.exists():
        return _empty_store()
    if not path.is_file():
        raise CredentialError(f"Credential path is not a regular file: {path}")
    permissions = stat.S_IMODE(path.stat().st_mode)
    if permissions & 0o077:
        raise CredentialError(
            f"Credential file permissions are too broad: {path}. Run `chmod 600 {path}`."
        )
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CredentialError(f"Could not read credential file {path}: {exc}") from exc
    if (
        not isinstance(raw, dict)
        or raw.get("version") != CREDENTIALS_VERSION
        or not isinstance(raw.get("credentials"), dict)
    ):
        raise CredentialError(f"Credential file has an unsupported format: {path}")
    return raw


def _write(path: Path, data: dict[str, Any]) -> None:
    if path.is_symlink():
        raise CredentialError(f"Refusing to replace symlinked credential file: {path}")
    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if path.parent.name == "devstill":
            path.parent.chmod(0o700)
    except OSError as exc:
        raise CredentialError(
            f"Could not prepare credential directory {path.parent}: {exc}"
        ) from exc
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=".credentials.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            json.dump(data, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
            temporary = Path(handle.name)
        temporary.chmod(0o600)
        temporary.replace(path)
    except OSError as exc:
        raise CredentialError(f"Could not write credential file {path}: {exc}") from exc
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def saved_api_key(config: LlmConfig) -> str | None:
    entry = _load(credentials_path())["credentials"].get(credential_id(config))
    if entry is None:
        return None
    if not isinstance(entry, dict) or not isinstance(entry.get("api_key"), str):
        raise CredentialError("Saved agent credential is malformed")
    return entry["api_key"]


def store_api_key(config: LlmConfig, api_key: str) -> Path:
    if not api_key or any(character in api_key for character in "\r\n\0"):
        raise ValueError("API token must be a non-empty single line")
    path = credentials_path()
    data = _load(path)
    data["credentials"][credential_id(config)] = {
        "base_url": config.base_url,
        "api_key_env": config.api_key_env,
        "api_key": api_key,
    }
    _write(path, data)
    return path


def delete_api_key(config: LlmConfig) -> bool:
    path = credentials_path()
    data = _load(path)
    removed = data["credentials"].pop(credential_id(config), None) is not None
    if not removed:
        return False
    if data["credentials"]:
        _write(path, data)
    else:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            raise CredentialError(
                f"Could not remove credential file {path}: {exc}"
            ) from exc
    return True


def api_key_source(config: LlmConfig) -> str:
    if os.environ.get(config.api_key_env):
        return "environment"
    try:
        return "saved" if saved_api_key(config) else "not set"
    except CredentialError:
        return "saved credentials unavailable"


def resolve_api_key(config: LlmConfig) -> str | None:
    return os.environ.get(config.api_key_env) or saved_api_key(config)
