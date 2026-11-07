# Task workflow versus legacy Plan/Pi: measured comparison

Run 2026-11-07. **The new workflow is not an overall improvement yet.** It reduced generation turns on successful coding cases and improved Cargo context coverage with a larger allowance, but introduced definition failures and increased review latency. Feature retrieval on Werkzeug did not improve. Fix the task-definition failures before promoting this as the preferred feature workflow.

## Protocol and limits

“Old” means the existing scoped hybrid retrieval, adaptive context packing, file-impact Plan and Pi execution immediately preceding the task workflow. It includes the prior deterministic retrieval improvements; it is not the earlier 60%-coverage baseline.

Both methods used the same configured Qwen-compatible endpoint, model identifier, pinned corpus revisions and frozen embedding indexes. No production source changed during the campaigns; final source hashes match their initial hashes. Original read-only corpora stayed untouched. Queries, contexts, outputs, failures, timings and usage are retained under the artifact directories below.

Definition comparison: twelve exposed Werkzeug change probes and twelve source-inspected Cargo development lookups. Old uses pool 12/max five chunks; Task uses pool 24/max eight. Both have an 8192-token full-input ceiling and 4096-token output reservation/cap. A separate selection diagnostic holds the context cap at five chunks and the serialized-context token cap at 2048 for small edits or 4096 for features/Cargo. Native operating points therefore differ in pool/chunk policy; gains cannot be attributed to the task schema alone.

Variant order alternated by query. Each query/variant had an untimed search warmup and seven timed warm searches; each definition was generated once. Review time sums median warm search, native selection/tokenizer time and definition time, excludes human confirmation and encoder startup, and includes failed attempts. Task selection is also evaluated diagnostically before calling the production definition function; its repeated packing reuses the tokenizer cache, so the measured total includes a small extra packing traversal. The server's sampling defaults were retained. These samples do not establish model-output variance or reliable production p95 latency.

The local embedding accelerator was an NVIDIA GeForce RTX 4080 Laptop GPU, driver 576.88, 12282 MiB VRAM. CUDA indexing/search ran in the installed environment. The model endpoint is remote on the user's LAN; its hardware/load was not independently recorded. The GPU snapshot was collected after the runs, not continuous load monitoring.

## Retrieval and review results

Coverage is mean labeled evidence-group coverage in selected context. Complete means all labeled groups are present. “Usable complete” additionally requires a valid definition and no unresolved Search/blocker entries. It is a minimum-evidence diagnostic, not proof a plan or patch implements the feature.

| Workload | Measure | Old | New Task |
| --- | --- | --- | --- |
| Werkzeug small edits, 5 | Native evidence coverage | 90% | 90% |
| | Complete contexts before definition validation | 4/5 | 4/5 |
| | Valid definitions | 5/5 | 3/5 |
| | Usable complete reviews | 4/5 | 3/5 |
| | Median request-to-review time | 2.24 s | 8.25 s |
| Werkzeug features, 5 | Native evidence coverage | 65% | 63.3% |
| | Complete contexts | 0/5 | 0/5 |
| | Valid definitions | 5/5 | 3/5 |
| | Median request-to-review time | 3.12 s | 15.10 s |
| Cargo lookups, 12 | Native evidence coverage | 58.3% | 75% |
| | Complete contexts | 7/12 | 9/12 |
| | Valid definitions | 12/12 | 11/12 |
| | Usable complete reviews | 7/12 | 9/12 |
| | Median request-to-review time | 2.98 s | 6.71 s |

The remaining Werkzeug probes are a new middleware capability and an absent-capability lookup. Neither method retrieved the labeled middleware anchors; Task rejected an unretrieved path, while old Plan produced a valid file-impact output. For the absent-capability probe, Task explicitly left an unresolved item; old Plan produced no Search row. This is useful evidence-status behavior, not a calibrated abstention score.

The equal-context diagnostic was 90% small-edit coverage for both; feature coverage was 65% old versus 58.3% Task; Cargo was **7/12 complete for both**. Cargo's native improvement appears with the larger context allowance and is not evidence that task decomposition itself improves retrieval.

Response size contributes to latency. Median definition output tokens for small edits were 171 old versus 411 Task; for features, 213 versus 1121; for Cargo, 174 versus 406.5. Task prompts also repeat structured outcomes, acceptance conditions and contracts. Review time increased roughly 3.7× for small edits, 4.8× for features and 2.3× for Cargo in these single-generation samples.

Warm search medians across per-query medians: Werkzeug small edits 28.3 ms old versus 69.4 ms Task; features 47.3 versus 73.7 ms; Cargo 90.1 versus 93.1 ms. Cargo's p95 across its twelve query medians was 100.9 versus 103.4 ms. These are small warm-search distributions, excluding selection/tokenizer/model time, and should not be presented as production tail latency.

## Coding and end-to-end performance pilot

Four disclosed synthetic requests use tiny real Git repositories with held-out acceptance scripts: a CSS button change, an empty-name fallback, a backend/route option, and independent backend/frontend helper changes. Each variant starts from identical source. Acceptance scripts are excluded from retrieval and are executed independently after the method returns, including after reported failures. No source from the actual benchmark corpora was patched.

