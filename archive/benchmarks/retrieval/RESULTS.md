> Archived historical document. Statements and commands describe the workflow at that time. Use the [active test plan](../../../benchmarks/retrieval/TEST_PLAN.md) for current work. Original path: `benchmarks/retrieval/RESULTS.md`.

# First retrieval campaign results

The first campaign supports a small query-ranking change: apply fuzzy symbol matching only to queries of at most three whitespace-separated words. Longer questions still use exact, prefix, path, full-text and semantic retrieval. Short identifiers and typos keep fuzzy matching. The change improves development evidence coverage and measured warm latency while preserving held-out file and evidence hits. Keep it for review; this is one Python corpus, not a general code-search quality claim.

## Dataset and runtime

Werkzeug 3.1.3 is pinned to `6389612fd1ee1bd93579eed5026e8fd471d04abd`. `werkzeug.reviewed.v1.json` contains 30 development and 30 held-out questions, each split with 25 answerable and five unanswerable questions. Related families stay within one split. Source spans and hashes were reviewed before retrieval outputs. This was a same-author review, not independent adjudication.

The held-out positives mostly concern individual implementation locations. They do not establish performance on multi-file coding tasks. Its negative questions concern absent integrations and are relatively easy absence cases. Future evaluation should include harder negatives, independently authored multi-file tasks, and a second language.

The CUDA index contains 3,956 search units from 265 eligible files. Indexing plus token audit took 40.6 seconds. Nine inputs exceeded the configured 2,048 embedding-token limit. There were 14 parser diagnostics, 41 MEH files and zero BAD files; diagnostics and fallback recovery deserve inspection before drawing conclusions about parser coverage.

Runtime: RTX 4080 Laptop GPU, driver 576.88, PyTorch 2.11.0+cu128, Transformers 4.57.6, Sentence Transformers 5.7.0, Chroma 1.5.9. Queries retrieve 12 chunks; primary metrics use the first five. Timings exclude warmup and use the median of three repetitions per query. P95 is over those per-query medians. These are campaign measurements, not guaranteed latency limits.

## Development results

| Variant | File hit at five | Evidence hit at five | Context evidence recall | Complete context | Median / p95 ms |
| --- | --- | --- | --- | --- | --- |
| Existing hybrid | 24/25 | 22/25 | 78% | 18/25 | 35.4 / 48.2 |
| Semantic only | 25/25 | 21/25 | 84% | 20/25 | 12.5 / 13.1 |
| No vector | 21/25 | 20/25 | 72% | 17/25 | 19.3 / 36.1 |
| Hybrid with no prose fuzzy matching | 24/25 | 23/25 | 82% | 19/25 | 23.6 / 26.1 |

The no-vector values use the confirmation run `baseline-dev-no_vector-confirm`, collected with original source restored. It replaces the first no-vector run as the authoritative baseline to remove ambiguity about source provenance while the first candidate was being prepared. The runner now rejects source changes during a run.

The candidate wins on w18, where fuzzy matches previously promoted unrelated `Pagination.previous` examples over dictionary append behavior. There are no development evidence-hit regressions. The family bootstrap interval for the four-percentage-point evidence-hit improvement includes zero (0 to approximately 11.5 percentage points); this small set does not establish a statistically decisive improvement.

## Held-out comparison

The candidate was frozen using development results before these scores were inspected. No additional retrieval changes were made after viewing them.

| Metric | Existing hybrid | Candidate |
| --- | --- | --- |
| File hit at five | 25/25 | 25/25 |
| Evidence hit at five | 24/25 | 24/25 |
| Complete selected context | 23/25 | 23/25 |
| Evidence reciprocal rank | 0.691 | 0.751 |
| Median / p95 warm latency | 28.8 / 36.6 ms | 20.0 / 22.7 ms |

Both pass the provisional file/evidence thresholds on this set. The held-out evidence-hit delta is zero; the justification for keeping the candidate is better development retrieval, earlier held-out evidence ranking, and lower measured latency with no observed hit-rate loss. Further tuning needs new held-out questions.

All five unanswerable questions in each split received nonempty hits and context under both hybrid variants. The current stack has no demonstrated abstention behavior. Do not interpret a returned chunk as proof that the requested feature exists.

## Failure ledger and next experiments

| Case | Observed failure | Next bounded investigation |
| --- | --- | --- |
| w04 | File serving found, but required path-safety implementation absent from the returned pool. | Inspect channel candidates and test one-hop call expansion under a fixed final budget. |
| w10 | Both required implementations appear in the pool; the final context loses one. | Context selection and redundancy, before replacing the embedding model. |
| w11 | Response-range handling found; required header parsing not fully covered. | Candidate retrieval versus representation audit. |
| w14 | Requested filename tests rank too low. | Test-intent routing; avoid globally removing tests. |
| w22 | Related import-error class dominates while the import implementation is missing. | Candidate instrumentation or bounded local reranking. |
| w27 | Overload declarations and implementation consume repeated slots; final context loses the implementation. | Prefer implementation over overload stubs, deduplicate before per-file caps. |
| All negative cases | Plausible-looking context is always returned. | Calibrate abstention on development positives and hard negatives; validate on new held-out cases. |

The small fuzzy gate does not resolve these failures. Do not launch the remaining hypothesis list as an uncontrolled sweep. Context-selection diagnostics and overload handling are the next cheap steps. Larger candidate pools, reranking, alternative embeddings, and relationship expansion remain separate experiments requiring their own frozen source and cost measurements.

## Qwen planning checks

Five development questions were sent through the configured `vllm-qwen38-27b` planner using saved candidate context. All five produced valid structured plans, with existing files labeled Read as requested. Four had no unresolved Search rows; w22 retained a Search row when retrieval evidence was absent. Model elapsed times were approximately 3.3–4.5 seconds. Retrieved evidence recall for the five checks was 1, 1, 1, 0.5 and 0 respectively.

These were review-only planning checks. They establish connectivity and a valid advisory handoff, not implementation correctness or general planning accuracy. Raw response usage and repair attempts are retained in `planning-agent.jsonl`; model time is separate from local retrieval latency.

## Artifacts and verification

Persistent local artifacts are under `.devstill/benchmark-campaign/`: complete run reports and snippets, config and dataset snapshots, baseline/candidate source snapshots, hardware details, index audit, failure ledger, paired comparisons, the original Devstill smoke-set recheck, and Qwen plan results/logs. Temporary corpus and index remain under `/tmp/devstill-retrieval-bench/`. The full reports are ignored generated data; this results document and reviewed dataset are versionable.

The production change is limited to `search.py` plus a regression test verifying that prose skips fuzzy lookup and short typos retain it. The benchmark runner additionally checks source stability during a run. Verification: 361 tests passed, one CUDA-only test skipped in the CPU development environment; lint passed. Real indexing and retrieval ran on the CUDA environment. Existing user changes to model-context detection and configuration were preserved.
