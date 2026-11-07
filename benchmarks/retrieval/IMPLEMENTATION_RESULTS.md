# Deterministic retrieval implementation and evaluation

The first implementation stage of [NEXT_PLAN.md](NEXT_PLAN.md) is implemented and tested. Small-edit context improved substantially. Feature evidence improved, but the release gates for complete features, new capabilities and multiple concurrent sessions remain open. This document records measured development results, not independent acceptance certification.

## Implemented behavior

- Path, kind and permanent exclusions constrain lexical, exact, prefix, fuzzy and vector candidate retrieval **before** channel limits. The same scope applies to explicit symbol lookups and overload resolution. SQLite uses bound parameters; large ID/path sets use JSON tables instead of exceeding SQL parameter limits.
- Natural requests containing dotted symbols, snake-case names or backtick identifiers contribute bounded exact lookups. An implementation request receives the uniquely resolved Python overload body before file quotas. Explicit typing/signature requests retain declarations; ambiguous implementations remain unresolved rather than choosing an arbitrary body.
- CLI Plan and initial Deep Plan, TUI Plan/Deep Plan/refinement, and TUI context recognize explicit requests for tests or regression assertions. The existing explicit CLI include-tests option remains supported.
- Adaptive context selection removes near-overlapping ranges before quotas, scans past unsupported/oversized candidates, and backfills from the remaining eligible pool. Competitive focused methods replace enclosing classes in the original rank slot. Explicit class anchors remain intact.
- Initial CLI/TUI planning can pack the entire retrieved pool against a **full chat prompt** counted by the served model's tokenizer. Counts include system instructions, request, source serialization and generation framing. Output tokens and a 256-token margin are reserved. Every generation request, including repairs and Deep Plan, is checked against the configured and tokenizer-reported context window. Deep Plan/refinement do not yet repack against their full expanded history; they fail explicitly if their requests exceed the window.

Budgeting is opt-in because `/tokenize` is a server extension. The local `devstill.json` now sets `llm.context_window` to the verified server value, 262144, and `llm.planning_input_tokens` to 8192. A configured tokenizer failure does not silently fall back to character estimates. Other compatible servers keep the existing behavior until a supported tokenizer and context window are configured. Supplying an input budget without a context window is rejected.

No automatic workload router, relationship graph or additional Qwen query-rewrite layer was installed. These require the later controlled stages; original Deep Plan follow-up searches remain available.

## Controlled Werkzeug comparison

The starting source tree was saved before implementation at `.devstill/retrieval-implementation-v3/baseline/devstill/`, including the user's existing changes. The comparison loads the saved search, SQLite query implementation and context selector beside the current code. Both use the same frozen vectors and corpus. No index writes occur during these comparisons. Embedding/chunk-building behavior was not changed.

The four stages are `baseline`, `test_eligibility`, `scoped_anchors`, and `packed`. Pool size stays 12 and context limit stays five chunks. Context budgets remain 2048 for small edits and 4096 for feature work, counted using the served Qwen tokenizer. These benchmark budgets apply to serialized context; the separate planning track below tests the full prompt. Gold evidence and workload labels never enter production selection. The benchmark still uses known workload labels for budget selection, so it does not evaluate a router.

Authoritative change-probe output: `.devstill/retrieval-implementation-v3/matched-final/`.

| Development workload | Starting baseline | Implemented selection |
| --- | --- | --- |
| Five small edits: mean evidence coverage | 60% | **90%** |
| Five small edits: complete contexts | 1/5 | **4/5** |
| Small edits: median context tokens | 1545 | **1396** |
| Five features: mean evidence coverage | 50% | **65%** |
| Five features: complete contexts | 0/5 | **0/5** |
| Features: median context tokens | 2426 | 2623 |

Test eligibility alone accounts for the small-edit coverage improvement and raises feature coverage to 58.3%. Exact anchors improve small-edit evidence rank; the targeted overload diagnostic now retrieves the implementation. Packing/focused selection raises feature coverage further to 65%. No embedding-model improvement is claimed.

