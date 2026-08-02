from __future__ import annotations

import subprocess

import pytest

from devstill.indexer import RepositoryIndexer
from devstill.parsers import JAVASCRIPT_FAMILY_PARSER_VERSION

from .conftest import FakeChroma, FakeEmbedder


def test_file_progress_reporting_is_capped_and_keeps_boundaries() -> None:
    positions = [
        position
        for position in range(1, 10_001)
        if RepositoryIndexer._report_file_position(position, 10_000)
    ]

    assert positions[0] == 1
    assert positions[-1] == 10_000
    assert len(positions) <= 201


def test_incremental_index_change_and_delete(configured_repo: tuple) -> None:
    repo, config_path, config = configured_repo
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    source = repo / "accounts.py"
    source.write_text("def authenticate():\n    return True\n", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(repo), "add", "accounts.py", "devstill.json"], check=True
    )
    embedder = FakeEmbedder()
    chroma = FakeChroma()

    indexer = RepositoryIndexer(
        config_path, config, embedder=embedder, chroma_store=chroma
    )
    first = indexer.run()
    assert first.parsed_files == 1
    assert first.search_units_embedded == 1
    unchanged_events = []
    second = indexer.run(progress=unchanged_events.append)
    assert second.unchanged_files == 1
    assert second.search_units_embedded == 0
    unchanged_files = unchanged_events[-1].files
    assert unchanged_files is not None
    assert unchanged_files.total == 1
    assert unchanged_files.checked == 1
    assert unchanged_files.changed == 0
    assert unchanged_files.unchanged == 1
    assert unchanged_files.parsed == 0
    source.write_text("def authenticate():\n    return False\n", encoding="utf-8")
    third = indexer.run()
    assert third.parsed_files == 1
    source.unlink()
    fourth = indexer.run()
    assert fourth.deleted_files == 1
    assert indexer.store.count_chunks() == 0
    indexer.close()


