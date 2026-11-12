from __future__ import annotations

import subprocess

import pytest

from devstill.config import DevstillConfig
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
    embedder.calls.clear()
    batched = search.query_many(
        ["authentication", "profile"],
        limit=8,
        semantic_only=True,
    )
    assert len(batched) == 2
    assert embedder.calls == [["authentication", "profile"]]
    assert HybridSearch._candidate_limit(12) == 50
    assert HybridSearch._candidate_limit(16) == 64
    assert search.query("profile", path="missing/") == []
    search.close()


def test_fuzzy_matching_is_reserved_for_short_queries(configured_repo, monkeypatch):
    import devstill.search

    repo, config_path, config = configured_repo
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "accounts.py").write_text("def authenticate_user():\n    return True\n")
    embedder, chroma = FakeEmbedder(), FakeChroma()
    indexer = RepositoryIndexer(
        config_path, config, embedder=embedder, chroma_store=chroma
    )
    indexer.run()
    indexer.close()
    engine = HybridSearch(config_path, config, embedder=embedder, chroma_store=chroma)
    calls = []
    original = devstill.search.process.extract

    def track(query, *args, **kwargs):
        calls.append(query)
        return original(query, *args, **kwargs)

    monkeypatch.setattr(devstill.search.process, "extract", track)
    try:
        assert engine.query("Where does authentication check the user credentials?")
        assert calls == []
        assert engine.query("authentcate_user")[0].chunk.name == "authenticate_user"
        assert calls == ["authentcate_user"]
    finally:
        engine.close()


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


def test_retrieval_exclusions_filter_indexed_results(configured_repo: tuple) -> None:
    repo, config_path, config = configured_repo
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "visible.py").write_text("def shared_feature():\n    return 1\n")
    (repo / "hidden.py").write_text("def shared_feature_hidden():\n    return 2\n")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    embedder = FakeEmbedder()
    chroma = FakeChroma()
    indexer = RepositoryIndexer(
        config_path, config, embedder=embedder, chroma_store=chroma
    )
    indexer.run()
    indexer.close()
    excluded = DevstillConfig.model_validate(
        {
            **config.model_dump(mode="json"),
            "retrieval": {"exclude": ["hidden.py"]},
        }
    )
    search = HybridSearch(
        config_path,
        excluded,
        embedder=embedder,
        chroma_store=chroma,
    )
    try:
        assert all(
            result.chunk.path != "hidden.py"
            for result in search.query("shared_feature", limit=20)
        )
    finally:
        search.close()


@pytest.mark.parametrize(
    "mode", [{}, {"lexical_only": True}, {"semantic_only": True}, {"exact_only": True}]
)
def test_scope_is_applied_before_candidate_limits(configured_repo, mode):
    repo, config_path, config = configured_repo
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "hidden").mkdir()
    for index in range(90):
        (repo / "hidden" / f"a{index:03}.py").write_text(
            "def shared_feature():\n    return 1\n"
        )
    (repo / "visible.py").write_text("def shared_feature():\n    return 2\n")
    embedder, chroma = FakeEmbedder(), FakeChroma()
    indexer = RepositoryIndexer(
        config_path, config, embedder=embedder, chroma_store=chroma
    )
    indexer.run()
    indexer.close()
    filtered = config.model_copy(deep=True)
    filtered.retrieval.exclude = ["hidden/**"]
    engine = HybridSearch(config_path, filtered, embedder=embedder, chroma_store=chroma)
    try:
        hits = engine.query("shared_feature", limit=5, **mode)
        assert hits and {r.chunk.path for r in hits} == {"visible.py"}
        assert engine.query("shared_feature", path="visible.py", limit=5, **mode)
        assert engine.query("shared_feature", path="hidden/", **mode) == []
    finally:
        engine.close()


def test_kind_is_applied_before_semantic_limit(configured_repo):
    repo, config_path, config = configured_repo
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "many.py").write_text(
        "\n".join(f"def func_{i}():\n    return 1\n" for i in range(90))
        + "\nclass LastClass:\n    pass\n"
    )
    embedder, chroma = FakeEmbedder(), FakeChroma()
    indexer = RepositoryIndexer(
        config_path, config, embedder=embedder, chroma_store=chroma
    )
    indexer.run()
    indexer.close()
    engine = HybridSearch(config_path, config, embedder=embedder, chroma_store=chroma)
    try:
        hits = engine.query("explain this", semantic_only=True, kind="class")
        assert len(hits) == 1 and hits[0].chunk.name == "LastClass"
    finally:
        engine.close()


def test_embedded_reference_resolves_body_and_preserves_typing_requests(
    configured_repo,
):
    repo, config_path, config = configured_repo
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    code = (
        "from typing import overload\n\nclass Values:\n"
        "    @overload\n    def get(self, key: str) -> str: ...\n"
        "    @overload\n    def get(self, key: int) -> int: ...\n"
        "    def get(self, key):\n        return key\n"
    )
    (repo / "values.py").write_text(code)
    (repo / "other.py").write_text(code)
    embedder, chroma = FakeEmbedder(), FakeChroma()
    indexer = RepositoryIndexer(
        config_path, config, embedder=embedder, chroma_store=chroma
    )
    indexer.run()
    indexer.close()
    engine = HybridSearch(config_path, config, embedder=embedder, chroma_store=chroma)
    try:
        hits = engine.query(
            "Change Values.get to return a fallback",
            path="values.py",
            lexical_only=True,
        )
        assert hits[0].chunk.qualname == "Values.get"
        assert "return key" in hits[0].chunk.code
        assert all(r.chunk.path == "values.py" for r in hits)
        assert len([r for r in hits if r.chunk.qualname == "Values.get"]) == 1
        typing = engine.query(
            "Show overload signatures for Values.get",
            path="values.py",
            lexical_only=True,
        )
        assert any("@overload" in r.chunk.code for r in typing)
        assert engine.query("Values.get", exact_only=True, kind="class") == []
    finally:
        engine.close()
