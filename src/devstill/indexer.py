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
from devstill.content_policy import CONTENT_POLICY_VERSION
from devstill.context import build_search_unit
from devstill.embeddings import EmbeddingBackend
from devstill.git_files import RepositoryFile, discover_repository_inventory
from devstill.hashing import hash_file, hash_text
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
from devstill.parsers.registry import language_for
from devstill.paths import IndexPaths
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

        inventory = discover_repository_inventory(self.repo_root, self.config)
        policy = ChunkPolicy(
            max_chars=self.config.index.max_chunk_chars,
            max_lines=self.config.index.max_chunk_lines,
            overlap_lines=self.config.index.fallback_overlap_lines,
        )
        previous = self.store.get_files()
        current_paths = {item.relative for item in inventory.files}
        deleted = sorted(set(previous) - current_paths)
        changed: list[tuple[RepositoryFile, FileRecord]] = []
        unchanged = 0
        override_fingerprint = hash_text(
            json.dumps(sorted(self.config.files.include_ignored))
        )[:12]
        for item in inventory.files:
            path = item.path
            relative = item.relative
            digest = hash_file(path)
            stat = path.stat()
            record = FileRecord(
                path=relative,
                content_hash=digest,
                size_bytes=stat.st_size,
                modified_ns=stat.st_mtime_ns,
                language=language_for(path),
                parser_version=(
                    f"{parser_version_for(path)}|{CONTENT_POLICY_VERSION}|pipeline:3"
                    f"|chars:{policy.max_chars}|lines:{policy.max_lines}"
                    f"|overlap:{policy.overlap_lines}"
                    f"|noisy:{int(self.config.files.index_noisy_files)}"
                    f"|ignored:{override_fingerprint}"
                    f"|category:{item.decision.category}"
                    f"|disposition:{item.decision.disposition}"
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
                changed.append((item, record))

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
        for item, record in changed:
            path = item.path
            relative = item.relative
            if verbose:
                action = (
                    "Parsing" if item.decision.disposition == "indexed" else "Assessing"
                )
                print(f"{action} {relative}", file=sys.stderr)
            result = parse_source_file(
                path,
                self.repo_root,
                policy=policy,
                index_noisy_files=self.config.files.index_noisy_files,
                include_ignored=self.config.files.include_ignored,
                decision=item.decision,
            )
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
            discovered_files=len(inventory.files),
            unchanged_files=unchanged,
            parsed_files=sum(
                item.disposition == "indexed" for _, _, _, _, item in parsed
            ),
            deleted_files=len(deleted),
            canonical_chunks=self.store.count_chunks(),
            search_units_embedded=len(all_new_units),
            parse_errors=len(errors),
            eligible_files=sum(item.disposition == "indexed" for item in quality),
            code_files=sum(
                item.disposition == "indexed" and item.category == "code"
                for item in quality
            ),
            documentation_files=sum(
                item.disposition == "indexed" and item.category == "documentation"
                for item in quality
            ),
            configuration_files=sum(
                item.disposition == "indexed" and item.category == "configuration"
                for item in quality
            ),
            good_files=sum(
                item.disposition == "indexed" and item.status == "GOOD"
                for item in quality
            ),
            meh_files=sum(
                item.disposition == "indexed" and item.status == "MEH"
                for item in quality
            ),
            bad_files=sum(
                item.disposition == "indexed" and item.status == "BAD"
                for item in quality
            ),
            ignored_files=sum(item.disposition == "ignored" for item in quality),
            noisy_files_skipped=sum(
                item.disposition == "ignored" and item.category == "generated"
                for item in quality
            ),
            empty_files_skipped=sum(item.category == "empty" for item in quality),
            binary_files_skipped=sum(item.category == "binary" for item in quality),
            index_path=str(self.paths.root),
        )
        self._write_outputs(summary, errors, quality)
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
    ) -> None:
        manifest = {
            "version": 2,
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
            "version": 2,
            "indexed_at": manifest["indexed_at"],
            "good_files": summary.good_files,
            "meh_files": summary.meh_files,
            "bad_files": summary.bad_files,
            "eligible_files": summary.eligible_files,
            "ignored_files": summary.ignored_files,
            "noisy_files_skipped": summary.noisy_files_skipped,
            "empty_files_skipped": summary.empty_files_skipped,
            "binary_files_skipped": summary.binary_files_skipped,
            "binary_files": [
                item.path for item in quality if item.category == "binary"
            ],
            "ignored": [
                item.to_dict() for item in quality if item.disposition == "ignored"
            ],
            "skipped": [
                item.to_dict() for item in quality if item.disposition == "skipped"
            ],
            "files": [item.to_dict() for item in quality],
        }
        (self.paths.manifests / "quality.json").write_text(
            json.dumps(quality_manifest, indent=2) + "\n",
            encoding="utf-8",
        )

    @staticmethod
    def _write_verbose_quality(quality: list[FileQuality]) -> None:
        diagnostic_console = Console(stderr=True)
        good_by_parser: dict[tuple[str, str], int] = {}
        for item in quality:
            if item.status == "GOOD":
                key = (item.category, item.parser)
                good_by_parser[key] = good_by_parser.get(key, 0) + 1
        if good_by_parser:
            diagnostic_console.print("[green]GOOD files by category/parser:[/green]")
            for (category, parser), count in sorted(good_by_parser.items()):
                diagnostic_console.print(
                    f"  [green]{category} / {parser}: {count}[/green]"
                )
        for item in quality:
            if item.status == "GOOD":
                continue
            reasons = "; ".join(item.reasons) or "no reason recorded"
            if item.disposition != "indexed":
                diagnostic_console.print(
                    f"[dim]{item.status} {item.path} ({item.category}): {reasons}[/dim]"
                )
                continue
            color = "yellow" if item.status == "MEH" else "red"
            diagnostic_console.print(
                f"[{color}]{item.status} {item.path}: {reasons}[/{color}]"
            )
