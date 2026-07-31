from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from devstill.config import (
    DevstillConfig,
    resolve_index_path,
    resolve_model_path,
    resolve_repo_root,
)
from devstill.context import build_search_unit
from devstill.embeddings import EmbeddingBackend
from devstill.git_files import discover_repository_files
from devstill.hashing import hash_file
from devstill.models import (
    FileRecord,
    IndexSummary,
    ParseError,
    SearchUnit,
    SourceChunk,
)
from devstill.parsers import parse_source_file, parser_version_for
from devstill.paths import IndexPaths, relative_repo_path
from devstill.storage import ChromaStore, SQLiteStore


class RepositoryIndexer:
    def __init__(
        self,
        config_path: Path,
        config: DevstillConfig,
        embedder: Any | None = None,
        chroma_store: Any | None = None,
    ) -> None:
        self.config_path = config_path
        self.config = config
        self.repo_root = resolve_repo_root(config_path, config)
        self.paths = IndexPaths.from_chroma_path(
            resolve_index_path(config_path, config)
        )
        self.paths.create()
        self.store = SQLiteStore(self.paths.sqlite)
        self.embedder = embedder or EmbeddingBackend(
            resolve_model_path(config),
            max_sequence_length=config.embedding.max_sequence_length,
            batch_size=config.embedding.batch_size,
            normalize=config.embedding.normalize_embeddings,
        )
        self.chroma = chroma_store or ChromaStore(
            self.paths.chroma,
            config.index.collection,
        )

    def close(self) -> None:
        self.store.close()

    def _reconcile_vectors(self, verbose: bool) -> None:
        units = self.store.all_search_units()
        if verbose:
            print(
                f"Reconciling {len(units)} vector records after interrupted indexing.",
                file=sys.stderr,
            )
        self.chroma.reset_collection()
        if not units:
            return
        chunk_ids = [chunk_id for unit in units for chunk_id in unit.source_chunk_ids]
        chunks = self.store.get_chunks_by_ids(chunk_ids)
        embeddings = self.embedder.encode(
            [unit.embedding_text for unit in units],
            show_progress=verbose,
        )
        self.chroma.upsert_search_units(units, embeddings, chunks)

    def run(self, rebuild: bool = False, verbose: bool = False) -> IndexSummary:
        if self.paths.dirty_marker.exists():
            self._reconcile_vectors(verbose)
            self.paths.dirty_marker.unlink()

        if rebuild:
            self._mark_dirty()
            with self.store.transaction():
                self.store.reset_all()
            self.chroma.reset_collection()

        discovered = discover_repository_files(self.repo_root, self.config)
        previous = self.store.get_files()
        current_paths = {
            relative_repo_path(self.repo_root, path) for path in discovered
        }
        deleted = sorted(set(previous) - current_paths)
        changed: list[tuple[Path, str, FileRecord]] = []
        unchanged = 0
        for path in discovered:
            relative = relative_repo_path(self.repo_root, path)
            digest = hash_file(path)
            stat = path.stat()
            record = FileRecord(
                path=relative,
                content_hash=digest,
                size_bytes=stat.st_size,
                modified_ns=stat.st_mtime_ns,
                language="python"
                if path.suffix.lower() == ".py"
                else path.suffix.lstrip(".") or "text",
                parser_version=parser_version_for(path),
            )
            old = previous.get(relative)
            if (
                old
                and old.content_hash == record.content_hash
                and old.parser_version == record.parser_version
            ):
                unchanged += 1
            else:
                changed.append((path, relative, record))

        parsed: list[
            tuple[FileRecord, list[SourceChunk], list[SearchUnit], list[ParseError]]
        ] = []
        all_new_units: list[SearchUnit] = []
        new_chunks_by_id: dict[str, SourceChunk] = {}
        for path, relative, record in changed:
            if verbose:
                print(f"Parsing {relative}", file=sys.stderr)
            result = parse_source_file(path, self.repo_root)
            searchable = [
                chunk
                for chunk in result.chunks
                if chunk.kind != "module"
                and chunk.parser_metadata.get("searchable", True)
            ]
            units = [build_search_unit(chunk) for chunk in searchable]
            parsed.append((record, result.chunks, units, result.errors))
            all_new_units.extend(units)
            new_chunks_by_id.update({chunk.id: chunk for chunk in result.chunks})

        embeddings = self.embedder.encode(
            [unit.embedding_text for unit in all_new_units],
            show_progress=verbose,
        )
        if not rebuild:
            self._mark_dirty()
        removed_units: list[str] = []
        with self.store.transaction():
            for relative in deleted:
                removed_units.extend(self.store.delete_file(relative))
            for record, chunks, units, parse_errors in parsed:
                removed_units.extend(
                    self.store.replace_chunks_for_file(record.path, chunks, units)
                )
                self.store.replace_parse_errors(record.path, parse_errors)
                self.store.upsert_file(record)

        self.chroma.delete_search_units(removed_units)
        self.chroma.upsert_search_units(all_new_units, embeddings, new_chunks_by_id)
        errors = self.store.list_parse_errors()
        summary = IndexSummary(
            repository=str(self.repo_root),
            discovered_files=len(discovered),
            unchanged_files=unchanged,
            parsed_files=len(changed),
            deleted_files=len(deleted),
            canonical_chunks=self.store.count_chunks(),
            search_units_embedded=len(all_new_units),
            parse_errors=len(errors),
            index_path=str(self.paths.root),
        )
        self._write_outputs(summary, errors)
        self.paths.dirty_marker.unlink()
        return summary

    def _mark_dirty(self) -> None:
        self.paths.dirty_marker.write_text(
            datetime.now(UTC).isoformat() + "\n", encoding="utf-8"
        )

    def _write_outputs(self, summary: IndexSummary, errors: list[ParseError]) -> None:
        manifest = {
            "version": 1,
            "indexed_at": datetime.now(UTC).isoformat(),
            **summary.to_dict(),
        }
        (self.paths.manifests / "index.json").write_text(
            json.dumps(manifest, indent=2) + "\n",
            encoding="utf-8",
        )
        error_path = self.paths.logs / "parse-errors.jsonl"
        error_path.write_text(
            "".join(json.dumps(error.to_dict()) + "\n" for error in errors),
            encoding="utf-8",
        )
