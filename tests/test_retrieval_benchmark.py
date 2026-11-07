from __future__ import annotations

import copy
import json
from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace

import pytest

from benchmarks.retrieval import runner
from devstill.config import DevstillConfig
from devstill.models import SearchResult, SourceChunk


def test_custom_manifest_routes_validation_to_pinned_corpus(tmp_path, monkeypatch):
    manifest = {
        "id": "other-corpus",
        "url": "https://github.com/example/other.git",
        "ref": "v1",
        "commit": "a" * 40,
    }
    path = tmp_path / "corpus.json"
    path.write_text(json.dumps(manifest))
    observed = []
    monkeypatch.setattr(runner, "CORPUS", runner.CORPUS.copy())
    monkeypatch.setattr(runner, "validate", lambda args: observed.append(args))
    monkeypatch.setattr(
        "sys.argv",
        [
            "runner",
            "--corpus-manifest",
            str(path),
            "validate",
            "--dataset",
            "labels.json",
        ],
    )
    runner.main()
    assert runner.CORPUS["commit"] == "a" * 40
    assert observed[0].repo == runner.DEFAULT_WORKSPACE / "repos/other-corpus"
    assert observed[0].dataset == Path("labels.json")


def test_custom_corpus_refuses_implicit_werkzeug_labels(tmp_path, monkeypatch):
    manifest = {
        "id": "other-corpus",
        "url": "https://github.com/example/other.git",
        "ref": "v1",
        "commit": "a" * 40,
    }
    path = tmp_path / "corpus.json"
    path.write_text(json.dumps(manifest))
    monkeypatch.setattr(runner, "CORPUS", runner.CORPUS.copy())
    monkeypatch.setattr(
        "sys.argv", ["runner", "--corpus-manifest", str(path), "validate"]
    )
    with pytest.raises(SystemExit) as error:
        runner.main()
    assert error.value.code == 2


def case(repo: Path) -> dict:
    source = repo / "sample.py"
    source.write_text("def feature():\n    return 42\n")
    return {
        "id": "sample",
        "query": "Where is the feature implemented?",
        "family": "feature",
        "category": "natural_language",
        "split": "dev",
        "label_status": "seed",
        "reviewer": None,
        "answerable": True,
        "include_tests": False,
        "filters": {},
        "rationale": "Function body evidence.",
        "evidence_groups": [
            [
                {
                    "path": "sample.py",
                    "lines": [1, 2],
                    "sha256": runner.digest(source.read_bytes()),
                }
            ]
        ],
    }


def dataset(entry: dict) -> dict:
    return {
        "schema_version": 1,
        "corpus_commit": runner.CORPUS["commit"],
        "cases": [entry],
    }


def test_evidence_metrics_distinguish_file_hit_from_required_code(tmp_path):
    entry = case(tmp_path)
    wrong = [{"path": "sample.py", "lines": [10, 20]}]
    metrics = runner.score(entry, wrong, wrong)
    assert metrics["file_hit_at_5_chunks"]
    assert not metrics["evidence_hit_at_5_chunks"]
    assert metrics["context_evidence_recall"] == 0
    split_evidence = [
        {"path": "sample.py", "lines": [1, 1]},
        {"path": "sample.py", "lines": [2, 2]},
    ]
    metrics = runner.score(entry, split_evidence, split_evidence)
    assert metrics["context_complete"]
    assert metrics["evidence_mrr_at_pool"] == 0.5
    assert not runner.covers({"path": "sample.py", "lines": [1, 3]}, split_evidence)


def test_multiple_required_groups_and_alternative_evidence(tmp_path):
    entry = case(tmp_path)
    entry["evidence_groups"][0].append({"path": "alternative.py", "lines": [2, 3]})
    entry["evidence_groups"].append([{"path": "caller.py", "lines": [5, 6]}])
    partial = [{"path": "alternative.py", "lines": [1, 4]}]
    assert runner.evidence_recall(entry, partial) == 0.5
    complete = partial + [{"path": "caller.py", "lines": [5, 8]}]
    assert runner.evidence_recall(entry, complete) == 1


def test_negative_cases_do_not_inflate_positive_metrics(tmp_path):
    entry = case(tmp_path)
    entry.update(answerable=False, evidence_groups=[])
    metrics = runner.score(entry, [], [])
    assert metrics == {
        "negative_returned_any": False,
        "negative_context_nonempty": False,
    }
    assert runner.score(entry, [{"path": "unrelated.py", "lines": [1, 2]}], [1])[
        "negative_context_nonempty"
    ]


def test_dataset_rejects_drift_leakage_and_invalid_labels(tmp_path):
    original = dataset(case(tmp_path))
    runner.validate_dataset(original, tmp_path)
    changed = copy.deepcopy(original)
    changed["cases"][0]["evidence_groups"][0][0]["sha256"] = "wrong"
    with pytest.raises(ValueError, match="Evidence content changed"):
        runner.validate_dataset(changed, tmp_path)
    changed = copy.deepcopy(original)
    other = copy.deepcopy(changed["cases"][0])
    other.update(id="paraphrase", split="holdout")
    changed["cases"].append(other)
    with pytest.raises(ValueError, match="leaks across splits"):
        runner.validate_dataset(changed, tmp_path)
    changed = copy.deepcopy(original)
    changed["cases"][0]["label_status"] = "reviewed"
    with pytest.raises(ValueError, match="name their reviewer"):
        runner.validate_dataset(changed, tmp_path)


