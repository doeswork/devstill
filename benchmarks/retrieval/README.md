# Retrieval benchmark plan and agent handoff

**Small execution diagnostic:** [four cases, three approaches, bounded run and decision gate](QUICK_DIAGNOSTIC.md). Compare native Pi, Pi with retrieved evidence, and a benchmark-only exact-replacement editor before resuming broad campaigns.
The [completed diagnostic and corrected-editor recheck](QUICK_DIAGNOSTIC_RESULTS.md) found 4/4 parity and a speed advantage for the revised editor on one known-case repetition; repeated and fresh-case validation remains required.

**Direct Pi baseline:** [stock Pi versus Quick YOLO, Quick Review and frozen Quick](PI_COMPARISON.md). The [four-way campaign](PI_FULL_RESULTS.md) was stopped at 48/288 attempts in favor of a four-case context diagnostic. Includes frozen source/runtime records, full model-call traces, paired correctness/latency reporting, and standards for development and fresh evaluation.
The [integration smoke results](PI_SMOKE_RESULTS.md) passed coding/acceptance and final integrity checks; they do not establish a speed or quality advantage.

**Current feature implementation:** [serial Quick composition, verification and benchmark commands](FEATURE_IMPLEMENTATION.md). The user selects Quick or Feature explicitly. Feature now tries Quick first and coordinates only unresolved reviews. The [completed comparisons and coordinator revision](FEATURE_COORDINATION_RESULTS.md) show working dependent tasks, but feature parity with Quick has not been achieved. [Design plan](FEATURE_TASK_PLAN.md).

**Current direction:** [Quick restored underneath Task Review/YOLO](QUICK_RESTORE_RESULTS.md). Small-edit parity comes first; feature decomposition is deferred. The Task v1/v2 results below describe the superseded structured-edit experiments.

**Latest 24-case coding comparison:** [144 attempts, failed parity gates, and per-request results](TASK_V2_RESULTS.md).

**Compact Task follow-up:** [runtime corrections and the 24-case coding protocol](TASK_V2_IMPLEMENTATION.md).

**Measured old/new comparison:** [task workflow results, performance and failure diagnosis](TASK_COMPARISON_RESULTS.md). The new workflow currently regresses review reliability and latency; feature context remains incomplete.

**Task workflow implementation:** [Task Review / YOLO, bounded task execution, tests and remaining evaluation](TASK_IMPLEMENTATION.md).

**Implementation results:** [deterministic retrieval changes, controlled measurements and remaining release gates](IMPLEMENTATION_RESULTS.md).

**Historical strategy:** [one-shot tasks composed into features](ONE_SHOT_STRATEGY.md). Its mandatory per-request Task Plan and separate coder were superseded. Use the explicit Quick/Feature plan above for the next implementation.

**Earlier design:** [small-edit and feature retrieval plan, tested prototypes, and implementation handoff](NEXT_PLAN.md). The original campaign and protocol below remain useful background.

Improve the evidence Devstill retrieves for repository questions and planning, while preserving fast local search. Start with a repeatable baseline, diagnose misses, then change one mechanism at a time. Production retrieval code is outside this package. The runner never calls Qwen, generates a plan, or executes a patch.

The existing 12-question Devstill check is a development regression set. Getting 11/12 or 12/12 is desirable, but each question changes the result by 8.3 percentage points. Tuning against those same questions cannot demonstrate general improvement. Also, finding the right file does not mean the selected chunk contains the answer.

## Prepared corpus and workspace

