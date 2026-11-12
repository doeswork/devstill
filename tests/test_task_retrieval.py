from types import SimpleNamespace

from devstill.task_retrieval import retrieve_task_candidates
from tests.test_context import make_result


def test_relationship_expansion_preserves_anchors_and_exclusions():
    anchor = make_result(
        path="src/api.py", code="def greet(name): return name", name="greet"
    )
    test = make_result(
        path="tests/test_api.py", code='def test_greet(): assert greet("Ada") == "Ada"'
    )
    excluded = make_result(path="private/test_api.py", code='greet("secret")')

    class Store:
        def retrieval_paths(self):
            return ["src/api.py", "tests/test_api.py", "private/test_api.py"]

        def get_files(self):
            return {"tests/test_api.py": SimpleNamespace(content_hash="indexed")}

        def search_fts(self, query, **kwargs):
            assert "private/test_api.py" not in kwargs["allowed_paths"]
            return [("test", 1), ("excluded", 1)]

        def get_chunks_by_ids(self, ids):
            return {"test": test.chunk, "excluded": excluded.chunk}

    engine = SimpleNamespace(
        query=lambda q, limit: [anchor], store=Store(), retrieval_exclude=["private/**"]
    )
    result = retrieve_task_candidates(engine, "Update greeting")
    assert result[0] is anchor
    assert [r.chunk.path for r in result] == ["src/api.py", "tests/test_api.py"]
    assert result[1].source_hash == "indexed"
