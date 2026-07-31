from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from rich.console import Console

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
    ChunkPolicy,
    FileQuality,
    FileRecord,
    IndexSummary,
    ParseError,
    SearchUnit,
    SourceChunk,
)
from devstill.parsers import parse_source_file, parser_version_for
from devstill.parsers.detection import path_is_binary
from devstill.parsers.registry import language_for
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
        policy = ChunkPolicy(
            max_chars=self.config.index.max_chunk_chars,
            max_lines=self.config.index.max_chunk_lines,
            overlap_lines=self.config.index.fallback_overlap_lines,
        )
        previous = self.store.get_files()
        text_files: list[Path] = []
        binary_paths: list[str] = []
        for path in discovered:
            if path_is_binary(path):
                binary_paths.append(relative_repo_path(self.repo_root, path))
            else:
                text_files.append(path)
        current_paths = {
            relative_repo_path(self.repo_root, path) for path in text_files
        }
        deleted = sorted(set(previous) - current_paths)
        changed: list[tuple[Path, str, FileRecord]] = []
        unchanged = 0
        for path in text_files:
            relative = relative_repo_path(self.repo_root, path)
            digest = hash_file(path)
            stat = path.stat()
            record = FileRecord(
                path=relative,
                content_hash=digest,
                size_bytes=stat.st_size,
                modified_ns=stat.st_mtime_ns,
                language=language_for(path),
                parser_version=(
                    f"{parser_version_for(path)}|pipeline:2"
                    f"|chars:{policy.max_chars}|lines:{policy.max_lines}"
                    f"|overlap:{policy.overlap_lines}"
                    f"|noisy:{int(self.config.files.index_noisy_files)}"
                ),
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
            tuple[
                FileRecord,
                list[SourceChunk],
                list[SearchUnit],
                list[ParseError],
                FileQuality,
            ]
        ] = []
        all_new_units: list[SearchUnit] = []
        new_chunks_by_id: dict[str, SourceChunk] = {}
        for path, relative, record in changed:
            if verbose:
                print(f"Parsing {relative}", file=sys.stderr)
            result = parse_source_file(
                path,
                self.repo_root,
                policy=policy,
                index_noisy_files=self.config.files.index_noisy_files,
            )
            if result.binary:
                # Binary detection is repeated defensively inside the parse pipeline.
                binary_paths.append(relative)
                continue
            if result.quality is None:
                raise RuntimeError(
                    f"Parser did not assess index quality for {relative}"
                )
            searchable = [
                chunk
                for chunk in result.chunks
                if chunk.parser_metadata.get("searchable", True)
            ]
            units = [build_search_unit(chunk) for chunk in searchable]
            parsed.append((record, result.chunks, units, result.errors, result.quality))
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
            for record, chunks, units, parse_errors, quality in parsed:
                removed_units.extend(
                    self.store.replace_chunks_for_file(record.path, chunks, units)
                )
                self.store.replace_parse_errors(record.path, parse_errors)
                self.store.upsert_file(record)
                self.store.upsert_quality(quality)

        self.chroma.delete_search_units(removed_units)
        self.chroma.upsert_search_units(all_new_units, embeddings, new_chunks_by_id)
        errors = self.store.list_parse_errors()
        quality = self.store.list_quality()
        summary = IndexSummary(
            repository=str(self.repo_root),
            discovered_files=len(discovered),
            unchanged_files=unchanged,
            parsed_files=len(parsed),
            deleted_files=len(deleted),
            canonical_chunks=self.store.count_chunks(),
            search_units_embedded=len(all_new_units),
            parse_errors=len(errors),
            good_files=sum(item.status == "GOOD" for item in quality),
            meh_files=sum(item.status == "MEH" for item in quality),
            bad_files=sum(item.status == "BAD" for item in quality),
            binary_files_skipped=len(binary_paths),
            index_path=str(self.paths.root),
        )
        self._write_outputs(summary, errors, quality, sorted(set(binary_paths)))
        if verbose:
            self._write_verbose_quality(quality)
        self.paths.dirty_marker.unlink()
        return summary

    def _mark_dirty(self) -> None:
        self.paths.dirty_marker.write_text(
            datetime.now(UTC).isoformat() + "\n", encoding="utf-8"
        )

    def _write_outputs(
        self,
        summary: IndexSummary,
        errors: list[ParseError],
        quality: list[FileQuality],
        binary_paths: list[str],
    ) -> None:
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
        quality_manifest = {
            "version": 1,
            "indexed_at": manifest["indexed_at"],
            "good_files": summary.good_files,
            "meh_files": summary.meh_files,
            "bad_files": summary.bad_files,
            "binary_files_skipped": summary.binary_files_skipped,
            "binary_files": binary_paths,
            "files": [item.to_dict() for item in quality],
        }
        (self.paths.manifests / "quality.json").write_text(
            json.dumps(quality_manifest, indent=2) + "\n",
            encoding="utf-8",
        )

    @staticmethod
    def _write_verbose_quality(quality: list[FileQuality]) -> None:
        diagnostic_console = Console(stderr=True)
        good_by_parser: dict[str, int] = {}
        for item in quality:
            if item.status == "GOOD":
                good_by_parser[item.parser] = good_by_parser.get(item.parser, 0) + 1
        if good_by_parser:
            diagnostic_console.print("[green]GOOD files by parser:[/green]")
            for parser, count in sorted(good_by_parser.items()):
                diagnostic_console.print(f"  [green]{parser}: {count}[/green]")
        for item in quality:
            if item.status == "GOOD":
                continue
            reasons = "; ".join(item.reasons) or "no reason recorded"
            color = "yellow" if item.status == "MEH" else "red"
            diagnostic_console.print(
                f"[{color}]{item.status} {item.path}: {reasons}[/{color}]"
            )