def test_paired_comparison_rejects_incompatible_protocols():
    def report(hit):
        return {
            "protocol": {"dataset": "same", "limit": 12},
            "queries": [
                {
                    "id": "one",
                    "family": "one",
                    "answerable": True,
                    "metrics": {"evidence_hit_at_5_chunks": hit},
                }
            ],
        }

    compared = runner.paired_comparison(report(False), report(True))
    assert compared["paired_delta"] == 1
    assert compared["wins"] == ["one"]
    different = report(True)
    different["protocol"]["limit"] = 50
    with pytest.raises(ValueError, match="Protocol/dataset mismatch"):
        runner.paired_comparison(report(False), different)


def test_index_lock_prevents_overlapping_benchmark_access(tmp_path):
    with (
        runner.index_lock(tmp_path),
        pytest.raises(ValueError, match="Another benchmark"),
        runner.index_lock(tmp_path),
    ):
        pass


def test_run_pipeline_is_read_only_and_records_outputs_without_llm(
    tmp_path, monkeypatch
):
    import devstill.search

    repo = tmp_path / "repo"
    repo.mkdir()
    data = dataset(case(repo))
    data_path = tmp_path / "cases.json"
    runner.write_json(data_path, data)
    index = tmp_path / "index"
    index.mkdir()
    identity = {"corpus_commit": runner.CORPUS["commit"]}
    runner.write_json(
        index / "benchmark-index.json",
        {"identity_sha256": runner.fingerprint(identity)},
    )
    monkeypatch.setattr(
        runner, "index_identity", lambda _: (DevstillConfig(), repo, index, identity)
    )
    monkeypatch.setattr(runner, "check_repo", lambda *_: None)
    queries = []
    hit = SearchResult(
        SourceChunk(
            "chunk",
            "sample.py",
            "sample.py",
            "python",
            "function",
            "feature",
            "feature",
            None,
            1,
            2,
            (repo / "sample.py").read_text(),
            None,
            "hash",
        ),
        1,
        {"exact": 1},
    )

    class Search:
        embedder = SimpleNamespace(warm=lambda: None, device="test")

        def __init__(self, *_):
            pass

        def query(self, query, **kwargs):
            # Relevance labels must never be an input to retrieval.
            assert set(kwargs) == {"limit"}
            queries.append(query)
            return [hit]

        def close(self):
            pass

    monkeypatch.setattr(devstill.search, "HybridSearch", Search)
    args = Namespace(
        config=tmp_path / "config.json",
        dataset=data_path,
        split="dev",
        allow_seed=True,
        limit=12,
        repeats=2,
        mode="hybrid",
        order_seed=42,
        output=tmp_path / "run",
    )
    runner.run(args)
    report = json.loads((args.output / "report.json").read_text())
    assert report["summary"]["metrics"]["evidence_hit_at_5_chunks"] == 1
    assert report["queries"][0]["ranking_stable"]
    assert report["status"] == "pilot"
    assert len(queries) == 3
    assert sorted(p.name for p in repo.iterdir()) == ["sample.py"]
    with pytest.raises(FileExistsError):
        runner.run(args)
    args.output = tmp_path / "second"
    args.allow_seed = False
    with pytest.raises(ValueError, match="Unreviewed labels"):
        runner.run(args)
    args.allow_seed = True
    monkeypatch.setattr(
        runner,
        "index_identity",
        lambda _: (DevstillConfig(), repo, index, {"changed": True}),
    )
    with pytest.raises(ValueError, match="Index fingerprint changed"):
        runner.run(args)


def test_model_fingerprint_detects_replaced_weights_at_same_path(tmp_path):
    weights = tmp_path / "model.safetensors"
    weights.write_bytes(b"first model")
    before = runner.tree_digest(tmp_path)
    weights.write_bytes(b"other model")
    assert runner.tree_digest(tmp_path) != before


def test_index_lifecycle_records_truncation_and_refuses_reuse(tmp_path, monkeypatch):
    import devstill.indexer

    root = tmp_path / "index"
    repo = tmp_path / "repo"
    repo.mkdir()
    config = DevstillConfig()
    identity = {"corpus_commit": runner.CORPUS["commit"]}
    monkeypatch.setattr(
        runner, "index_identity", lambda _: (config, repo, root, identity)
    )
    monkeypatch.setattr(runner, "check_repo", lambda *_: None)
    closed = []

    class Indexer:
        embedder = SimpleNamespace(
            warm=lambda: SimpleNamespace(
                tokenizer=lambda _, **kwargs: {"input_ids": [[1] * 100, [1] * 2100]}
            ),
            device="test",
        )
        store = SimpleNamespace(
            all_search_units=lambda: [
                SimpleNamespace(embedding_text="a"),
                SimpleNamespace(embedding_text="b"),
            ]
        )

        def __init__(self, *_):
            (root / "metadata.sqlite").touch()

        def run(self):
            return SimpleNamespace(to_dict=lambda: {"search_units_embedded": 2})

        def close(self):
            closed.append(True)

    monkeypatch.setattr(devstill.indexer, "RepositoryIndexer", Indexer)
    args = Namespace(config=tmp_path / "config.json")
    runner.build_index(args)
    manifest = json.loads((root / "benchmark-index.json").read_text())
    assert manifest["embedding_inputs"] == 2
    assert manifest["truncated_embedding_inputs"] == 1
    assert closed == [True]
    with pytest.raises(ValueError, match="Index already exists"):
        runner.build_index(args)