Use [Werkzeug](https://github.com/pallets/werkzeug/tree/3.1.3), the Python WSGI library, as the first external corpus. Its request handling, routing, middleware, parsing, utilities, documentation, and tests provide several distinct retrieval problems. The tag is a reproducibility choice, not a recommendation to deploy this version. [Project documentation](https://werkzeug.palletsprojects.com/en/stable/) describes the library's scope.

The pinned checkout is `6389612fd1ee1bd93579eed5026e8fd471d04abd` (tag `3.1.3`): 302 tracked files, 1,975,411 tracked bytes, and 21,366 Python source lines under `src/`. These counts were measured from the checkout. It is a manageable step up from Devstill without requiring the project to be installed or its tests to run.

```text
/tmp/devstill-retrieval-bench/
  repos/werkzeug-3.1.3/       # Clean pinned source; no benchmark files here
  configs/baseline.json      # Absolute repo and index paths; LLM disabled
  indexes/baseline/          # SQLite, Chroma, embedding audit, index fingerprint
  runs/baseline-hybrid/      # Immutable report, dataset, config and source diff
```

`/tmp` is disposable. Copy runs worth keeping to persistent storage before reboot or cleanup. `prepare --workspace /another/path` creates the same layout elsewhere. The versioned recipe, corpus pin, runner, and seeds live in this repository. An index under a different profile must have a different parent directory, because SQLite lives next to Chroma.

The first campaign is complete: see [campaign results](RESULTS.md). The pinned corpus, isolated index, 60 source-reviewed questions, three development baselines, and one held-out candidate comparison are available. The review was by the same author; independent relevance adjudication remains desirable. The original 20 seeds are retained unchanged for provenance.

## What to measure

Keep the retrieval pool at 12 chunks for the initial comparisons, matching the ordinary planning path. The first five chunks are the primary search cutoff. A separate diagnostic experiment may use a deeper pool, but its result must not be compared as though it used the same budget.

| Metric | Meaning |
| --- | --- |
| `file_hit_at_5_chunks` | At least one relevant file appears among the first five chunks. This matches the earlier 12-question convention. |
| `file_hit_at_5_unique_files_within_pool` | Relevant file among the first five distinct paths found in the returned pool. This is a separate, usually easier measure. |
| `evidence_hit_at_5_chunks` | The first five chunks completely cover at least one required evidence group. Primary experiment comparison metric. |
| `evidence_recall_at_5_chunks` | Fraction of required evidence groups completely covered by those chunks. |
| `evidence_recall_at_pool` | Evidence available in the returned pool; helps distinguish ranking failure from a broader retrieval miss. This is not raw pre-fusion candidate recall. |
| `evidence_mrr_at_pool` | Reciprocal earliest prefix length at which a required evidence group is covered. |
| `context_evidence_recall`, `context_complete` | Coverage after Devstill's normal adaptive selection, including test exclusion and file diversity. |
| Negative return rates | How often known-unanswerable queries still receive hits or context. Report separately; never include negatives in positive hit-rate denominators. |
| Latency and size | Median and p95 of per-query median warm latencies; selected source characters; separate encoder startup and index-plus-audit time. |

An evidence group is one required fact or implementation location. Its alternatives are acceptable substitutes. A multi-file task has multiple groups. All lines in one alternative must be covered, including through adjacent retrieved chunks. A hit on an unrelated function in the correct file is not evidence credit. Do not choose entire large functions when a short implementation span proves the fact.

Reports retain source snippets, ranges, symbols, channel scores, ranks, repeated timings, and ranking stability. Inspect failures, not only averages. `context_tokens` is deliberately null: characters are not tokens. Before model-facing budget experiments, add an offline tokenizer matching the served Qwen model and count the fully serialized prompt. The Jina embedding tokenizer is not a substitute for Qwen's tokenizer.

The runner reports `no_vector`, not “BM25 only”: Devstill's `lexical_only` flag currently includes exact, prefix, path, fuzzy, and full-text channels. A true lexical-only ablation needs its own implementation.

## Build and freeze the question set

1. Review `werkzeug.seed.json` against the pinned code. Check each query, rationale, span, and alternative. Correct incomplete labels before looking at retrieval outputs. The seeds cover natural language, exact symbols, misspellings, similar request/response symbols, path filters, multi-file tasks, test discovery, documentation, and provisional negative cases.
2. Expand to at least 60 questions: 30 development and 30 held-out, with roughly five unanswerable cases per split. Include exact and partial identifiers, natural-language behavior, multi-file changes, explicit tests/docs requests, filters, exclusions, and misleading near matches. A second author or reviewer should supply the held-out questions where possible.
3. Split by `family`: paraphrases, typos, and related questions about the same implementation stay together. Keep the existing exposed seeds in development. Do not create held-out paraphrases of them. New held-out families could cover contexts, cookies, dispatching, debugger behavior, encoders, or other code not represented in the development labels.
4. For each reviewed case, set `label_status` to `reviewed` and record the reviewer. Save a new dataset file with a new name/version; do not silently rewrite a dataset used by a saved run. `validate` checks identities, split leakage, evidence ranges and content hashes. It cannot decide whether the labels are semantically correct.
5. Freeze the file before experiments. Keep held-out results unopened during tuning. If later evidence exposes a label error, version the dataset and rerun every compared variant; do not revise only the failing candidate's labels.

The schema is demonstrated in the seed file: `id`, `family`, `category`, `split`, `label_status`, `reviewer`, `query`, `answerable`, `include_tests`, `filters`, `evidence_groups`, and `rationale`. Evidence paths are repository-relative; lines are inclusive and one-based. Hash the exact selected lines including existing line endings with SHA-256. Empty evidence groups are permitted only for unanswerable queries.

For real issue-derived questions, use the issue statement as the query and code at the pre-fix revision as the corpus. Inspect the fix separately for labeling. Do not feed the fix, changed filenames, or answer snippets into query generation/retrieval. This requires a separate pinned corpus manifest; the current runner intentionally supports only the prepared Werkzeug snapshot.

## Run the baseline

Run from the Devstill checkout. Use the installed CUDA environment with the checkout on `PYTHONPATH`; the development `.venv` is CPU-only on the reviewed machine. No new packages or model downloads are needed for the baseline.

```bash
export PYTHONPATH="$PWD/src"
BENCH_PY="$HOME/.local/share/devstill/venv/bin/python"
BENCH_ROOT=/tmp/devstill-retrieval-bench

"$BENCH_PY" -m benchmarks.retrieval.runner prepare --workspace "$BENCH_ROOT" --profile baseline --device cuda
"$BENCH_PY" -m benchmarks.retrieval.runner validate
"$BENCH_PY" -m benchmarks.retrieval.runner index --config "$BENCH_ROOT/configs/baseline.json"

# Optional seed pilot to verify hardware and inspect the reporting workflow.
# This produces a report marked pilot; it cannot establish a quality improvement.
"$BENCH_PY" -m benchmarks.retrieval.runner run \
  --config "$BENCH_ROOT/configs/baseline.json" --mode hybrid --allow-seed \
  --output "$BENCH_ROOT/runs/seed-pilot-hybrid"
```

After independent review and dataset expansion, use the reviewed file for the three baselines. Execute serially. Output directories must be new; the runner refuses to overwrite previous evidence.

```bash
BENCH_DATA=benchmarks/retrieval/werkzeug.reviewed.v1.json
"$BENCH_PY" -m benchmarks.retrieval.runner validate --dataset "$BENCH_DATA"

for BENCH_MODE in hybrid semantic no_vector; do
  "$BENCH_PY" -m benchmarks.retrieval.runner run \
    --config "$BENCH_ROOT/configs/baseline.json" --dataset "$BENCH_DATA" \
    --split dev --mode "$BENCH_MODE" --repeats 3 --limit 12 \
    --output "$BENCH_ROOT/runs/baseline-$BENCH_MODE"
done

"$BENCH_PY" -m benchmarks.retrieval.runner compare \
  "$BENCH_ROOT/runs/baseline-hybrid/report.json" \
  "$BENCH_ROOT/runs/baseline-semantic/report.json"
```

`werkzeug.reviewed.v1.json` is now supplied. Its first held-out evaluation has already been used for the candidate in `RESULTS.md`; do not use those results to tune another candidate and still call that set unseen. Seed labels are refused unless a development pilot explicitly uses `--allow-seed`.

The index fingerprint includes the corpus commit, local embedding model contents, embedding/chunking/file settings, indexing source, and relevant dependency versions. Changed fingerprints require a fresh profile and index; this avoids Devstill's existing embedding-invalidation gap. The benchmark lock serializes its own index access. Ordinary Devstill commands do not honor that lock, so do not run them against these indexes. The clean-corpus check rejects modifications or extra untracked files.

Keep hardware load comparable. Capture GPU model, driver, available memory, and background workloads with each campaign. Query timing excludes label scoring, evidence serialization, model hashing, index opening, encoder warmup, and one untimed repetition per query. Run variants in a rotated order across fresh processes to check order effects. A separate subprocess-per-query test is needed for CLI cold-start measurements; the runner measures a warm TUI-style session.

## Diagnose before changing retrieval

Classify each miss in a saved failure ledger: query ID, category, expected evidence, first returned relevant rank, chosen context, suspected stage, proposed fix, and outcome. Use one of these stages:

- **Inventory:** useful file was excluded, never parsed, or belongs outside this repository.
- **Representation:** the right source exists but the needed tail was truncated or a chunk mixes unrelated behavior. Check the index token audit.
- **Candidates:** no useful chunk reaches the returned pool. Inspect the raw channel candidates with experiment-only instrumentation before claiming a ranking problem.
- **Ranking:** useful chunks appear deeper in the pool but lose to repeated tests, docs, fuzzy matches, or duplicate symbols.
- **Context selection:** useful retrieved chunks disappear during test filtering, diversity selection, or the relevance-drop heuristic.
- **Labels:** more than one implementation can answer the query, or the required evidence is incomplete. Correct through a dataset version, not an experiment-specific exception.

Do not change expected paths to make a score look better. Use Qwen as an optional reviewer of a failure explanation only after deterministic scoring; it is not the sole relevance judge.

## Prioritized experiments

The first three baselines are supported by the runner. Everything below is a planned isolated change, not an already implemented flag. Create a branch/worktree or save an exact source patch for each variant. Keep the same corpus, queries, labels, result budget, and context policy except for the one factor under test.

| Order | Hypothesis and bounded change | Evidence to inspect | Cost |
| --- | --- | --- | --- |
| 1 | Route identifier-like queries to exact/prefix/fuzzy channels; reduce or disable fuzzy scoring for long natural-language queries. | Natural-language wins without losing exact/typo cases. | Low; query time only. |
| 2 | Simplify fusion to lexical plus semantic for prose, or lower fuzzy/prefix weights. Try a small predeclared weight grid, not an open-ended search. | Per-category evidence hit, MRR, and which channels promote distractors. | Low; query time only. |
| 3 | Apply path/kind/exclusion filters before channel limits; ensure enough eligible candidates survive. Add controlled synthetic starvation fixtures. | Scoped queries that previously returned no evidence; no excluded-file leakage. | Medium; store/query changes. |
| 4 | Add a genuine lexical-only baseline with identifier splitting and restrained query normalization. | Partial names and exact code phrases versus prose. | Low to medium; lexical index may need rebuilding. |
| 5 | Improve context selection: deduplicate overlapping snippets, preserve requested tests/docs, and enforce a Qwen token budget. | Pool recall versus final context recall, complete multi-file evidence, tokens. | Medium; avoid blanket test/doc downranking. |
| 6 | Retrieve a broader candidate set and rerank query/code pairs with a local cross-encoder. Start with 30–50 candidates, return the same five chunks. | Evidence gains versus added p95 latency and VRAM. | Medium; new local model. |
| 7 | Compare token-aware structural chunks and embedding inputs: enforce the Jina token limit, preserve parent/signature metadata, reduce repeated boilerplate. | Truncation rate, chunk evidence recall, index size/time. | Medium; fresh index for every variant. |
| 8 | Compare one alternative code embedding model against Jina using its documented prompts and normalization. | Evidence recall across categories and languages, load/index latency, memory. | Higher; separate model download and index. |
| 9 | Add bounded relationship expansion around the winning chunks, such as one caller, callee, import, or adjacent implementation. | Multi-file evidence completeness under the same final token budget. | Higher; graph extraction and selection. |
| 10 | Let Qwen generate at most two retrieval rewrites for ambiguous queries, then fuse results with the original query retained. | Extra relevant evidence versus drift, latency, and prompt/completion tokens. | Highest latency; separate model-assisted track. |

Start with experiments 1–3 only if their corresponding failure modes appear. Finish one, record results, then decide whether the next is justified. Do not implement the entire table before learning from the baseline.

Devstill already uses reciprocal-rank fusion. The [original RRF paper](https://plg.uwaterloo.ca/~gvcormac/cormacksigir09-rrf.pdf) supports treating fusion as a tunable ranking mechanism; it does not guarantee improvement for Devstill's channels. The [Sentence Transformers retrieve-and-rerank workflow](https://sbert.net/examples/sentence_transformer/applications/retrieve_rerank/README.html) provides the basis for experiment 6: retrieve cheaply first, then score a bounded candidate set jointly with the query. Whether either helps here must be measured on the frozen cases.

## Decision rules and final handoff

Before tuning, agree to provisional acceptance gates: at least 90% file hit@5 on answerable held-out cases, at least 85% evidence hit@5, perfect exact-symbol cases, and no unexplained category regression greater than five percentage points. For the fast retrieval path, use 150 ms warm p95 on the reviewed CUDA machine as an initial budget; model-assisted retrieval gets a separately declared budget. These are goals, not measured results or universal requirements. A miss caused by a bad label still requires a versioned dataset correction.

Use paired per-query wins/losses and the runner's family-clustered bootstrap interval, not repeated latency samples as extra questions. Small held-out sets still have wide uncertainty; report numerator/denominator and individual regressions. A candidate that moves 8/12 to 11/12 on the exposed Devstill set may be useful, but must also improve the external evidence metrics. Do not trade exact lookup or required context completeness for a better aggregate file score.

Select a candidate using development results only. Freeze its source/configuration, then evaluate that candidate and the unchanged baseline once on held-out cases. If the held-out result drives another modification, that set becomes development data and a new held-out set is needed. Compare final finalists on a second pinned repository in another supported language, such as a moderate Rust project, before claiming cross-language improvement. The prepared Werkzeug corpus alone supports only Python conclusions.

After retrieval selection, add a separate end-to-end planning track using the existing Qwen endpoint: record actual prompt tokens, plan validity/repairs, unresolved Search rows, correct file-impact labels, and evidence completeness. A valid JSON plan is not automatically a correct plan. Do not mix Qwen generation time into local retrieval latency or use generated plans as ground truth.

Deliver: reviewed/versioned dataset; baseline and candidate reports; source patch and exact configs; failure ledger; evidence-hit and context-recall comparison by category; latency/memory tradeoffs; held-out results; and a recommendation to keep or reject each change. Preserve the user's existing changes to `pi_runtime.py`, its tests, `.gitignore`, and `devstill.json` when working on the benchmark branch.

Start the next agent with:

> Read `benchmarks/retrieval/README.md`. Review the 20 development seeds, expand and freeze an independently reviewed dev/holdout dataset, then run the three existing baselines serially on the pinned Werkzeug checkout. Produce a failure ledger before making retrieval changes. Test only the first justified hypothesis, keeping immutable results and the same scoring protocol. Do not tune on held-out results, change labels to improve scores, call the LLM during local retrieval measurements, or modify the user's existing work. Report the evidence, costs, and whether the change deserves to proceed.