The final candidate's warm search p95 across the five per-query medians was approximately 24 ms for small edits and 29 ms for features. Queries were warmed, timed three times and engine order rotated by task. These tiny sets are development diagnostics, not a reliable tail-latency distribution. Selection/tokenizer HTTP time and model generation are outside search timing; raw samples and selection times remain in the reports.

The existing 60-case set is now an **exposed regression set**. Authoritative output: `.devstill/retrieval-implementation-v3/regression-final/`. Among its 50 answerable cases:

| Metric | Starting baseline | Implemented selection |
| --- | --- | --- |
| Evidence hit in first five chunks | 47/50 | 47/50 |
| Mean final-context evidence coverage | 79% | **85%** |
| Complete contexts | 38/50 | **41/50** |

No individual answerable case loses context evidence coverage against the starting baseline. All ten negative cases still receive context: ranking changes do not provide calibrated abstention.

The first packing revision regressed `w12`: dropping a useful parent class allowed an unrelated class fragment to consume the budget before the focused range-processing method. The corrected selection puts the method in its parent's original slot. A focused regression test reproduces the failure, and the final 60-case run confirms recovery. Earlier runs remain immutable under `matched-r1`, `matched-r2` and `regression-60`. The first regression run also exposed a report-aggregation bug mixing positive and negative metrics; `regression-final` separates their workload groups. Its overlapping GPU timings are not used for the final comparison.

## Larger Rust corpus

