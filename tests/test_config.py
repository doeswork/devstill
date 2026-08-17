from __future__ import annotations

import json

import pytest

from devstill.config import find_config, load_config, write_default_config


def test_config_upward_discovery_and_strict_validation(tmp_path) -> None:
    config_path = write_default_config(tmp_path / "devstill.json")
    nested = tmp_path / "src" / "feature"
    nested.mkdir(parents=True)
    assert find_config(nested) == config_path
    config = load_config(config_path)
    assert config.embedding.device == "auto"
    assert config.index.path == ".devstill/chroma"
    assert config.index.max_chunk_chars == 8000
    assert config.index.max_chunk_lines == 400
    assert config.index.fallback_overlap_lines == 20
    assert config.files.scan_all_text is True
    assert config.files.index_noisy_files is False
    assert config.files.include_ignored == []
    assert config.llm.api_key_env == "DEVSTILL_AGENT_API_KEY"
    assert config.llm.timeout_seconds == 300
    assert config.llm.max_output_tokens == 2048
    assert config.llm.planning_max_output_tokens == 6144
    assert config.llm.planning_reasoning == "off"
    assert config.executor.enabled is True
    assert config.executor.backend == "pi"
    assert config.executor.timeout_seconds == 300
    assert config.executor.max_output_tokens == 49152
    assert config.executor.max_tool_calls == 16
    assert config.executor.validation_commands == []

    raw = json.loads(config_path.read_text(encoding="utf-8"))
    raw["unexpected"] = True
    config_path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError, match="Invalid devstill configuration"):
        load_config(config_path)


def test_legacy_explicit_cpu_device_is_preserved(tmp_path) -> None:
    config_path = write_default_config(tmp_path / "devstill.json")
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    raw["embedding"]["device"] = "cpu"
    config_path.write_text(json.dumps(raw), encoding="utf-8")

    assert load_config(config_path).embedding.device == "cpu"


def test_embedding_device_is_strictly_validated(tmp_path) -> None:
    config_path = write_default_config(tmp_path / "devstill.json")
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    raw["embedding"]["device"] = "metal"
    config_path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(ValueError, match="embedding.device"):
        load_config(config_path)


def test_chunk_overlap_must_be_smaller_than_line_limit(tmp_path) -> None:
    config_path = tmp_path / "devstill.json"
    config_path.write_text(
        json.dumps(
            {
                "index": {
                    "max_chunk_lines": 20,
                    "fallback_overlap_lines": 20,
                }
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="fallback_overlap_lines"):
        load_config(config_path)
