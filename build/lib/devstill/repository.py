from __future__ import annotations

from pathlib import Path

MANAGED_GITIGNORE_COMMENT = "# devstill local index"
MANAGED_GITIGNORE_ENTRY = ".devstill/"
MANAGED_GITIGNORE_BLOCK = f"{MANAGED_GITIGNORE_COMMENT}\n{MANAGED_GITIGNORE_ENTRY}\n"


def has_managed_gitignore_block(path: Path) -> bool:
    if not path.is_file():
        return False
    lines = path.read_text(encoding="utf-8").splitlines()
    return any(
        line == MANAGED_GITIGNORE_COMMENT
        and index + 1 < len(lines)
        and lines[index + 1] == MANAGED_GITIGNORE_ENTRY
        for index, line in enumerate(lines)
    )


def ensure_devstill_gitignore(path: Path) -> bool:
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    if MANAGED_GITIGNORE_ENTRY in lines:
        return False
    with path.open("a", encoding="utf-8") as handle:
        if lines:
            handle.write("\n")
        handle.write(MANAGED_GITIGNORE_BLOCK)
    return True


def remove_managed_gitignore_block(path: Path) -> bool:
    if not path.is_file():
        return False
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    changed = False
    index = 0
    while index + 1 < len(lines):
        current = lines[index].rstrip("\r\n")
        following = lines[index + 1].rstrip("\r\n")
        if (
            current == MANAGED_GITIGNORE_COMMENT
            and following == MANAGED_GITIGNORE_ENTRY
        ):
            start = index
            if start > 0 and not lines[start - 1].rstrip("\r\n"):
                start -= 1
            del lines[start : index + 2]
            changed = True
            index = max(0, start - 1)
            continue
        index += 1
    if changed:
        path.write_text("".join(lines), encoding="utf-8")
    return changed
