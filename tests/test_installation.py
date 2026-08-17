from __future__ import annotations

import json

import pytest

from devstill.installation import (
    InstallMetadata,
    load_install_metadata,
    write_install_metadata,
)


def metadata(tmp_path) -> InstallMetadata:
    return InstallMetadata(
        version="0.1.0",
        requested_device="auto",
        resolved_device="cuda",
        torch_index_url="https://download.pytorch.org/whl/cu128",
        venv=str(tmp_path / "venv"),
    )


def test_install_metadata_round_trip_is_private(tmp_path) -> None:
    target = tmp_path / "install.json"
    write_install_metadata(metadata(tmp_path), target)

    assert load_install_metadata(target) == metadata(tmp_path)
    assert target.stat().st_mode & 0o777 == 0o600


def test_install_metadata_rejects_missing_or_unknown_schema(tmp_path) -> None:
    with pytest.raises(FileNotFoundError, match="setup.sh"):
        load_install_metadata(tmp_path / "missing.json")
    target = tmp_path / "install.json"
    target.write_text(json.dumps({"schema_version": 99}), encoding="utf-8")
    with pytest.raises(ValueError, match="incomplete"):
        load_install_metadata(target)
