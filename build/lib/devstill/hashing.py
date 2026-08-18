from __future__ import annotations

import hashlib
from pathlib import Path


def hash_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def hash_text(value: str) -> str:
    return hash_bytes(value.encode("utf-8"))


def hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()
