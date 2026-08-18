from __future__ import annotations

import json
import os
import stat
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


def data_home() -> Path:
    configured = os.environ.get("DEVSTILL_DATA_HOME")
    if configured:
        return Path(os.path.expandvars(configured)).expanduser().resolve()
    base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
    return (base.expanduser().resolve() / "devstill").resolve()


def install_metadata_path() -> Path:
    configured = os.environ.get("DEVSTILL_INSTALL_METADATA")
    if configured:
        return Path(os.path.expandvars(configured)).expanduser().resolve()
    return data_home() / "install.json"


@dataclass(frozen=True, slots=True)
class InstallMetadata:
    version: str
    requested_device: str
    resolved_device: str
    torch_index_url: str
    venv: str
    install_mode: str = "self-contained"
    schema_version: int = 1

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> InstallMetadata:
        try:
            metadata = cls(
                schema_version=int(value.get("schema_version", 1)),
                install_mode=str(value["install_mode"]),
                requested_device=str(value["requested_device"]),
                resolved_device=str(value["resolved_device"]),
                torch_index_url=str(value["torch_index_url"]),
                version=str(value["version"]),
                venv=str(value["venv"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("Devstill installation metadata is incomplete") from exc
        if metadata.schema_version != 1:
            raise ValueError(
                f"Unsupported installation metadata version {metadata.schema_version}"
            )
        if metadata.install_mode != "self-contained":
            raise ValueError(
                f"Unsupported Devstill install mode: {metadata.install_mode}"
            )
        return metadata


def load_install_metadata(path: Path | None = None) -> InstallMetadata:
    target = (path or install_metadata_path()).expanduser().resolve()
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise FileNotFoundError(
            f"No self-contained installation metadata found at {target}. "
            "Re-run `./setup.sh`."
        ) from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid Devstill installation metadata at {target}") from exc
    if not isinstance(raw, dict):
        raise TypeError(f"Invalid Devstill installation metadata at {target}")
    return InstallMetadata.from_dict(raw)


def write_install_metadata(
    metadata: InstallMetadata,
    path: Path | None = None,
) -> Path:
    target = (path or install_metadata_path()).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(asdict(metadata), indent=2, sort_keys=True) + "\n"
    mode = 0o600
    if target.exists() and not target.is_symlink():
        mode = stat.S_IMODE(target.stat().st_mode)
    if target.is_symlink():
        raise ValueError(f"Refusing to update symlinked metadata: {target}")
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=target.parent,
            prefix=f".{target.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            handle.write(rendered)
            handle.flush()
            os.fsync(handle.fileno())
            temporary = Path(handle.name)
        temporary.chmod(mode)
        temporary.replace(target)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
    return target
