from __future__ import annotations

import json

from devstill.context import (
    context_record,
    is_test_chunk,
    planning_retrieval_limit,
    query_requests_tests,
    render_context_json,
    render_context_markdown,
    select_adaptive_context_results,
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
    assert "Query: explain login · Repository: /workspace/repository" in rendered
    assert "login_api (function)\n````python" in rendered


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


def test_adaptive_context_stops_at_semantic_gap_after_minimum() -> None:
    results = [
        make_result(name=f"symbol_{index}", path=f"src/file_{index}.py")
        for index in range(5)
    ]
    for result, score in zip(results, [0.91, 0.88, 0.85, 0.70, 0.68], strict=True):
        result.scores = {"semantic": score}

    selected = select_adaptive_context_results(
        results,
        min_limit=3,
        max_limit=5,
        include_tests=False,
    )

    assert [result.chunk.name for result in selected] == [
        "symbol_0",
        "symbol_1",
        "symbol_2",
    ]


def test_adaptive_context_keeps_close_and_multi_channel_candidates() -> None:
    results = [
        make_result(name=f"symbol_{index}", path=f"src/file_{index}.py")
        for index in range(5)
    ]
    for result, score in zip(results, [0.91, 0.88, 0.85, 0.83, 0.60], strict=True):
        result.scores = {"semantic": score}
    results[-1].scores["lexical"] = 0.9

    selected = select_adaptive_context_results(
        results,
        min_limit=3,
        max_limit=5,
        include_tests=False,
    )

    assert selected == results
    assert planning_retrieval_limit(5) == 12
    assert planning_retrieval_limit(8) == 16


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


def test_context_backfills_after_budget_and_overlap_rejections():
    large = make_result(path="src/large.py", name="large", code="x" * 100)
    first = make_result(name="first", code="abc", start_line=1, end_line=5)
    overlap = make_result(name="overlap", code="bc", start_line=2, end_line=4)
    last = make_result(name="last", code="def", start_line=10, end_line=12)
    selected = select_adaptive_context_results(
        [large, first, overlap, last],
        min_limit=1,
        max_limit=2,
        include_tests=False,
        fits=lambda rs: sum(len(r.chunk.code) for r in rs) <= 6,
    )
    assert selected == [first, last]


def test_context_can_skip_unsupported_candidate_and_use_later_anchor():
    first = make_result(name="first", path="first.py")
    weak = make_result(name="weak", path="weak.py")
    weak.scores = {"semantic": 0.1}
    anchor = make_result(name="anchor", path="anchor.py")
    anchor.scores = {"exact": 1.0}
    assert select_adaptive_context_results(
        [first, weak, anchor],
        min_limit=1,
        max_limit=2,
        include_tests=False,
    ) == [first, anchor]


def test_requested_tests_are_detected_without_prompt_length_routing():
    assert query_requests_tests("Fix safe_join and add regression assertions")
    assert query_requests_tests("Extend the existing tests")
    assert not query_requests_tests("Update the error message without tests")
    assert not query_requests_tests("Leave the tests unchanged; rename the variable")


def test_focused_method_keeps_parent_from_consuming_budget():
    parent = make_result(
        kind="class", name="Values", code="x" * 100, start_line=1, end_line=80
    )
    method = make_result(
        kind="method", name="Values.get", code="abc", start_line=10, end_line=11
    )
    helper = make_result(name="helper", code="def", start_line=90, end_line=92)
    assert select_adaptive_context_results(
        [parent, method, helper],
        min_limit=1,
        max_limit=2,
        include_tests=False,
        fits=lambda rs: sum(len(r.chunk.code) for r in rs) <= 6,
    ) == [method, helper]
    parent.scores = {"exact": 1.0}
    assert select_adaptive_context_results(
        [parent, method],
        min_limit=1,
        max_limit=2,
        include_tests=False,
    ) == [parent]


def test_focused_child_retains_parent_priority_over_bulky_sibling():
    parent = make_result(
        kind="class", name="Response", code="x" * 9, start_line=10, end_line=30
    )
    sibling = make_result(
        kind="class", name="Response", code="y" * 9, start_line=31, end_line=50
    )
    child = make_result(
        kind="method", name="Response.range", code="abc", start_line=11, end_line=15
    )
    assert select_adaptive_context_results(
        [parent, sibling, child],
        min_limit=1,
        max_limit=3,
        include_tests=False,
        fits=lambda rs: sum(len(r.chunk.code) for r in rs) <= 10,
    ) == [child]
