from __future__ import annotations

import importlib.metadata
import json
import subprocess
import sys
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import httpx
from packaging.version import InvalidVersion, Version

from devstill.installation import (
    InstallMetadata,
    load_install_metadata,
    write_install_metadata,
)

LATEST_RELEASE_URL = "https://api.github.com/repos/doeswork/devstill/releases/latest"


@dataclass(frozen=True, slots=True)
class Release:
    version: str
    tag: str
    tarball_url: str


def _normalized_version(value: str) -> Version:
    candidate = value.strip().removeprefix("v")
    try:
        return Version(candidate)
    except InvalidVersion as exc:
        raise ValueError(f"Invalid Devstill release version: {value}") from exc


def fetch_latest_release(client: Any | None = None) -> Release:
    owns_client = client is None
    http = client or httpx.Client(
        timeout=20.0,
        follow_redirects=True,
        headers={"Accept": "application/vnd.github+json"},
    )
    try:
        response = http.get(LATEST_RELEASE_URL)
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Could not check GitHub Releases: {exc}") from exc
    finally:
        if owns_client:
            http.close()
    if not isinstance(payload, dict):
        raise TypeError("GitHub returned an invalid release response")
    if payload.get("draft") or payload.get("prerelease"):
        raise RuntimeError("GitHub's latest release is not a stable release")
    tag = str(payload.get("tag_name", "")).strip()
    tarball_url = str(payload.get("tarball_url", "")).strip()
    if not tag or not tarball_url.startswith("https://api.github.com/repos/doeswork/devstill/"):
        raise RuntimeError("GitHub's latest release is missing trusted release data")
    version = str(_normalized_version(tag))
    return Release(version=version, tag=tag, tarball_url=tarball_url)


def is_editable_install() -> bool:
    try:
        direct_url = importlib.metadata.distribution("devstill").read_text(
            "direct_url.json"
        )
    except importlib.metadata.PackageNotFoundError:
        return False
    if not direct_url:
        return False
    try:
        payload = json.loads(direct_url)
    except json.JSONDecodeError:
        return False
    if not isinstance(payload, dict):
        return False
    directory = payload.get("dir_info", {})
    return isinstance(directory, dict) and bool(directory.get("editable"))


def validate_self_contained_install(metadata: InstallMetadata) -> None:
    if is_editable_install():
        raise ValueError(
            "`devstill update` cannot update an editable development checkout. "
            "Use `git pull` and reinstall the development environment instead."
        )
    expected = Path(metadata.venv).expanduser().resolve()
    executable = Path(sys.executable).resolve()
    if not executable.is_relative_to(expected):
        raise ValueError(
            "The running command is not from Devstill's self-contained environment. "
            "Run the ~/.local/bin/devstill command or re-run `./setup.sh`."
        )


def update_available(current: str, release: Release) -> bool:
    return _normalized_version(release.version) > _normalized_version(current)


def install_release(
    release: Release,
    metadata: InstallMetadata,
    *,
    runner: Any = subprocess.run,
) -> None:
    validate_self_contained_install(metadata)
    install = runner(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--no-cache-dir",
            "--upgrade",
            "--upgrade-strategy",
            "only-if-needed",
            release.tarball_url,
        ],
        check=False,
        text=True,
    )
    if install.returncode != 0:
        raise RuntimeError(
            "The Devstill package upgrade failed. The existing command was left in "
            "place; re-run `./setup.sh` if it no longer starts."
        )
    check = runner(
        [sys.executable, "-m", "pip", "check"],
        check=False,
        text=True,
        capture_output=True,
    )
    if check.returncode != 0:
        detail = (check.stdout or check.stderr or "dependency check failed").strip()
        raise RuntimeError(
            f"Devstill updated, but package integrity failed: {detail}. "
            "Re-run `./setup.sh` to repair the installation."
        )
    health = runner(
        [sys.executable, "-m", "devstill.cli", "doctor"],
        check=False,
        text=True,
        cwd=str(Path(metadata.venv).expanduser().resolve()),
    )
    if health.returncode != 0:
        raise RuntimeError(
            "Devstill updated, but its health check failed. Run `devstill doctor` "
            "for details or re-run `./setup.sh`."
        )
    write_install_metadata(replace(metadata, version=release.version))


def installed_metadata() -> InstallMetadata:
    metadata = load_install_metadata()
    validate_self_contained_install(metadata)
    return metadata
