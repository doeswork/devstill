from __future__ import annotations

import subprocess

import pytest

from devstill.indexer import RepositoryIndexer
from devstill.search import HybridSearch

from .conftest import FakeChroma, FakeEmbedder


def test_hybrid_exact_lexical_semantic_and_filters(configured_repo: tuple) -> None:
    repo, config_path, config = configured_repo
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "accounts.py").write_text(
        "def authenticate_user(password):\n"
        '    """Check user credentials."""\n'
        '    return password == "secret"\n\n'
        "class UserProfile:\n"
        "    bio = None\n",
        encoding="utf-8",
    )
    subprocess.run(
        ["git", "-C", str(repo), "add", "accounts.py", "devstill.json"], check=True
    )
    embedder = FakeEmbedder()
    chroma = FakeChroma()
    indexer = RepositoryIndexer(
        config_path, config, embedder=embedder, chroma_store=chroma
    )
    indexer.run()
    indexer.close()
    search = HybridSearch(config_path, config, embedder=embedder, chroma_store=chroma)

    assert (
        search.query("authenticate_user", exact_only=True)[0].chunk.name
        == "authenticate_user"
    )
    assert (
        search.query("credentials", lexical_only=True)[0].chunk.name
        == "authenticate_user"
    )
    semantic = search.query("where is profile data?", semantic_only=True, kind="class")
    assert semantic[0].chunk.name == "UserProfile"
    assert search.query("profile", path="missing/") == []
    search.close()


def test_dirty_index_rejects_search_until_reconciled(configured_repo: tuple) -> None:
    repo, config_path, config = configured_repo
    index_root = repo / ".devstill"
    index_root.mkdir()
    (index_root / ".indexing-dirty").write_text("interrupted\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="index recovery is required"):
        HybridSearch(
            config_path,
            config,
            embedder=FakeEmbedder(),
            chroma_store=FakeChroma(),
        )
