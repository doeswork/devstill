from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from devstill.context import build_search_unit
from devstill.models import FileRecord, SourceChunk
from devstill.storage.sqlite_store import SQLiteStore


def sample_chunk(code: str = "def authenticate():\n    return True") -> SourceChunk:
    return SourceChunk(
        id="auth.py::authenticate::function",
        path="auth.py",
        filename="auth.py",
        language="python",
        kind="function",
        name="authenticate",
        qualname="authenticate",
        parent_id="auth.py::module",
        start_line=1,
        end_line=2,
        code=code,
        leading_comments="# Verify credentials",
        content_hash="abc",
        parser_metadata={},
    )


def test_storage_exact_prefix_fts_replace_and_delete(tmp_path: Path) -> None:
    with SQLiteStore(tmp_path / "metadata.sqlite") as store:
        chunk = sample_chunk()
        unit = build_search_unit(chunk)
        with store.transaction():
            store.upsert_file(FileRecord("auth.py", "file1", 10, 1, "python", "1"))
            store.replace_chunks_for_file("auth.py", [chunk], [unit])
        assert store.find_symbol_exact("AUTHENTICATE") == [chunk.id]
        assert store.find_symbol_prefix("auth") == [chunk.id]
        assert store.search_fts("credentials")[0][0] == chunk.id

        changed = sample_chunk("def authenticate():\n    return False")
        changed.content_hash = "changed"
        with store.transaction():
            store.replace_chunks_for_file(
                "auth.py", [changed], [build_search_unit(changed)]
            )
        assert store.get_chunk_by_id(chunk.id).code.endswith("False")
        with store.transaction():
            removed = store.delete_file("auth.py")
        assert removed
        assert store.get_chunk_by_id(chunk.id) is None


def test_non_searchable_modules_are_canonical_but_not_retrievable(
    tmp_path: Path,
) -> None:
    with SQLiteStore(tmp_path / "metadata.sqlite") as store:
        function = sample_chunk()
        module = replace(
            function,
            id="auth.py::auth::module",
            kind="module",
            name="auth",
            qualname="auth",
            parent_id=None,
            code="legacy module marker\ndef authenticate():\n    return True",
            parser_metadata={"searchable": False},
        )
        with store.transaction():
            store.replace_chunks_for_file(
                "auth.py", [module, function], [build_search_unit(function)]
            )
            # Simulate module rows written by an older Devstill version.
            store.connection.execute(
                "INSERT INTO aliases(alias, alias_normalized, chunk_id) VALUES (?, ?, ?)",
                ("auth", "auth", module.id),
            )
            store.connection.execute(
                """
                INSERT INTO chunks_fts(
                    chunk_id, name, qualname, path, code, comments
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    module.id,
                    module.name,
                    module.qualname,
                    module.path,
                    module.code,
                    "",
                ),
            )

        assert store.get_chunk_by_id(module.id) is not None
        assert module.id not in store.find_symbol_exact("auth")
        assert module.id not in store.find_symbol_prefix("auth")
        assert module.id not in store.search_paths("auth.py")
        assert module.id not in {chunk_id for chunk_id, _ in store.search_fts("legacy")}
