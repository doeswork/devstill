from __future__ import annotations

import subprocess

from devstill.indexer import RepositoryIndexer
from devstill.parsers import JAVASCRIPT_FAMILY_PARSER_VERSION

from .conftest import FakeChroma, FakeEmbedder


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
    second = indexer.run()
    assert second.unchanged_files == 1
    assert second.search_units_embedded == 0
    source.write_text("def authenticate():\n    return False\n", encoding="utf-8")
    third = indexer.run()
    assert third.parsed_files == 1
    source.unlink()
    fourth = indexer.run()
    assert fourth.deleted_files == 1
    assert indexer.store.count_chunks() == 0
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

    recovered = indexer.run()

    assert recovered.unchanged_files == 1
    assert recovered.parsed_files == 0
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
    assert binary.deleted_files == 1
    assert indexer.store.list_chunks_for_file("accounts.py") == []
    assert indexer.store.list_quality() == []
    assert not chroma.records
    indexer.close()
