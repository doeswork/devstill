from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

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
    device: str = "cpu"
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


class LlmConfig(StrictModel):
    enabled: bool = False
    base_url: str = "http://127.0.0.1:8080/v1"
    model: str = ""


class DevstillConfig(StrictModel):
    version: int = 1
    repo: RepoConfig = Field(default_factory=RepoConfig)
    index: IndexConfig = Field(default_factory=IndexConfig)
    embedding: EmbeddingConfig = Field(default_factory=EmbeddingConfig)
    files: FilesConfig = Field(default_factory=FilesConfig)
    llm: LlmConfig = Field(default_factory=LlmConfig)


def default_model_path() -> Path:
    configured = os.environ.get("DEVSTILL_MODEL_PATH")
    if configured:
        return Path(os.path.expandvars(configured)).expanduser().resolve()
    return (
        Path.home() / ".local/share/devstill/models/jina-embeddings-v2-base-code"
    ).resolve()


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
            "device": "cpu",
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
        "llm": {"enabled": False, "base_url": "http://127.0.0.1:8080/v1", "model": ""},
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
