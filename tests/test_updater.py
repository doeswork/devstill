from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace

import pytest

from devstill import updater
from devstill.installation import InstallMetadata
from devstill.updater import Release


class FakeResponse:
    def __init__(self, payload: object) -> None:
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> object:
        return self.payload


class FakeClient:
    def __init__(self, payload: object) -> None:
        self.payload = payload

    def get(self, url: str) -> FakeResponse:
        assert url == updater.LATEST_RELEASE_URL
        return FakeResponse(self.payload)


def install_metadata(tmp_path) -> InstallMetadata:
    return InstallMetadata(
        version="0.1.0",
        requested_device="auto",
        resolved_device="cuda",
        torch_index_url="https://download.pytorch.org/whl/cu128",
        venv=str(tmp_path),
    )


def test_latest_release_accepts_only_stable_trusted_github_data() -> None:
    release = updater.fetch_latest_release(
        FakeClient(
            {
                "tag_name": "v1.2.3",
                "tarball_url": (
                    "https://api.github.com/repos/doeswork/devstill/tarball/v1.2.3"
                ),
                "draft": False,
                "prerelease": False,
            }
        )
    )
    assert release == Release("1.2.3", "v1.2.3", release.tarball_url)
    assert updater.update_available("1.2.2", release)
    assert not updater.update_available("1.2.3", release)

    with pytest.raises(RuntimeError, match="stable"):
        updater.fetch_latest_release(
            FakeClient(
                {
                    "tag_name": "v1.3.0rc1",
                    "tarball_url": (
                        "https://api.github.com/repos/doeswork/devstill/tarball/test"
                    ),
                    "prerelease": True,
                }
            )
        )


def test_editable_install_detection(monkeypatch) -> None:
    distribution = SimpleNamespace(
        read_text=lambda _name: json.dumps({"dir_info": {"editable": True}})
    )
    monkeypatch.setattr(updater.importlib.metadata, "distribution", lambda _n: distribution)
    assert updater.is_editable_install()


def test_install_release_preserves_dependencies_and_runs_checks(monkeypatch, tmp_path) -> None:
    metadata = install_metadata(tmp_path)
    release = Release(
        "1.2.3",
        "v1.2.3",
        "https://api.github.com/repos/doeswork/devstill/tarball/v1.2.3",
    )
    monkeypatch.setattr(updater, "validate_self_contained_install", lambda _m: None)
    saved: list[InstallMetadata] = []
    monkeypatch.setattr(updater, "write_install_metadata", saved.append)
    calls: list[list[str]] = []

    def run(args, **_kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, "", "")

    updater.install_release(release, metadata, runner=run)

    assert "--upgrade-strategy" in calls[0]
    assert "--no-cache-dir" in calls[0]
    assert "only-if-needed" in calls[0]
    assert calls[1][-2:] == ["pip", "check"]
    assert calls[2][-2:] == ["devstill.cli", "doctor"]
    assert saved[0].version == "1.2.3"
    assert saved[0].torch_index_url == metadata.torch_index_url


def test_failed_update_does_not_write_new_metadata(monkeypatch, tmp_path) -> None:
    metadata = install_metadata(tmp_path)
    release = Release("1.0.0", "v1.0.0", "https://api.github.com/repos/doeswork/devstill/tarball/v1")
    monkeypatch.setattr(updater, "validate_self_contained_install", lambda _m: None)
    monkeypatch.setattr(
        updater,
        "write_install_metadata",
        lambda _m: pytest.fail("metadata should not be written"),
    )

    def fail(args, **_kwargs):
        return subprocess.CompletedProcess(args, 1, "", "failed")

    with pytest.raises(RuntimeError, match="upgrade failed"):
        updater.install_release(release, metadata, runner=fail)