The native old and new policies use equal 4096-token per-generation caps, with old Pi limited to twelve tools/60 seconds. Aggregate generation/token budgets differ. This measures native operating points, not equal-total-budget superiority. No run approached the Pi timeout.

| Request | Old acceptance | New acceptance | Old total time | New total time | Logical generation turns old/new |
| --- | --- | --- | --- | --- | --- |
| Submit button color | Pass | Fail: CSS rule used as a file target | 5.15 s | 3.46 s | 4 / 1 |
| Empty-name fallback | Pass | Pass | 6.15 s | 4.67 s | 4 / 2 |
| Backend and route option | Pass | Pass | 8.35 s | 9.17 s | 5 / 2 |
| Independent helper changes | Pass | Fail: nonexistent test path treated as existing | 7.73 s | 6.67 s | 4 / 1 |
| **Acceptance total** | **4/4** | **2/4** | | | |

The new method's all-case median, 5.67 seconds versus old 6.94, includes two early failures. It must **not** be called a speed improvement at equivalent success. On the two shared successful cases, one became faster and the other slower. The successful backend/route change used one Task; this does not establish successful multi-task decomposition on feature work.

Old generation counts combine the direct Plan request with assistant turns from Pi's event stream. Transparent provider HTTP retries inside Pi are not independently counted. Pi reported zero token usage for every assistant message, so **a complete old-versus-new coding token comparison is unavailable**. Direct request usage is stored, but old totals must not be inferred from Plan-only usage or treated as zero. Definition-token comparisons above are measured for both methods.

Concurrency performance was not measured against live Qwen here. The nominal independent-feature case failed during new definition validation, before parallel scheduling could matter. Existing automated scheduler tests remain the evidence for concurrent readiness/correctness.

## Failure diagnosis and prioritized follow-up

1. **Bind impact targets to exact file identities.** Task outputs included a CSS rule, a path with `::test_name`, and nonexistent tests as existing paths. Supply an explicit path inventory, constrain existing targets in the schema where possible, and distinguish proposed New files from existing Read/Modify files. Keep rejection of unapproved targets.
2. **Use file/source fingerprints for staleness.** `edit_import` incorrectly failed with stale `src/werkzeug/urls.py`. Its indexed `_codec_error_url_quote` snippet omits a trailing `# type: ignore` that remains in the clean pinned file. Literal chunk-versus-file text equality confuses parser normalization with an actual source change. Do not weaken actual changed-file detection.
3. **Keep small edits cohesive and declarations consistent.** The getlist request split implementation/tests into tasks sharing a file without declaring a dependency. Improve decomposition/contract generation and check existing-file ownership before execution. Merely removing the conflict check would hide the defect.
4. **Reduce definition verbosity.** Most successful small edits need an outcome, compact impact list and a check; repeating them in title/instruction/acceptance adds tokens and latency. Benchmark a compact schema while preserving concrete interfaces for genuinely dependent tasks.
5. **Repair feature evidence discovery/selection.** Returning more candidates alone did not improve Werkzeug features, and neither native method produced complete feature context. Keep request-to-parser, converter-registration and source-to-test retrieval experiments on the roadmap.
6. **Add complete usage telemetry to Pi before equal-cost claims.** Then compare aggregate token/call budgets and successful-feature latency. Keep invalid/blocked requests in the headline denominator.

These are proposed next changes. The campaigns froze the implementation and did not tune production behavior against these outputs. All sets are now exposed development evidence. Use new independently reviewed families before any reliability claim.

## Artifacts and reproduction

- `.devstill/task-comparison-v1/`: all 24 Werkzeug variant rows, definition logs, selected contexts, matched/native scores, summaries and provenance. The initial combined process stopped before Cargo because the legacy dataset validator assumed Werkzeug; completed rows were preserved and summarized without regeneration.
- `.devstill/task-comparison-cargo-v1/`: all 24 Cargo variant rows after the benchmark adapter selected the correct corpus pin. Source/hash validation still ran. No Werkzeug requests were repeated.
- `.devstill/task-coding-comparison-v1/`: eight execution results, independently scored acceptance, generation-turn summaries and disclosed fixture/source provenance. Temporary variant repositories and their model/event logs are referenced by each result.
- Every directory has `final-audit.json` recording unchanged production source and final benchmark-script hashes.

```bash
export PYTHONPATH="$PWD/src"
BENCH_PY="$HOME/.local/share/devstill/venv/bin/python"
"$BENCH_PY" -m benchmarks.retrieval.task_comparison \
  --corpus werkzeug --output .devstill/task-compare-werkzeug-fresh
"$BENCH_PY" -m benchmarks.retrieval.task_comparison \
  --corpus cargo --output .devstill/task-compare-cargo-fresh
"$BENCH_PY" -m benchmarks.retrieval.task_coding_comparison \
  --output .devstill/task-coding-fresh
```

Run serially to avoid local/model contention. Output directories are immutable. The corpus indexes must already exist at the documented temporary paths. Review and acceptance requests are live model calls; output variance is expected. Transfer ignored artifacts and temporary coding repositories with the handoff if needed. Eleven benchmark correctness tests and benchmark-script lint passed; no production change was made for this comparison.
