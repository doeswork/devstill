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
    assert (
        indexer.store.get_files()["App.tsx"].parser_version
        == JAVASCRIPT_FAMILY_PARSER_VERSION
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
