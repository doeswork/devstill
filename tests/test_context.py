from __future__ import annotations

import json

from devstill.context import (
    context_record,
    is_test_chunk,
    render_context_json,
    render_context_markdown,
    select_context_results,
    write_context_artifacts,
)
from devstill.models import SearchResult, SourceChunk


def make_result(
    *,
    path: str = "core/views.py",
    name: str = "login_api",
    kind: str = "function",
    code: str = "def login_api():\n    return True",
    start_line: int = 10,
    end_line: int = 11,
) -> SearchResult:
    chunk = SourceChunk(
        id=f"{path}::{name}::{kind}",
        path=path,
        filename=path.rsplit("/", 1)[-1],
        language="python",
        kind=kind,
        name=name,
        qualname=name,
        parent_id=None,
        start_line=start_line,
        end_line=end_line,
        code=code,
        leading_comments=None,
        content_hash="hash",
        parser_metadata={"parser": "python_ast"},
    )
    return SearchResult(chunk=chunk, final_score=0.5, scores={"semantic": 0.8})


def test_compact_context_json_has_only_agent_fields() -> None:
    result = make_result()
    record = context_record(result)
    assert list(record) == ["path", "lines", "symbol", "kind", "code"]
    assert record["lines"] == [10, 11]
    payload = json.loads(render_context_json([result]))
    assert payload == [record]
    assert "content_hash" not in payload[0]
    assert "scores" not in payload[0]


def test_markdown_has_provenance_exact_code_and_safe_fence() -> None:
    code = "def example():\n    value = ```\n    return value"
    result = make_result(code=code)
    rendered = render_context_markdown(
        [result],
        "explain login",
        "/workspace/repository",
    )
    assert "# Devstill AI Context" in rendered
    assert "Query: explain login" in rendered
    assert "Repository: /workspace/repository" in rendered
    assert "core/views.py:10-11 — login_api (function)" in rendered
    assert code in rendered
    assert "````python" in rendered


def test_empty_markdown_and_json_are_clear() -> None:
    assert render_context_json([]) == "[]"
    rendered = render_context_markdown([], "missing", "/repo")
    assert "No relevant context found." in rendered


def test_test_detection_and_filtering_happen_before_limit() -> None:
    test_file = make_result(path="tests/test_auth.py", name="test_login")
    tests_module = make_result(path="core/tests.py", name="AuthApiTests.test_logout")
    production = make_result(name="login_api")
    second_production = make_result(name="session_api")
    assert is_test_chunk(test_file.chunk)
    assert is_test_chunk(tests_module.chunk)
    assert is_test_chunk(make_result(path="frontend/App.test.tsx").chunk)
    assert is_test_chunk(make_result(path="frontend/__tests__/App.tsx").chunk)
    assert not is_test_chunk(production.chunk)
    assert not is_test_chunk(make_result(name="Contest").chunk)

    selected = select_context_results(
        [test_file, production, tests_module, second_production],
        limit=2,
        include_tests=False,
    )
    assert [result.chunk.name for result in selected] == ["login_api", "session_api"]
    included = select_context_results(
        [test_file, production],
        limit=2,
        include_tests=True,
    )
    assert included == [test_file, production]


def test_context_artifacts_overwrite_both_files(tmp_path) -> None:
    markdown_path, json_path = write_context_artifacts(
        tmp_path,
        "# First",
        '[{"first": true}]',
    )
    assert markdown_path.name == ".context.md"
    assert json_path.name == ".context.json"
    write_context_artifacts(tmp_path, "# Second", "[]")
    assert markdown_path.read_text(encoding="utf-8") == "# Second\n"
    assert json_path.read_text(encoding="utf-8") == "[]\n"