def test_index_reports_optional_progress(configured_repo: tuple) -> None:
    class ReportingEmbedder(FakeEmbedder):
        def encode(self, texts, show_progress=False, progress=None):
            values = super().encode(texts, show_progress=show_progress)
            if progress is not None and texts:
                progress(1, len(texts))
                progress(len(texts), len(texts))
            return values

    repo, config_path, config = configured_repo
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    source = repo / "accounts.py"
    source.write_text(
        "def authenticate():\n    return True\n\ndef authorize():\n    return True\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "-C", str(repo), "add", "accounts.py"], check=True)
    events = []
    indexer = RepositoryIndexer(
        config_path,
        config,
        embedder=ReportingEmbedder(),
        chroma_store=FakeChroma(),
    )

    indexer.run(progress=events.append)

    assert events[0].stage == "discovery"
    assert any(event.stage == "parsing" for event in events)
    assert any(event.stage == "embedding" for event in events)
    assert any(
        event.stage == "embedding" and event.current == 1 and event.total == 2
        for event in events
    )
    checking = [event.files for event in events if event.stage == "checking"]
    assert checking
    assert all(snapshot is not None for snapshot in checking)
    assert [snapshot.checked for snapshot in checking] == sorted(
        snapshot.checked for snapshot in checking
    )
    embedding = next(
        event for event in events if event.stage == "embedding" and event.current == 1
    )
    assert embedding.files is not None
    assert embedding.files.total == 1
    assert embedding.files.checked == 1
    assert embedding.files.changed == 1
    assert embedding.files.parsed == 1
    assert any(event.stage == "persistence" for event in events)
    assert events[-1].stage == "complete"
    indexer.close()


def test_parse_error_persists_when_broken_file_is_unchanged(
    configured_repo: tuple,
) -> None:
    repo, config_path, config = configured_repo
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    source = repo / "broken.py"
    source.write_text("def broken(:\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "broken.py"], check=True)
    indexer = RepositoryIndexer(
        config_path,
        config,
        embedder=FakeEmbedder(),
        chroma_store=FakeChroma(),
    )
    assert indexer.run().parse_errors == 1
    second = indexer.run()
    assert second.unchanged_files == 1
    assert second.parse_errors == 1
    indexer.close()


def test_parser_version_migrates_only_javascript_family(
    configured_repo: tuple,
) -> None:
    repo, config_path, config = configured_repo
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    python_source = repo / "accounts.py"
    python_source.write_text("def authenticate():\n    return True\n", encoding="utf-8")
    tsx_source = repo / "App.tsx"
    tsx_source.write_text(
        "export function App() {\n  return <main />\n}\n",
        encoding="utf-8",
    )
    subprocess.run(
        ["git", "-C", str(repo), "add", "accounts.py", "App.tsx"],
        check=True,
    )
    embedder = FakeEmbedder()
    indexer = RepositoryIndexer(
        config_path,
        config,
        embedder=embedder,
        chroma_store=FakeChroma(),
    )

    first = indexer.run()
    assert first.parsed_files == 2
    indexer.store.connection.execute(
        "UPDATE files SET parser_version = '1' WHERE path = 'App.tsx'"
    )
    indexer.store.connection.commit()

    migrated = indexer.run()
    assert migrated.parsed_files == 1
    assert migrated.unchanged_files == 1
    assert migrated.search_units_embedded == 1
    assert indexer.store.get_files()["App.tsx"].parser_version.startswith(
        JAVASCRIPT_FAMILY_PARSER_VERSION
    )
    assert all(
        chunk.kind != "document"
        for chunk in indexer.store.list_chunks_for_file("App.tsx")
    )

    unchanged = indexer.run()
    assert unchanged.unchanged_files == 2
    assert unchanged.parsed_files == 0
    assert unchanged.search_units_embedded == 0
    indexer.close()


def test_chunk_policy_change_reprocesses_unchanged_source(
    configured_repo: tuple,
) -> None:
    repo, config_path, config = configured_repo
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    source = repo / "accounts.py"
    source.write_text("def authenticate():\n    return True\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "accounts.py"], check=True)
    chroma = FakeChroma()
    indexer = RepositoryIndexer(
        config_path,
        config,
        embedder=FakeEmbedder(),
        chroma_store=chroma,
    )
    assert indexer.run().parsed_files == 1
    assert indexer.run().parsed_files == 0
    indexer.close()

    changed_config = config.model_copy(
        update={"index": config.index.model_copy(update={"max_chunk_chars": 7000})}
    )
    migrated = RepositoryIndexer(
        config_path,
        changed_config,
        embedder=FakeEmbedder(),
        chroma_store=chroma,
    )
    assert migrated.run().parsed_files == 1
    assert migrated.run().parsed_files == 0
    migrated.close()


def test_dirty_marker_reconciles_vectors_before_incremental_skip(
    configured_repo: tuple,
) -> None:
    repo, config_path, config = configured_repo
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    source = repo / "accounts.py"
    source.write_text("def authenticate():\n    return True\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "accounts.py"], check=True)
    chroma = FakeChroma()
    embedder = FakeEmbedder()
    indexer = RepositoryIndexer(
        config_path,
        config,
        embedder=embedder,
        chroma_store=chroma,
    )
    indexer.run()
    assert chroma.records
    chroma.records.clear()
    indexer.paths.dirty_marker.write_text("interrupted\n", encoding="utf-8")

    events = []
    recovered = indexer.run(progress=events.append)

    assert recovered.unchanged_files == 1
    assert recovered.parsed_files == 0
    assert recovered.reconciled_search_units == 1
    assert any(
        event.stage == "embedding" and event.current == event.total == 1
        for event in events
    )
    assert chroma.records
    reconcile = next(event for event in events if event.stage == "reconcile")
    assert reconcile.files is not None
    assert reconcile.files.total == 1
    assert not indexer.paths.dirty_marker.exists()
    indexer.close()


def test_failed_reconciliation_keeps_dirty_marker_for_retry(
    configured_repo: tuple,
) -> None:
    class FailingChroma(FakeChroma):
        fail = False

        def upsert_search_units(
            self,
            units,
            embeddings,
            chunks,
            progress=None,
        ):
            if self.fail:
                raise RuntimeError("second Chroma batch failed")
            return super().upsert_search_units(
                units,
                embeddings,
                chunks,
                progress=progress,
            )

    repo, config_path, config = configured_repo
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    source = repo / "accounts.py"
    source.write_text("def authenticate():\n    return True\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "accounts.py"], check=True)
    chroma = FailingChroma()
    indexer = RepositoryIndexer(
        config_path,
        config,
        embedder=FakeEmbedder(),
        chroma_store=chroma,
    )
    indexer.run()
    indexer.paths.dirty_marker.write_text("interrupted\n", encoding="utf-8")
    chroma.records.clear()
    chroma.fail = True

    with pytest.raises(RuntimeError, match="second Chroma batch failed"):
        indexer.run()

    assert indexer.paths.dirty_marker.exists()
    assert not chroma.records
    chroma.fail = False
    recovered = indexer.run()
    assert recovered.reconciled_search_units == 1
    assert chroma.records
    assert not indexer.paths.dirty_marker.exists()
    indexer.close()


def test_file_becoming_binary_removes_stale_chunks_and_quality(
    configured_repo: tuple,
) -> None:
    repo, config_path, config = configured_repo
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    source = repo / "accounts.py"
    source.write_text("def authenticate():\n    return True\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "accounts.py"], check=True)
    chroma = FakeChroma()
    indexer = RepositoryIndexer(
        config_path,
        config,
        embedder=FakeEmbedder(),
        chroma_store=chroma,
    )
    assert indexer.run().good_files == 1

    source.write_bytes(b"\0binary")
    binary = indexer.run()

    assert binary.binary_files_skipped == 1
    assert binary.deleted_files == 0
    assert indexer.store.list_chunks_for_file("accounts.py") == []
    quality = indexer.store.list_quality()
    assert len(quality) == 1
    assert quality[0].status == "SKIPPED"
    assert quality[0].category == "binary"
    assert quality[0].disposition == "skipped"
    assert not chroma.records
    indexer.close()
