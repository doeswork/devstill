from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from devstill.config import DevstillConfig
from devstill.parsers.detection import is_binary, noisy_reason
from devstill.parsers.registry import FALLBACK_SPEC, ParserSpec, parser_spec_for

CONTENT_POLICY_VERSION = "content-policy:1"

_ASSET_EXTENSIONS = frozenset(
    {
        ".avif",
        ".bmp",
        ".gif",
        ".heic",
        ".heif",
        ".ico",
        ".jpeg",
        ".jpg",
        ".png",
        ".psd",
        ".ai",
        ".eps",
        ".pdf",
        ".svg",
        ".svgz",
        ".tif",
        ".tiff",
        ".webp",
    }
)
_FONT_EXTENSIONS = frozenset({".eot", ".otf", ".ttf", ".woff", ".woff2"})
_MEDIA_EXTENSIONS = frozenset(
    {
        ".aac",
        ".avi",
        ".flac",
        ".m4a",
        ".m4v",
        ".mkv",
        ".mov",
        ".mp3",
        ".mp4",
        ".mpeg",
        ".mpg",
        ".ogg",
        ".ogv",
        ".wav",
        ".webm",
    }
)
_ARCHIVE_EXTENSIONS = frozenset(
    {
        ".7z",
        ".bz2",
        ".gz",
        ".rar",
        ".tar",
        ".tgz",
        ".xz",
        ".zip",
        ".apk",
        ".deb",
        ".ipa",
        ".rpm",
        ".whl",
    }
)
_COMPILED_EXTENSIONS = frozenset(
    {
        ".a",
        ".class",
        ".dll",
        ".dylib",
        ".ear",
        ".exe",
        ".jar",
        ".lib",
        ".o",
        ".obj",
        ".pyc",
        ".pyd",
        ".so",
        ".war",
        ".wasm",
    }
)
_DATABASE_EXTENSIONS = frozenset({".db", ".db3", ".sqlite", ".sqlite3"})
_SECRET_EXTENSIONS = frozenset(
    {".cer", ".crt", ".der", ".jks", ".key", ".keystore", ".p12", ".pem", ".pfx"}
)
_ENV_TEMPLATES = frozenset({".env.example", ".env.sample", ".env.template"})


@dataclass(frozen=True, slots=True)
class ContentDecision:
    category: str
    disposition: str
    reason: str | None
    parser_spec: ParserSpec
    noisy: bool = False
    overridden: bool = False


def matches_patterns(path: str, patterns: list[str]) -> bool:
    candidate = PurePosixPath(path)
    return any(
        candidate.match(pattern)
        or fnmatch.fnmatchcase(path, pattern)
        or (pattern.startswith("**/") and fnmatch.fnmatchcase(path, pattern[3:]))
        for pattern in patterns
    )


def _path_policy(path: Path, relative: str) -> tuple[str, str] | None:
    suffix = path.suffix.casefold()
    name = path.name.casefold()
    if name == "devstill.json":
        return "metadata", "Devstill repository configuration"
    if suffix in _ASSET_EXTENSIONS:
        return "asset", f"image/vector asset ({suffix})"
    if suffix in _FONT_EXTENSIONS:
        return "asset", f"font asset ({suffix})"
    if suffix in _MEDIA_EXTENSIONS:
        return "asset", f"audio/video asset ({suffix})"
    if suffix in _ARCHIVE_EXTENSIONS:
        return "asset", f"archive ({suffix})"
    if suffix in _COMPILED_EXTENSIONS:
        return "asset", f"compiled artifact ({suffix})"
    if suffix in _DATABASE_EXTENSIONS:
        return "asset", f"database file ({suffix})"
    if suffix in _SECRET_EXTENSIONS:
        return "sensitive", f"key or certificate material ({suffix})"
    env_template = name in _ENV_TEMPLATES or name.endswith(
        (".example", ".sample", ".template")
    )
    if name == ".env" or (name.startswith(".env.") and not env_template):
        return "sensitive", "environment file may contain secrets"
    if any(part == ".devstill" for part in PurePosixPath(relative).parts):
        return "metadata", "Devstill index artifact"
    return None


def classify_content(
    path: Path,
    relative: str,
    raw: bytes,
    config: DevstillConfig,
) -> ContentDecision:
    override = matches_patterns(relative, config.files.include_ignored)
    path_policy = _path_policy(path, relative)
    if path_policy and not override:
        category, reason = path_policy
        return ContentDecision(category, "ignored", reason, FALLBACK_SPEC)

    if is_binary(raw):
        return ContentDecision("binary", "skipped", "binary content", FALLBACK_SPEC)
    if not raw.strip():
        spec = parser_spec_for(path)
        return ContentDecision("empty", "skipped", "empty file", spec)

    noisy = noisy_reason(path, relative, raw)
    noisy_override = bool(noisy) and (override or config.files.index_noisy_files)
    if noisy and not noisy_override:
        return ContentDecision("generated", "ignored", noisy, FALLBACK_SPEC, noisy=True)
    if noisy_override:
        return ContentDecision(
            "unknown_text",
            "indexed",
            f"noisy file indexed by override: {noisy}",
            FALLBACK_SPEC,
            noisy=True,
            overridden=True,
        )

    spec = parser_spec_for(path)
    category = spec.category
    reason = None
    if path_policy and override:
        category = "unknown_text" if spec is FALLBACK_SPEC else spec.category
        reason = f"built-in ignore overridden: {path_policy[1]}"
    return ContentDecision(
        category,
        "indexed",
        reason,
        spec,
        overridden=bool(path_policy and override),
    )
