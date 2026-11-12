from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Self

from devstill.models import FileQuality, FileRecord, ParseError, SearchUnit, SourceChunk

SCHEMA_VERSION = 3


class SQLiteStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.execute("PRAGMA journal_mode = WAL")
        self._create_schema()

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    @contextmanager
    def transaction(self) -> Iterator[None]:
        with self.connection:
            yield

    def _create_schema(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS files (
                path TEXT PRIMARY KEY,
                content_hash TEXT NOT NULL,
                size_bytes INTEGER NOT NULL,
                modified_ns INTEGER NOT NULL,
                language TEXT NOT NULL,
                parser_version TEXT NOT NULL,
                indexed_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS chunks (
                id TEXT PRIMARY KEY,
                path TEXT NOT NULL,
                filename TEXT NOT NULL,
                language TEXT NOT NULL,
                kind TEXT NOT NULL,
                name TEXT,
                qualname TEXT,
                parent_id TEXT,
                start_line INTEGER NOT NULL,
                end_line INTEGER NOT NULL,
                code TEXT NOT NULL,
                leading_comments TEXT,
                content_hash TEXT NOT NULL,
                parser_metadata_json TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS chunks_path_idx ON chunks(path);
            CREATE INDEX IF NOT EXISTS chunks_name_idx ON chunks(name);
            CREATE INDEX IF NOT EXISTS chunks_qualname_idx ON chunks(qualname);
            CREATE TABLE IF NOT EXISTS aliases (
                alias TEXT NOT NULL,
                alias_normalized TEXT NOT NULL,
                chunk_id TEXT NOT NULL,
                FOREIGN KEY(chunk_id) REFERENCES chunks(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS aliases_normalized_idx ON aliases(alias_normalized);
            CREATE TABLE IF NOT EXISTS search_units (
                id TEXT PRIMARY KEY,
                embedding_text TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                summary TEXT,
                concepts_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS search_unit_members (
                search_unit_id TEXT NOT NULL,
                chunk_id TEXT NOT NULL,
                PRIMARY KEY(search_unit_id, chunk_id),
                FOREIGN KEY(search_unit_id) REFERENCES search_units(id) ON DELETE CASCADE,
                FOREIGN KEY(chunk_id) REFERENCES chunks(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS parse_errors (
                path TEXT NOT NULL,
                message TEXT NOT NULL,
                line INTEGER,
                column_number INTEGER
            );
            CREATE INDEX IF NOT EXISTS parse_errors_path_idx ON parse_errors(path);
            CREATE TABLE IF NOT EXISTS file_quality (
                path TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                parser TEXT NOT NULL,
                category TEXT NOT NULL DEFAULT 'unknown_text',
                disposition TEXT NOT NULL DEFAULT 'indexed',
                reasons_json TEXT NOT NULL,
                canonical_chunks INTEGER NOT NULL,
                searchable_chunks INTEGER NOT NULL,
                max_chunk_chars INTEGER NOT NULL,
                max_chunk_lines INTEGER NOT NULL,
                fallback_segments INTEGER NOT NULL,
                noisy INTEGER NOT NULL,
                skipped INTEGER NOT NULL
            );
            CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
                chunk_id UNINDEXED,
                name,
                qualname,
                path,
                code,
                comments,
                tokenize = 'unicode61'
            );
            """
        )
        self._ensure_column(
            "file_quality", "category", "TEXT NOT NULL DEFAULT 'unknown_text'"
        )
        self._ensure_column(
            "file_quality", "disposition", "TEXT NOT NULL DEFAULT 'indexed'"
        )
        self.connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        self.connection.commit()

    def _ensure_column(self, table: str, column: str, declaration: str) -> None:
        columns = {
            row[1]
            for row in self.connection.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if column not in columns:
            self.connection.execute(
                f"ALTER TABLE {table} ADD COLUMN {column} {declaration}"
            )

    def get_files(self) -> dict[str, FileRecord]:
        rows = self.connection.execute("SELECT * FROM files").fetchall()
        return {
            row["path"]: FileRecord(
                path=row["path"],
                content_hash=row["content_hash"],
                size_bytes=row["size_bytes"],
                modified_ns=row["modified_ns"],
                language=row["language"],
                parser_version=row["parser_version"],
            )
            for row in rows
        }

    def upsert_file(self, record: FileRecord) -> None:
        self.connection.execute(
            """
            INSERT INTO files(path, content_hash, size_bytes, modified_ns, language, parser_version, indexed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(path) DO UPDATE SET
                content_hash=excluded.content_hash,
                size_bytes=excluded.size_bytes,
                modified_ns=excluded.modified_ns,
                language=excluded.language,
                parser_version=excluded.parser_version,
                indexed_at=excluded.indexed_at
            """,
            (
                record.path,
                record.content_hash,
                record.size_bytes,
                record.modified_ns,
                record.language,
                record.parser_version,
                datetime.now(UTC).isoformat(),
            ),
        )

    def _delete_chunks_for_path(self, path: str) -> list[str]:
        chunk_ids = [
            row[0]
            for row in self.connection.execute(
                "SELECT id FROM chunks WHERE path = ?", (path,)
            ).fetchall()
        ]
        if not chunk_ids:
            return []
        placeholders = ",".join("?" for _ in chunk_ids)
        unit_ids = [
            row[0]
            for row in self.connection.execute(
                f"SELECT DISTINCT search_unit_id FROM search_unit_members WHERE chunk_id IN ({placeholders})",
                chunk_ids,
            ).fetchall()
        ]
        self.connection.execute(
            f"DELETE FROM search_unit_members WHERE chunk_id IN ({placeholders})",
            chunk_ids,
        )
        self.connection.execute(
            f"DELETE FROM aliases WHERE chunk_id IN ({placeholders})", chunk_ids
        )
        self.connection.execute(
            f"DELETE FROM chunks_fts WHERE chunk_id IN ({placeholders})", chunk_ids
        )
        self.connection.execute("DELETE FROM chunks WHERE path = ?", (path,))
        if unit_ids:
            unit_placeholders = ",".join("?" for _ in unit_ids)
            self.connection.execute(
                f"DELETE FROM search_units WHERE id IN ({unit_placeholders})", unit_ids
            )
        return unit_ids

    def replace_chunks_for_file(
        self,
        path: str,
        chunks: Iterable[SourceChunk],
        search_units: Iterable[SearchUnit],
    ) -> list[str]:
        removed_units = self._delete_chunks_for_path(path)
        chunk_list = list(chunks)
        for chunk in chunk_list:
            self.connection.execute(
                """
                INSERT INTO chunks(
                    id, path, filename, language, kind, name, qualname, parent_id,
                    start_line, end_line, code, leading_comments, content_hash, parser_metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    chunk.id,
                    chunk.path,
                    chunk.filename,
                    chunk.language,
                    chunk.kind,
                    chunk.name,
                    chunk.qualname,
                    chunk.parent_id,
                    chunk.start_line,
                    chunk.end_line,
                    chunk.code,
                    chunk.leading_comments,
                    chunk.content_hash,
                    json.dumps(chunk.parser_metadata, sort_keys=True),
                ),
            )
            if not chunk.parser_metadata.get("searchable", True):
                continue
            aliases = {value for value in (chunk.name, chunk.qualname) if value}
            for alias in aliases:
                self.connection.execute(
                    "INSERT INTO aliases(alias, alias_normalized, chunk_id) VALUES (?, ?, ?)",
                    (alias, alias.casefold(), chunk.id),
                )
            self.connection.execute(
                "INSERT INTO chunks_fts(chunk_id, name, qualname, path, code, comments) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    chunk.id,
                    chunk.name or "",
                    chunk.qualname or "",
                    chunk.path,
                    chunk.code,
                    chunk.leading_comments or "",
                ),
            )
        for unit in search_units:
            self.connection.execute(
                """
                INSERT INTO search_units(id, embedding_text, content_hash, summary, concepts_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    unit.id,
                    unit.embedding_text,
                    unit.content_hash,
                    unit.summary,
                    json.dumps(unit.concepts),
                ),
            )
            self.connection.executemany(
                "INSERT INTO search_unit_members(search_unit_id, chunk_id) VALUES (?, ?)",
                [(unit.id, chunk_id) for chunk_id in unit.source_chunk_ids],
            )
        return removed_units

    def delete_file(self, path: str) -> list[str]:
        unit_ids = self._delete_chunks_for_path(path)
        self.connection.execute("DELETE FROM parse_errors WHERE path = ?", (path,))
        self.connection.execute("DELETE FROM file_quality WHERE path = ?", (path,))
        self.connection.execute("DELETE FROM files WHERE path = ?", (path,))
        return unit_ids

    def upsert_quality(self, quality: FileQuality) -> None:
        self.connection.execute(
            """
            INSERT INTO file_quality(
                path, status, parser, category, disposition, reasons_json, canonical_chunks,
                searchable_chunks, max_chunk_chars, max_chunk_lines,
                fallback_segments, noisy, skipped
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(path) DO UPDATE SET
                status=excluded.status,
                parser=excluded.parser,
                category=excluded.category,
                disposition=excluded.disposition,
                reasons_json=excluded.reasons_json,
                canonical_chunks=excluded.canonical_chunks,
                searchable_chunks=excluded.searchable_chunks,
                max_chunk_chars=excluded.max_chunk_chars,
                max_chunk_lines=excluded.max_chunk_lines,
                fallback_segments=excluded.fallback_segments,
                noisy=excluded.noisy,
                skipped=excluded.skipped
            """,
            (
                quality.path,
                quality.status,
                quality.parser,
                quality.category,
                quality.disposition,
                json.dumps(quality.reasons),
                quality.canonical_chunks,
                quality.searchable_chunks,
                quality.max_chunk_chars,
                quality.max_chunk_lines,
                quality.fallback_segments,
                int(quality.noisy),
                int(quality.skipped),
            ),
        )

    def list_quality(self) -> list[FileQuality]:
        rows = self.connection.execute(
            "SELECT * FROM file_quality ORDER BY path"
        ).fetchall()
        return [
            FileQuality(
                path=row["path"],
                status=row["status"],
                parser=row["parser"],
                category=row["category"],
                disposition=row["disposition"],
                reasons=json.loads(row["reasons_json"]),
                canonical_chunks=row["canonical_chunks"],
                searchable_chunks=row["searchable_chunks"],
                max_chunk_chars=row["max_chunk_chars"],
                max_chunk_lines=row["max_chunk_lines"],
                fallback_segments=row["fallback_segments"],
                noisy=bool(row["noisy"]),
                skipped=bool(row["skipped"]),
            )
            for row in rows
        ]

    def replace_parse_errors(self, path: str, errors: Iterable[ParseError]) -> None:
        self.connection.execute("DELETE FROM parse_errors WHERE path = ?", (path,))
        self.connection.executemany(
            "INSERT INTO parse_errors(path, message, line, column_number) VALUES (?, ?, ?, ?)",
            [(error.path, error.message, error.line, error.column) for error in errors],
        )

    def list_parse_errors(self) -> list[ParseError]:
        rows = self.connection.execute(
            "SELECT path, message, line, column_number FROM parse_errors ORDER BY path, line"
        ).fetchall()
        return [
            ParseError(
                path=row["path"],
                message=row["message"],
                line=row["line"],
                column=row["column_number"],
            )
            for row in rows
        ]

    def get_chunk_by_id(self, chunk_id: str) -> SourceChunk | None:
        row = self.connection.execute(
            "SELECT * FROM chunks WHERE id = ?", (chunk_id,)
        ).fetchone()
        return self._chunk(row) if row else None

    def get_chunks_by_ids(self, chunk_ids: Iterable[str]) -> dict[str, SourceChunk]:
        values = list(dict.fromkeys(chunk_ids))
        if not values:
            return {}
        rows = self.connection.execute(
            "SELECT * FROM chunks WHERE id IN (SELECT value FROM json_each(?))",
            (json.dumps(values),),
        ).fetchall()
        return {row["id"]: self._chunk(row) for row in rows}

    def list_chunks_for_file(self, path: str) -> list[SourceChunk]:
        rows = self.connection.execute(
            "SELECT * FROM chunks WHERE path = ? ORDER BY start_line, end_line", (path,)
        ).fetchall()
        return [self._chunk(row) for row in rows]

    def list_symbol_chunks(self) -> list[SourceChunk]:
        rows = self.connection.execute(
            """
            SELECT * FROM chunks
            WHERE name IS NOT NULL
              AND coalesce(json_extract(parser_metadata_json, '$.searchable'), 1) != 0
            """
        ).fetchall()
        return [self._chunk(row) for row in rows]

    def retrieval_paths(self) -> list[str]:
        return [
            row[0]
            for row in self.connection.execute("SELECT DISTINCT path FROM chunks")
        ]

    def eligible_chunk_ids(
        self, *, allowed_paths: list[str] | None = None, kind: str | None = None
    ) -> set[str]:
        scope, params = self._scope(allowed_paths, kind)
        return {
            row[0]
            for row in self.connection.execute(
                f"SELECT id FROM chunks WHERE 1=1 {scope}", params
            )
        }

    @staticmethod
    def _scope(allowed_paths: list[str] | None, kind: str | None) -> tuple[str, list]:
        clauses, params = [], []
        if allowed_paths is not None:
            clauses.append("chunks.path IN (SELECT value FROM json_each(?))")
            params.append(json.dumps(allowed_paths))
        if kind:
            clauses.append("chunks.kind = ?")
            params.append(kind)
        return (" AND " + " AND ".join(clauses) if clauses else ""), params

    def find_symbol_exact(
        self,
        query: str,
        limit: int = 50,
        *,
        allowed_paths: list[str] | None = None,
        kind: str | None = None,
    ) -> list[str]:
        scope, params = self._scope(allowed_paths, kind)
        rows = self.connection.execute(
            f"""
            SELECT DISTINCT aliases.chunk_id
            FROM aliases
            JOIN chunks ON chunks.id = aliases.chunk_id
            WHERE aliases.alias_normalized = ?
              AND coalesce(json_extract(chunks.parser_metadata_json, '$.searchable'), 1) != 0
              {scope}
            ORDER BY chunks.path, chunks.start_line, chunks.id
            LIMIT ?
            """,
            (query.casefold(), *params, limit),
        ).fetchall()
        return [row[0] for row in rows]

    def find_symbol_prefix(
        self,
        query: str,
        limit: int = 50,
        *,
        allowed_paths: list[str] | None = None,
        kind: str | None = None,
    ) -> list[str]:
        scope, params = self._scope(allowed_paths, kind)
        escaped = (
            query.casefold()
            .replace("\\", "\\\\")
            .replace("%", "\\%")
            .replace("_", "\\_")
        )
        rows = self.connection.execute(
            f"""
            SELECT DISTINCT aliases.chunk_id
            FROM aliases
            JOIN chunks ON chunks.id = aliases.chunk_id
            WHERE aliases.alias_normalized LIKE ? ESCAPE '\\'
              AND coalesce(json_extract(chunks.parser_metadata_json, '$.searchable'), 1) != 0
              {scope}
            ORDER BY length(aliases.alias_normalized), aliases.alias_normalized
            LIMIT ?
            """,
            (f"{escaped}%", *params, limit),
        ).fetchall()
        return [row[0] for row in rows]

    def search_paths(
        self,
        query: str,
        limit: int = 100,
        *,
        allowed_paths: list[str] | None = None,
        kind: str | None = None,
    ) -> list[str]:
        scope, params = self._scope(allowed_paths, kind)
        rows = self.connection.execute(
            f"""
            SELECT id FROM chunks
            WHERE lower(path) LIKE ?
              AND coalesce(json_extract(parser_metadata_json, '$.searchable'), 1) != 0
              {scope}
            ORDER BY length(path), start_line
            LIMIT ?
            """,
            (f"%{query.casefold()}%", *params, limit),
        ).fetchall()
        return [row[0] for row in rows]

    def search_fts(
        self,
        query: str,
        limit: int = 100,
        *,
        allowed_paths: list[str] | None = None,
        kind: str | None = None,
    ) -> list[tuple[str, float]]:
        scope, params = self._scope(allowed_paths, kind)
        tokens = re.findall(r"[\w.:-]+", query, flags=re.UNICODE)
        if not tokens:
            return []
        expression = " OR ".join(
            f'"{token.replace(chr(34), chr(34) * 2)}"' for token in tokens
        )
        rows = self.connection.execute(
            f"""
            SELECT chunks_fts.chunk_id,
                   bm25(chunks_fts, 0.0, 4.0, 3.0, 2.0, 1.0, 1.0) AS rank
            FROM chunks_fts
            JOIN chunks ON chunks.id = chunks_fts.chunk_id
            WHERE chunks_fts MATCH ?
              AND coalesce(json_extract(chunks.parser_metadata_json, '$.searchable'), 1) != 0
              {scope}
            ORDER BY rank
            LIMIT ?
            """,
            (expression, *params, limit),
        ).fetchall()
        return [(row["chunk_id"], float(row["rank"])) for row in rows]

    def count_chunks(self) -> int:
        return int(self.connection.execute("SELECT count(*) FROM chunks").fetchone()[0])

    def reset_all(self) -> None:
        self.connection.execute("DELETE FROM search_unit_members")
        self.connection.execute("DELETE FROM search_units")
        self.connection.execute("DELETE FROM parse_errors")
        self.connection.execute("DELETE FROM file_quality")
        self.connection.execute("DELETE FROM aliases")
        self.connection.execute("DELETE FROM chunks_fts")
        self.connection.execute("DELETE FROM chunks")
        self.connection.execute("DELETE FROM files")

    def all_search_units(self) -> list[SearchUnit]:
        rows = self.connection.execute("SELECT * FROM search_units").fetchall()
        units: list[SearchUnit] = []
        for row in rows:
            members = self.connection.execute(
                "SELECT chunk_id FROM search_unit_members WHERE search_unit_id = ?",
                (row["id"],),
            ).fetchall()
            units.append(
                SearchUnit(
                    id=row["id"],
                    source_chunk_ids=[value[0] for value in members],
                    embedding_text=row["embedding_text"],
                    summary=row["summary"],
                    concepts=json.loads(row["concepts_json"]),
                    content_hash=row["content_hash"],
                )
            )
        return units

    def _chunk(self, row: sqlite3.Row) -> SourceChunk:
        return SourceChunk(
            id=row["id"],
            path=row["path"],
            filename=row["filename"],
            language=row["language"],
            kind=row["kind"],
            name=row["name"],
            qualname=row["qualname"],
            parent_id=row["parent_id"],
            start_line=row["start_line"],
            end_line=row["end_line"],
            code=row["code"],
            leading_comments=row["leading_comments"],
            content_hash=row["content_hash"],
            parser_metadata=json.loads(row["parser_metadata_json"]),
        )