The second prepared corpus is [Cargo 0.83.0](https://github.com/rust-lang/cargo/tree/8f40fc59fb0c8df91c97405785197f3c630304ea), pinned to `8f40fc59fb0c8df91c97405785197f3c630304ea`. The manifest is [corpus.cargo.json](corpus.cargo.json). It has 2574 tracked entries, 2458 regular files, 2440 text files and 1118 Rust files, with approximately 15.2 MB of regular-file content. Symlinks and fixture directory entries are counted separately from readable regular files. The checkout is `/tmp/devstill-retrieval-bench/repos/cargo-0.83.0`.

Clap 4.5.20 was also inspected and left in the temporary workspace, but its 590 text files fell below the intended scale. Cargo was selected after measuring the inventories. Neither project's application was installed or built.

The initial Cargo index produced 15384 search units from 1856 eligible files in 216.3 seconds, including embedding-token audit. There were 472 truncated embedding inputs. The index reported 414 parser diagnostics and ten BAD files. All ten BAD files are Rustfix recorded JSON fixtures; 400 diagnostics are under Cargo's testsuite fixtures. Two diagnostics also affect Rust source in `cargo-util-schemas` and need follow-up. The detailed paths are saved in `.devstill/retrieval-implementation-v3/cargo-parser-review.json`. Do not dismiss all diagnostics as harmless fixtures.

[cargo.pilot.v1.json](cargo.pilot.v1.json) contains 12 source-inspected development questions in three related subsystem families. The initial pilot found a relevant file in 12/12 cases, evidence in its top five chunks in 8/12, and complete context in 8/12. Returned-pool evidence recall was 91.7%, again showing a gap between available and selected evidence. Warm retrieval median was 49.9 ms and p95 was 58.5 ms. The pilot has no Qwen token cap or independent relevance review; it is not directly comparable to the token-budgeted Werkzeug table.

The initial Rust run preceded the final parent-priority correction. Its source hash/diff are preserved in the report. A separate `cargo-final` profile rebuilt the index for final-source verification in 216.1 seconds; the runner deliberately rejects stale source fingerprints. This conservative fingerprint includes whole source files, even when a query-only change leaves vectors semantically unchanged.

**Final-source Cargo pilot:** 11/12 file hit@5, **7/12 evidence hit@5 and complete contexts**, and 83.3% returned-pool evidence recall. Warm median was 50.0 ms and p95 61.0 ms. These replace the initial pilot values when describing final-source behavior. The authoritative report is `.devstill/retrieval-implementation-v3/cargo-pilot-final/`.

This rebuild exposed an index-stability failure. Every embedding-input content hash matched between builds, and five sampled stored vectors were identical. With the **same query vector**, the required `Dependency.name_in_toml` chunk ranked 33rd under exhaustive cosine. The first approximate index returned it at rank 33; the final index omitted it from its 50 candidates. The two approximate candidate sets shared 48/50 entries. This directly demonstrates a missed approximate candidate; it is not evidence that a different embedding model is necessary. Full-index vector equality was not established. Next, test approximate-search depth/construction settings against an exhaustive-vector diagnostic before changing embeddings. Preserve both builds to measure stability, rather than relying only on repeated queries against one index.

The diagnostic is [ann_probe.py](ann_probe.py), with measured output in `.devstill/retrieval-implementation-v3/cargo-ann-probe.json` and the paired-build checks in `cargo-index-stability.json`.

Three fresh-process measurements of a Cargo query showed about 0.51 seconds median setup, 2.94 seconds first search including encoder loading, and 47 ms median subsequent warm search. Median process lifetime was 4.37 seconds including three extra warm queries and cleanup. Median peak process RSS was approximately 1.75 GiB. OS/model-file caches may be warm: these are process-start measurements, not disk-cold measurements. [latency_probe.py](latency_probe.py) separates them; raw results are in `cargo-process-latency.json`.

## Qwen planning and budget verification

The configured `vllm-qwen38-27b` server completed all 12 change-probe planning requests with valid schemas and paths. Actual prompt usage ranged from 1232 to 5337 tokens, below the configured 8192-token initial-planning input cap. Recounting each full prompt with the serving tokenizer matched the reported prompt usage **exactly in 12/12 requests**. This directly checks serialization/chat-framing consistency, beyond counting context JSON alone.

Artifacts are `.devstill/retrieval-implementation-v3/qwen-plans/`, including plans, response logs and `tokenizer-verification.json`. [planning_campaign.py](planning_campaign.py) reproduces planning from saved candidate pools without embedding calls or code execution.

Semantic review still finds important omissions: the multipart-limit plan does not include parser enforcement or a search for it. The absent OAuth request does produce a Search row rather than asserting that an implementation was found, but it also reads irrelevant neighboring files. The new middleware plan requests another search for middleware examples. Valid schemas and in-budget prompts therefore do not establish sufficient plans or correct patches.

## Validation and release decision

The final full test suite passed **383 tests**, with one CUDA-only test skipped in the CPU development environment. Real indexing/retrieval ran with CUDA. Lint and diff-whitespace checks passed. New correctness fixtures cover candidate starvation across all four search modes, kind filtering before semantic limits, scoped implementation resolution, preservation of typing declarations, overlap/budget backfill, original anchor priority and full-prompt overflow. A transient TUI teardown failure in an earlier suite run passed on its isolated retry and on both subsequent full-suite runs; no unrelated TUI lifecycle patch was made.

Retain this deterministic implementation for further evaluation. The feature release gate is still failed: 65% coverage and zero complete contexts on five design tasks fall short of the plan's proposed 90%/80% targets. Small edits also require a larger independent set before asserting the proposed 90% completeness gate. Fast retrieval and good file-hit scores are insufficient.

The remaining stages are explicit:

| NEXT_PLAN stage | State / next evidence needed |
| --- | --- |
| Review exposed labels and reproduce failures | Completed as a new source/output review; this is still not independent relevance adjudication. |
| Scoped candidates, exact references, overload handling, requested tests, context packing | Implemented; controlled development comparisons and correctness fixtures pass. |
| Full-prompt budgeting | Initial CLI/TUI plans packed and server usage verified; all generation requests guarded. Expanded Deep Plan/refinement still need budget-aware repacking. |
| Larger supported-language corpus | Prepared, indexed and piloted on Cargo; final-source verification has a separate profile/report. |
| Approximate-index rebuild stability | Newly demonstrated failure: exact-cosine rank 33 omitted by one approximate build. Test bounded ANN search-depth/construction settings, fixed seeds where supported, quality and latency before claiming stability. |
| Fresh 30 small + 30 feature + 10 new + 10 negative acceptance families | Still needed, independently authored/reviewed and frozen before another tuning cycle. Existing sets must not be renamed held-out. |
| Automatic intent routing and missing-role decisions | Pending; do not silently use benchmark workload labels at runtime. |
| Typed bounded relationship expansion | Next justified implementation experiment: request-to-parser plumbing, source-to-regression tests and original-anchor retention. Prove gains under fixed budgets before enabling generally. |
| Qwen rewrites / alternative embedding / reranker | Deferred until candidate/selection failure evidence justifies each isolated experiment. The earlier rewrites caused semantic drift. |
| Index invalidation, process-safe mutations, per-session artifacts, recovery | Existing release prerequisites remain open; this change does not certify multiple simultaneous sessions. |

The missing acceptance review and remaining release gates are recorded, rather than treating the exposed 12/60-case sets as new independent evidence. The next implementation should target the demonstrated missing feature roles, then seek independent acceptance evaluation.

## Reproduce

Use unique output directories and run CUDA experiments serially. Preserve `.devstill/retrieval-implementation-v3/baseline/` with the reports when transferring the campaign.

```bash
export PYTHONPATH="$PWD/src"
BENCH_PY="$HOME/.local/share/devstill/venv/bin/python"

"$BENCH_PY" -m benchmarks.retrieval.deterministic_campaign \
  --baseline .devstill/retrieval-implementation-v3/baseline/devstill \
  --dataset benchmarks/retrieval/werkzeug.changes.v2_1.json \
  --output .devstill/retrieval-implementation-v3/recheck-changes

# Same comparison on the exposed regression set.
"$BENCH_PY" -m benchmarks.retrieval.deterministic_campaign \
  --baseline .devstill/retrieval-implementation-v3/baseline/devstill \
  --dataset benchmarks/retrieval/werkzeug.reviewed.v1.json \
  --output .devstill/retrieval-implementation-v3/recheck-regression

# A custom corpus always needs explicit labels; the default stays Werkzeug.
"$BENCH_PY" -m benchmarks.retrieval.runner \
  --corpus-manifest benchmarks/retrieval/corpus.cargo.json \
  prepare --profile cargo-recheck --device cuda
"$BENCH_PY" -m benchmarks.retrieval.runner \
  --corpus-manifest benchmarks/retrieval/corpus.cargo.json \
  index --config /tmp/devstill-retrieval-bench/configs/cargo-recheck.json
"$BENCH_PY" -m benchmarks.retrieval.runner \
  --corpus-manifest benchmarks/retrieval/corpus.cargo.json \
  run --config /tmp/devstill-retrieval-bench/configs/cargo-recheck.json \
  --dataset benchmarks/retrieval/cargo.pilot.v1.json --allow-seed \
  --repeats 3 --output .devstill/retrieval-implementation-v3/recheck-cargo

.venv/bin/python -m benchmarks.retrieval.planning_campaign \
  --input .devstill/retrieval-implementation-v3/matched-final/queries.jsonl \
  --output .devstill/retrieval-implementation-v3/recheck-plans

"$BENCH_PY" -m benchmarks.retrieval.ann_probe \
  --config /tmp/devstill-retrieval-bench/configs/cargo-final.json \
  --query 'Where does a dependency choose the TOML alias name rather than the actual package name?' \
  --target-chunk-id src/cargo/core/dependency.rs::Dependency.name_in_toml::method \
  --output .devstill/retrieval-implementation-v3/recheck-ann.json

"$BENCH_PY" -m benchmarks.retrieval.latency_probe \
  --config /tmp/devstill-retrieval-bench/configs/cargo-final.json \
  --query 'Find the target output directory override and its fallback to the default directory.' \
  --output .devstill/retrieval-implementation-v3/recheck-process-latency.json

.venv/bin/python -m pytest -q
.venv/bin/python -m ruff check .
git diff --check
```
