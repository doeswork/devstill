from __future__ import annotations

import json
import os
import re
import stat
import tempfile
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from devstill.devices import EmbeddingDevice
from devstill.installation import data_home
from devstill.retrieval_policy import normalize_retrieval_pattern

CONFIG_FILENAME = "devstill.json"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RepoConfig(StrictModel):
    root: str = "."


class IndexConfig(StrictModel):
    path: str = ".devstill/chroma"
    collection: str = "devstill_code"
    max_chunk_chars: int = Field(default=8000, ge=256)
    max_chunk_lines: int = Field(default=400, ge=1)
    fallback_overlap_lines: int = Field(default=20, ge=0)

    @model_validator(mode="after")
    def validate_overlap(self) -> IndexConfig:
        if self.fallback_overlap_lines >= self.max_chunk_lines:
            raise ValueError("fallback_overlap_lines must be less than max_chunk_lines")
        return self


class EmbeddingConfig(StrictModel):
    model: str = "~/.local/share/devstill/models/jina-embeddings-v2-base-code"
    device: EmbeddingDevice = "auto"
    max_sequence_length: int = Field(default=2048, ge=128, le=8192)
    batch_size: int = Field(default=8, ge=1, le=256)
    normalize_embeddings: bool = True


class FilesConfig(StrictModel):
    use_gitignore: bool = True
    scan_all_text: bool = True
    index_noisy_files: bool = False
    include_ignored: list[str] = Field(default_factory=list)
    include: list[str] = Field(
        default_factory=lambda: [
            "**/*.py",
            "**/*.js",
            "**/*.jsx",
            "**/*.ts",
            "**/*.tsx",
            "**/*.go",
            "**/*.rs",
            "**/*.rb",
            "**/*.rake",
            "**/*.gemspec",
            "**/*.sh",
            "**/*.bash",
            "**/*.java",
            "**/*.json",
            "**/*.toml",
            "**/*.yaml",
            "**/*.yml",
            "**/*.sql",
            "**/*.html",
            "**/*.css",
            "**/*.md",
            "**/*.markdown",
            "**/*.rst",
            "**/*.adoc",
            "**/*.txt",
            "**/*.jsonc",
            "**/*.ini",
            "**/*.cfg",
            "**/*.conf",
            "**/*.service",
            "Dockerfile",
            "Makefile",
            ".gitignore",
            ".env*.example",
            ".env*.sample",
            ".env*.template",
            "Gemfile",
            "Rakefile",
        ]
    )
    exclude: list[str] = Field(
        default_factory=lambda: [
            ".git/**",
            ".devstill/**",
            ".venv/**",
            "venv/**",
            "node_modules/**",
            "dist/**",
            "build/**",
            "__pycache__/**",
        ]
    )


