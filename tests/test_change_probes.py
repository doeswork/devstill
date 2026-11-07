import json
from types import SimpleNamespace

import pytest

from benchmarks.retrieval.change_probes import (
    canonicalize,
    identifiers,
    is_overload,
    pack,
)
from devstill.models import SearchResult


def hit(name, start, end, code, decorators=()):
    return SearchResult(
        SimpleNamespace(
            id=f"{name}:{start}",
            path="sample.py",
            qualname=name,
            start_line=start,
            end_line=end,
            code=code,
            parser_metadata={"decorators": list(decorators)},
        ),
        1,
        {"exact": 1},
    )


def test_overload_metadata_handles_class_fragment_indentation():
    stub = hit(
        "Values.getlist",
        10,
        11,
        "@t.overload\n    def getlist(self): ...",
        ["t.overload"],
    )
    implementation = hit(
        "Values.getlist", 14, 20, "def getlist(self):\n        return []"
    )
    assert is_overload(stub.chunk)
    assert not is_overload(implementation.chunk)
    selected = canonicalize(
        [stub, stub, implementation],
        {("sample.py", "Values.getlist"): implementation.chunk},
    )
    assert len(selected) == 1
    assert selected[0].chunk is implementation.chunk


def test_symbol_anchors_come_from_request_text():
    assert identifiers(
        "Change MultiDict.getlist and safe_join; leave other files alone."
    ) == ["MultiDict.getlist", "safe_join"]
    assert identifiers("Build a larger upload feature") == []


def test_budget_packer_deduplicates_ranges_and_skips_oversized_candidate():
    first = hit("one", 1, 5, "abc")
    duplicate = hit("one-part", 2, 4, "bc")
    large = hit("large", 10, 50, "x" * 30)
    last = hit("last", 55, 56, "def")
    result = pack(
        [first, duplicate, large, last],
        lambda rs: sum(len(r.chunk.code) for r in rs),
        6,
        5,
    )
    assert result == [first, last]


def test_missing_implementation_preserves_stub_instead_of_inventing_source():
    stub = hit("missing", 10, 11, "@overload\ndef missing(): ...", ["overload"])
    assert canonicalize([stub], {})[0].chunk is stub.chunk


def test_rescore_updates_labels_without_changing_retrieval(tmp_path, monkeypatch):
    from benchmarks.retrieval import rescore_probes, runner

    source = "def example():\n    return 1\n\n"
    (tmp_path / "example.py").write_text(source)
    case = {
        "id": "example",
        "family": "example",
        "category": "small_edit",
        "workload": "small_edit",
        "split": "dev",
        "label_status": "reviewed",
        "reviewer": "test",
        "query": "Change example",
        "answerable": True,
        "include_tests": False,
        "filters": {},
        "rationale": "Remove a trailing blank line from the required span.",
        "roles": ["implementation"],
        "evidence_groups": [
            [
                {
                    "path": "example.py",
                    "lines": [1, 2],
                    "sha256": runner.digest(
                        "".join(source.splitlines(keepends=True)[:2]).encode()
                    ),
                }
            ]
        ],
    }
    dataset = {
        "schema_version": 1,
        "corpus_commit": runner.CORPUS["commit"],
        "cases": [case],
    }
    dataset_path = tmp_path / "labels.json"
    dataset_path.write_text(json.dumps(dataset))
    context = [{"path": "example.py", "lines": [1, 2], "code": source.rstrip()}]
    row = {
        "id": "example",
        "workload": "small_edit",
        "variant": "current",
        "query": case["query"],
        "pool": context,
        "context": context,
        "metrics": {"context_complete": 0},
        "context_tokens": 10,
        "rewrite_seconds": 0,
    }
    input_path = tmp_path / "queries.jsonl"
    input_path.write_text(json.dumps(row) + "\n")
    monkeypatch.setattr(runner, "check_repo", lambda *_: None)
    output = tmp_path / "rescored"
    rescore_probes.rescore(input_path, dataset_path, tmp_path, output)
    rescored = json.loads((output / "queries.jsonl").read_text())
    assert rescored["context"] == context
    assert rescored["pool"] == context
    assert rescored["metrics"]["context_complete"] == 1
    assert rescored["covered_roles"] == [True]
    case["query"] = "A different retrieval query"
    dataset_path.write_text(json.dumps(dataset))
    with pytest.raises(ValueError, match="cannot change the retrieval query"):
        rescore_probes.rescore(input_path, dataset_path, tmp_path, tmp_path / "invalid")