class RetrievalConfig(StrictModel):
    exclude: list[str] = Field(default_factory=list)

    @field_validator("exclude")
    @classmethod
    def validate_excludes(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for value in values:
            pattern = normalize_retrieval_pattern(value)
            if pattern not in normalized:
                normalized.append(pattern)
        return normalized


class LlmConfig(StrictModel):
    enabled: bool = False
    base_url: str = "http://127.0.0.1:8080/v1"
    model: str = ""
    api_key_env: str = "DEVSTILL_AGENT_API_KEY"
    timeout_seconds: float = Field(default=300.0, ge=1, le=3600)
    max_output_tokens: int = Field(default=2048, ge=64, le=65536)
    planning_max_output_tokens: int = Field(default=6144, ge=64, le=65536)
    planning_reasoning: Literal["off", "auto"] = "off"
    context_window: int | None = Field(default=None, ge=4096)
    planning_input_tokens: int | None = Field(default=None, ge=1024)

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        normalized = value.strip().rstrip("/")
        if any(character.isspace() for character in normalized):
            raise ValueError("base_url must not contain whitespace")
        parsed = urlsplit(normalized)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("base_url must be an HTTP(S) URL with a host")
        try:
            _ = parsed.port
        except ValueError as exc:
            raise ValueError("base_url contains an invalid port") from exc
        if parsed.username or parsed.password:
            raise ValueError("base_url must not contain credentials")
        if parsed.query or parsed.fragment:
            raise ValueError("base_url must not contain a query string or fragment")
        return normalized

    @field_validator("model")
    @classmethod
    def normalize_model(cls, value: str) -> str:
        return value.strip()

    @field_validator("api_key_env")
    @classmethod
    def validate_api_key_env(cls, value: str) -> str:
        normalized = value.strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", normalized):
            raise ValueError("api_key_env must be a valid environment variable name")
        return normalized

    @model_validator(mode="after")
    def validate_enabled_model(self) -> LlmConfig:
        if self.enabled and not self.model:
            raise ValueError("model is required when the agent is enabled")
        if self.planning_input_tokens is not None and self.context_window is None:
            raise ValueError("planning_input_tokens requires context_window")
        return self


class ExecutorConfig(StrictModel):
    enabled: bool = True
    backend: str = "pi"
    timeout_seconds: float = Field(default=300.0, ge=1, le=3600)
    context_window: Literal["auto"] | int = "auto"
    reasoning: Literal["off", "auto"] = "off"
    max_output_tokens: int = Field(default=4096, ge=64, le=65536)
    max_tool_calls: int = Field(default=12, ge=1, le=256)
    validation_commands: list[str] = Field(default_factory=list)
    task_validation_commands: list[str] = Field(default_factory=list)
    task_concurrency: int = Field(default=1, ge=1, le=4)

    @field_validator("backend")
    @classmethod
    def validate_backend(cls, value: str) -> str:
        normalized = value.strip().casefold()
        if normalized != "pi":
            raise ValueError("executor backend must be pi")
        return normalized

    @field_validator("context_window")
    @classmethod
    def validate_context_window(
        cls, value: Literal["auto"] | int
    ) -> Literal["auto"] | int:
        if value == "auto":
            return value
        if isinstance(value, bool) or value < 4096:
            raise ValueError("context_window must be auto or at least 4096")
        return value

    @field_validator("validation_commands", "task_validation_commands")
    @classmethod
    def validate_commands(cls, value: list[str]) -> list[str]:
        commands: list[str] = []
        for command in value:
            normalized = command.strip()
            if not normalized or "\n" in normalized or "\r" in normalized:
                raise ValueError("validation commands must be non-empty single lines")
            commands.append(normalized)
        return commands


class FeatureConfig(StrictModel):
    timeout_seconds: float = Field(default=180, ge=1, le=3600)
    checks: dict[str, list[str]] = Field(default_factory=dict)
    task_checks: list[str] | None = None
    final_checks: list[str] | None = None

    @model_validator(mode="after")
    def validate_checks(self):
        for name, commands in self.checks.items():
            if not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", name):
                raise ValueError("Feature check IDs must be simple names")
            self.checks[name] = ExecutorConfig.validate_commands(commands)
        for names in (self.task_checks, self.final_checks):
            if names is not None and set(names) - self.checks.keys():
                raise ValueError("Unknown configured feature check ID")
        return self


class EditingConfig(StrictModel):
    # Quick uses the native Devstill editor; false explicitly selects legacy Quick.
    enabled: bool = True
    # "loop": retrieval-primed tool loop (read/bash/edit/write/done).
    # "structured": the earlier two-response JSON editor with mandatory checks.
    engine: Literal["loop", "structured"] = "loop"
    timeout_seconds: float = Field(default=120, ge=1, le=3600)
    max_output_tokens: int = Field(default=4096, ge=64, le=65536)
    max_context_chars: int = Field(default=48000, ge=1024)
    python: str | None = None
    # Loop engine limits: model calls, output tokens per call, command timeout
    # and the first-turn context budget (smaller context = faster first token).
    max_turns: int = Field(default=10, ge=1, le=50)
    turn_output_tokens: int = Field(default=2048, ge=128, le=65536)
    bash_timeout_seconds: float = Field(default=30, ge=1, le=600)
    loop_context_chars: int = Field(default=16000, ge=2000)


class DevstillConfig(StrictModel):
    version: int = 1
    repo: RepoConfig = Field(default_factory=RepoConfig)
    index: IndexConfig = Field(default_factory=IndexConfig)
    embedding: EmbeddingConfig = Field(default_factory=EmbeddingConfig)
    files: FilesConfig = Field(default_factory=FilesConfig)
    retrieval: RetrievalConfig = Field(default_factory=RetrievalConfig)
    llm: LlmConfig = Field(default_factory=LlmConfig)
    executor: ExecutorConfig = Field(default_factory=ExecutorConfig)
    feature: FeatureConfig = Field(default_factory=FeatureConfig)
    editing: EditingConfig = Field(default_factory=EditingConfig)


def default_model_path() -> Path:
    configured = os.environ.get("DEVSTILL_MODEL_PATH")
    if configured:
        return Path(os.path.expandvars(configured)).expanduser().resolve()
    return (data_home() / "models/jina-embeddings-v2-base-code").resolve()


def find_config(start: Path | None = None) -> Path:
    current = (start or Path.cwd()).expanduser().resolve()
    if current.is_file():
        current = current.parent
    for directory in (current, *current.parents):
        candidate = directory / CONFIG_FILENAME
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(
        f"No {CONFIG_FILENAME} found in {current} or its parents. Run `devstill init`."
    )


def load_config(path: Path) -> DevstillConfig:
    try:
        raw: Any = json.loads(path.read_text(encoding="utf-8"))
        return DevstillConfig.model_validate(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Invalid JSON in {path}: line {exc.lineno}, column {exc.colno}"
        ) from exc
    except ValidationError as exc:
        raise ValueError(f"Invalid devstill configuration:\n{exc}") from exc


def _resolve(base: Path, value: str) -> Path:
    path = Path(os.path.expandvars(value)).expanduser()
    return (path if path.is_absolute() else base / path).resolve()


def resolve_repo_root(config_path: Path, config: DevstillConfig) -> Path:
    return _resolve(config_path.parent, config.repo.root)


def resolve_index_path(config_path: Path, config: DevstillConfig) -> Path:
    return _resolve(config_path.parent, config.index.path)


def resolve_model_path(config: DevstillConfig) -> Path:
    return (
        Path(
            os.path.expandvars(
                os.environ.get("DEVSTILL_MODEL_PATH", config.embedding.model)
            )
        )
        .expanduser()
        .resolve()
    )


def default_config_dict() -> dict[str, Any]:
    return {
        "version": 1,
        "repo": {"root": "."},
        "index": {
            "path": ".devstill/chroma",
            "collection": "devstill_code",
            "max_chunk_chars": 8000,
            "max_chunk_lines": 400,
            "fallback_overlap_lines": 20,
        },
        "embedding": {
            "model": str(default_model_path()),
            "device": "auto",
            "max_sequence_length": 2048,
            "batch_size": 8,
            "normalize_embeddings": True,
        },
        "files": {
            "use_gitignore": True,
            "scan_all_text": True,
            "index_noisy_files": False,
            "include_ignored": [],
            "include": FilesConfig().include,
            "exclude": FilesConfig().exclude,
        },
        "retrieval": {"exclude": []},
        "llm": {
            "enabled": False,
            "base_url": "http://127.0.0.1:8080/v1",
            "model": "",
            "api_key_env": "DEVSTILL_AGENT_API_KEY",
            "timeout_seconds": 300,
            "max_output_tokens": 2048,
            "planning_max_output_tokens": 6144,
            "planning_reasoning": "off",
        },
        "executor": {
            "enabled": True,
            "backend": "pi",
            "timeout_seconds": 300,
            "context_window": "auto",
            "reasoning": "off",
            "max_output_tokens": 4096,
            "max_tool_calls": 12,
            "validation_commands": [],
        },
    }


def write_default_config(destination: Path, force: bool = False) -> Path:
    destination = destination.expanduser().resolve()
    if destination.exists() and not force:
        raise FileExistsError(
            f"{destination} already exists. Use `devstill init --force`."
        )
    destination.write_text(
        json.dumps(default_config_dict(), indent=2) + "\n", encoding="utf-8"
    )
    return destination


def write_config(config_path: Path, config: DevstillConfig) -> Path:
    if config_path.is_symlink():
        raise ValueError(f"Refusing to update symlinked configuration: {config_path}")
    if not config_path.is_file():
        raise FileNotFoundError(config_path)
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    raw["embedding"] = config.embedding.model_dump(mode="json")
    raw["retrieval"] = config.retrieval.model_dump(mode="json")
    raw["llm"] = config.llm.model_dump(mode="json")
    executor = config.executor.model_dump(mode="json")
    if executor["max_output_tokens"] == 49152:
        executor["max_output_tokens"] = 4096
    if executor["max_tool_calls"] == 16:
        executor["max_tool_calls"] = 12
    raw["executor"] = executor
    validated = DevstillConfig.model_validate(raw)
    rendered = (
        json.dumps(
            {
                **raw,
                "embedding": validated.embedding.model_dump(mode="json"),
                "retrieval": validated.retrieval.model_dump(mode="json"),
                "llm": validated.llm.model_dump(mode="json"),
                "executor": validated.executor.model_dump(mode="json"),
            },
            indent=2,
        )
        + "\n"
    )
    mode = stat.S_IMODE(config_path.stat().st_mode)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=config_path.parent,
            prefix=f".{config_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            handle.write(rendered)
            handle.flush()
            os.fsync(handle.fileno())
            temporary = Path(handle.name)
        temporary.chmod(mode)
        temporary.replace(config_path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
    return config_path


def update_llm_config(
    config_path: Path,
    config: DevstillConfig,
    **updates: Any,
) -> DevstillConfig:
    raw = config.model_dump(mode="json")
    raw["llm"] = {**raw["llm"], **updates}
    updated = DevstillConfig.model_validate(raw)
    write_config(config_path, updated)
    return load_config(config_path)


def update_executor_config(
    config_path: Path,
    config: DevstillConfig,
    **updates: Any,
) -> DevstillConfig:
    raw = config.model_dump(mode="json")
    raw["executor"] = {**raw["executor"], **updates}
    updated = DevstillConfig.model_validate(raw)
    write_config(config_path, updated)
    return load_config(config_path)


def update_embedding_config(
    config_path: Path,
    config: DevstillConfig,
    **updates: Any,
) -> DevstillConfig:
    raw = config.model_dump(mode="json")
    raw["embedding"] = {**raw["embedding"], **updates}
    updated = DevstillConfig.model_validate(raw)
    write_config(config_path, updated)
    return updated


def update_retrieval_config(
    config_path: Path,
    config: DevstillConfig,
    *,
    exclude: list[str],
) -> DevstillConfig:
    raw = config.model_dump(mode="json")
    raw["retrieval"] = {"exclude": exclude}
    updated = DevstillConfig.model_validate(raw)
    write_config(config_path, updated)
    return updated
