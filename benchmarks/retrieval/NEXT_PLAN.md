# Retrieval for small edits and complete features

**Task workflow implementation:** [Task Review / YOLO and dependency execution](TASK_IMPLEMENTATION.md). Feature acceptance quality remains a separate evaluation gate.

**Current next-stage strategy:** [one-shot tasks composed into features](ONE_SHOT_STRATEGY.md). One reusable Devstill Task handles a compact outcome; a Task Plan connects independent and ordered tasks through explicit contracts and dependencies. This replaces the proposed Quick/Feature/Mix routing. Start there for the next prototype; the evaluation and correctness requirements below remain applicable.

**Implementation update:** [deterministic changes, controlled comparisons, Cargo pilot and remaining gates](IMPLEMENTATION_RESULTS.md). The proposal below is preserved as the original staged plan; its prototype-only status describes the earlier design round.

Status: design and benchmark prototypes only, 2026-11-07. Production implementation and independent initial review are the next agent's work. This follow-up supersedes the next-step instructions in the original README; [the first campaign](RESULTS.md) remains historical evidence.

Devstill can find relevant files quickly, but the evidence selected for a feature plan is not yet dependable. Improve the route from retrieved candidates to usable context before replacing the embedding model. Small edits need precise implementation anchors and nearby regression tests; feature work needs several connected locations. An 11/12 file-hit score cannot establish either kind of completeness.

## Corpus and limits of the claim

The actual GitHub checkout is Werkzeug 3.1.3, commit `6389612fd1ee1bd93579eed5026e8fd471d04abd`, at `/tmp/devstill-retrieval-bench/repos/werkzeug-3.1.3`. It has 302 tracked files, 1,975,411 tracked bytes and 21,366 Python library source lines. The existing index contains 3,956 search units from 265 eligible files. This is a **medium-sized Python project**, not a large monorepo. No larger corpus was downloaded in this follow-up.

These 12 new cases deliberately stress changes: five small edits, five feature additions, one new middleware capability, and one request for an absent existing capability. They are exposed development probes with same-author relevance labels, not an independent test set. Workload labels choose the prototype's budgets; automatic intent routing has not been tested. Some gold spans prove only a minimum anchor set, not everything an implementation would require.

## What was tested

[change_probes.py](change_probes.py) imports existing search code into an isolated benchmark process. It does not patch or register production behavior. Every variant uses the same existing CUDA index and the current working-tree search baseline, which already includes the earlier prose/fuzzy change. The Qwen endpoint is `http://192.168.0.131:1234/v1`; the configured server model is `vllm-qwen38-27b`. The model name is the server identifier, not an independent verification of its underlying weights.

| Variant | Candidate and context policy |
| --- | --- |
| `current` | Existing search pool of 12; adaptive 3–5 chunks; tests excluded; add only a context token cap for this comparison. |
| `opt_in_wide` | Pool of 20; allow tests; cap at 5 small-edit or 8 feature chunks; remove near-overlapping snippets; apply token cap. |
| `canonical_anchors` | Above, plus exact identifiers extracted from the user's text and overload-to-implementation replacement by source path and qualified name. |
| `model_facets` | Above, plus up to three Qwen search rewrites, fused with the original query results. Qwen sees the user query, not gold evidence. |

Caps are 2,048 context tokens for small edits/absent lookups and 4,096 for features/new capabilities. Counts come from the serving Qwen tokenizer's `/tokenize` endpoint applied to serialized context records. They exclude system messages, tools, history and output reservation: this is **not** full-prompt budget enforcement. Candidate counts and context chunk caps differ by variant; these are diagnostic bundles, not isolated causal ablations.

Authoritative results use [werkzeug.changes.v2_1.json](werkzeug.changes.v2_1.json) and `.devstill/retrieval-design-v2/rescored/summary.json`. Coverage is the mean per-task fraction of required evidence groups present in the final context; complete means every labeled group is present.

| Workload | Current | Wider + tests | + canonical anchors | + Qwen facets |
| --- | --- | --- | --- | --- |
| Small edits, evidence coverage | 60% | **90%** | **90%** | 80% |
| Small edits, complete tasks | 1/5 | **4/5** | **4/5** | 3/5 |
| Small edits, median context tokens | 1,545 | 1,152 | 1,152 | 1,245 |
| Features, evidence coverage | 50% | 51.7% | 51.7% | **61.7%** |
| Features, complete tasks | 0/5 | 0/5 | 0/5 | **1/5** |
| Features, median context tokens | 2,426 | 2,953 | 2,953 | 3,435 |

All four variants achieved relevant-file hit@5 on all five feature tasks. Qwen facets raised feature evidence coverage in the returned pool from 70% to 93.3%, but only 61.7% reached final context. This strongly motivates investigating selection, deduplication and budget allocation; it does not prove that candidate retrieval is solved. Qwen facets also regressed two feature tasks relative to current selection.

The one new-middleware task missed the labeled architectural examples in every variant. Other returned snippets may be useful alternatives: independent adjudication must decide, rather than treating the chosen examples as exhaustive truth. The absent OAuth implementation request received nonempty context in every variant. That demonstrates a missing evidence-status decision, not a measured hallucinated answer: no answer generator was evaluated here.

Qwen rewrite time was about 0.75 seconds median for small edits and 0.78 seconds for features in the original generation pass. The corrected pass reused those exact rewrites and retained their original timing. Local retrieval timings are single samples without rotated order; tokenizer HTTP time and context selection are excluded. Do not cite these as p95 or end-to-end latency results. These probes did not evaluate generated code or plan quality.

### Corrections and provenance

The first prototype incorrectly failed to recognize certain overload chunks: their decorator begins at column zero while the class method remains indented. The corrected prototype consults existing parser decorator metadata before attempting AST parsing. A targeted `MultiDict.getlist` lookup then includes the real body at lines 262–290; the current path spends slots on overload signatures at 258–261 and omits the body. This is a successful diagnostic, not a production fix. It added no aggregate gain over the wider variant on the ten change tasks.

Two label errors were found while reviewing outputs. `feature_conversion` included blank line 117 outside the implementation; `feature_downloads` joined two required test bodies across unindexed whitespace. Version 2.1 removes the blank line and scores both test bodies as separate required groups. Queries and retrieved results did not change. [rescore_probes.py](rescore_probes.py) applies the new labels uniformly to every saved variant and rejects changed queries. These are disclosed post-run corrections, not a fresh held-out result.

Preserved local artifacts:

- `.devstill/retrieval-design-v2/change-probes/`: initial prototype run; canonicalization was faulty. Retained for audit, not the authoritative comparison.
- `.devstill/retrieval-design-v2/corrected-probes/`: corrected overload handling; exact original Qwen rewrites reused; original v2 labels.
- `.devstill/retrieval-design-v2/rescored/`: authoritative v2.1 metrics, complete contexts, generated queries, label snapshot and input hashes.
- `.devstill/retrieval-design-v2/production-before.json`: production source hashes taken before this follow-up. Verification records whether those files remained unchanged.

These generated artifacts are ignored local data. Transfer them with the handoff if the next agent runs elsewhere; `/tmp` corpus/index data are disposable. Keep endpoint credentials out of transferred configurations.

Follow-up verification: 14 benchmark/prototype tests passed; repository lint and `git diff --check` passed. All 44 production source files matched the hashes captured at the start of this follow-up, including the pre-existing changes. No production retrieval behavior was implemented in this follow-up.

## Failure ledger and the next experiments

| Evidence | Observed failure | Next isolated experiment / acceptance check |
| --- | --- | --- |
| Four small edits explicitly request tests | Current selection retains implementations but omits their required tests. | Change test eligibility only, with the same pool, chunk cap and token budget; measure tests and implementation retention separately. |
| Exact `MultiDict.getlist` diagnostic | Overloads occupy file slots before the implementation. | Resolve overload groups before quotas; distinguish implementation edits from signature/typing requests. No exact-lookup regression. |
| `feature_upload_limits` | Current covers docs only; facets recover parser enforcement and tests but still miss request plumbing. | Trace one request-to-parser relationship; retain entry point and implementation under the same budget. |
| `feature_cookie_priority` | Entry point and serialization are present; required tests remain absent. | Target the missing test role, then pack it without displacing those two anchors. |
| `feature_downloads` | Current finds directory serving but misses traversal and tests; facets cover all four groups. | Preserve this gain with bounded facet/relationship expansion and matched budgets. |
| `feature_converter` | Facets invent `RouteConverter`, promote unrelated numeric behavior, and lose numeric validation. | Validate rewrite fidelity and keep original useful evidence; reject or ignore fabricated symbol anchors. |
| `feature_conversion` | Rewriting strict value conversion into duplicate-key rejection loses single-value conversion evidence. | Retain user constraints and original anchors; fail a rewrite that changes the requested behavior. |
| `new_request_id` | Chosen middleware/header examples are missing; alternatives are not fully adjudicated. | Have an independent reviewer assess multiple valid architectural patterns, then test integration-point retrieval. |
| `absent_refresh` | Irrelevant context is supplied for a nonexistent existing implementation. | Return an explicit unresolved/unsupported evidence status after bounded search; never infer certainty of absence from low similarity alone. |

The benchmark packs only the already selected 5/8 chunks. When a large or overlapping candidate is rejected, it does not backfill from the remaining pool. It also allows large parent/class snippets to compete with focused methods. Both are plausible sources of the pool-to-context loss and need their own ablations. Do not describe them as verified fixes yet.

## Implementation contract for the next agent

### 1. Freeze trustworthy evaluation before tuning

Independently review the corrected labels against source and all saved candidates. Keep minimal meaningful spans, alternatives for equivalent implementations, and separate roles for separately necessary facts. For new features, label existing extension points and conventions, not imaginary files that have yet to be written. Review hard negatives against repository inventory and distinguish “find existing X” from “build new X.”

Create a fresh acceptance set of at least 30 small edits, 30 features, 10 new-capability requests and 10 absent/ambiguous requests, distinct from development families and from the exposed first campaign. Include terse feature prompts, long small-edit prompts, missing identifiers, exact paths, exclusions, tests/docs requirements and similar symbols in different namespaces. Do not infer task breadth from prompt length. Freeze source revision, labels, model/configuration, budgets and thresholds before opening results. Keep paraphrases and related subsystem tasks in the same split.

Log each decision stage: eligible source inventory; channel candidates; fusion; exact anchors; related candidates; overlap removal; context selection; tokens; evidence status. Record why a candidate was dropped. Expose the original query and any transformations. Gold roles and evidence must never enter runtime selection; the author-supplied workload labels in this prototype are an evaluation shortcut to remove in the implementation.

### 2. Repair deterministic retrieval and selection first

Extract explicit code references from natural requests, resolve them with path and namespace constraints, and preserve their implementation anchors. Exact path/kind filters and exclusions must apply before channel limits and on **every** subsequent lookup or relationship expansion. Add a starvation fixture where globally higher-ranked excluded files fill the candidate cap. The current prototype does not implement this contract and should not be copied directly into production.

Canonicalize overload groups before per-file quotas. Use parser metadata, source path and qualified name; do not resolve globally by short name. An implementation task should receive the body, while a typing/signature task may need the overload declarations too. Preserve provenance and ambiguity for conditional definitions, inherited methods, aliases, re-exports and multiple valid bodies; the prototype's single dictionary entry per qualified name is insufficient for those cases.

Include tests when requested or when a change's regression strategy needs them. Preserve docs when explicitly requested. Keep implementation and validation evidence distinguishable; allowing tests should not let a test class replace every implementation. Test eligibility, pool size, overlap handling and final chunk count must be separate experiments before combining them.

Apply overlap removal before final quotas. Prefer focused relevant implementations over bulky enclosing classes when they supply the same evidence. Pack under the **actual fully serialized prompt budget** using the served model's tokenizer, with allowance for system instructions, tools, conversation history and requested output. Backfill from remaining eligible candidates when overlap or size rejects an earlier choice. Retain source references for any trimming; do not claim that an omitted implementation tail is present. Benchmark token-count overhead separately and cache counts by content plus tokenizer identity.

### 3. Route by the work and fill missing evidence

| Request type | Initial strategy | Bounded expansion and stopping rule |
| --- | --- | --- |
| Precise small edit | Explicit implementation anchor, immediate contract, regression example when needed. | Stop when required evidence is sufficient; avoid an LLM rewrite by default. |
| Feature across existing code | Entry point, implementation, integration, requested tests/docs. Infer needed roles from the request and code. | Search for missing roles with at most 2–3 facets or a typed one-hop relationship. Stop at a preregistered round/time/token budget. |
| Entirely new capability | Existing extension points, conventions, neighboring examples and validation setup. | Identify what exists and what must be created. Offer alternatives when the architecture is ambiguous. |
| Existing capability not supported by retrieved evidence | Verify scope and inventory; try a bounded alternative lookup. | Return unresolved/needs-search or ask for location/scope; do not turn unrelated nearest neighbors into an answer. |

The router needs its own test cases and confusion report; run the retrieval benchmark with predicted routes as well as known routes. A route error counts against end-to-end evidence quality. Avoid a compulsory minimum of three chunks when no useful evidence exists. RRF scores are ranking scores, not calibrated probabilities of answerability.

For feature expansion, prefer concrete caller/callee, import, test-reference or wrapper links keyed by file and qualified symbol. Cap edges and rounds, respect exclusions, and account for parsing/graph maintenance costs. First test missing links shown above; a full repository graph is not required to diagnose them. Preserve useful original-query evidence rather than allowing fusion to discard it automatically. Inspect whether Deep Plan's initial pinned context prevents a later search from repairing a gap.

Keep Qwen facets optional. Require scope preservation, keep original identifiers intact, validate newly proposed identifiers against the repository, and distinguish a user's new API proposal from an alleged existing symbol. If rewriting fails or times out, fall back to deterministic retrieval with an explicit unresolved gap. Query rewriting must not become a prerequisite for every small edit. Model queries, not model answers or proposed fixes, are inputs to this retrieval stage.

### 4. Keep index and session correctness as release prerequisites

Previously identified risks remain outside this prototype: embedding/config changes do not reliably invalidate ordinary production indexes; the benchmark lock is not a production multi-process lock; indexing markers and shared planning artifacts need concurrency review. Before advertising multiple simultaneous Devstill sessions, verify process-safe index mutation, clear reader behavior during rebuilds, per-session artifacts, and atomic recovery after interruption. Test additions, renames, deletion and modification so stale snippets cannot masquerade as current evidence. Keep these changes separate from ranking experiments.

The earlier index audit found nine truncated embedding inputs and 14 parser diagnostics. Inspect their effect on task evidence before selecting a different model. If chunking or embeddings change, build a separate index with a new fingerprint; do not reuse stale vectors. Alternative embeddings or a reranker become justified when labeled failures remain in candidate retrieval after these corrections, not solely because feature context is incomplete.

## Controlled campaign and proposed gates

Implement one step at a time: eligibility; exact anchors/overloads; overlap-aware budget packing/backfill; bounded relationship expansion; then optional facets. For each comparison hold the embedding model, index, request set, final token budget and unrelated policies fixed. Report interactions separately when combining winners. Include an equal-chunk/equal-pool ablation alongside the intentionally wider operating mode.

Primary measures are implementation anchor hit@1/@3 for small edits; required evidence recall and complete-task rate for both kinds of changes; tests/docs coverage when requested; unsupported-context rate and useful abstention on negatives; context tokens and irrelevant/redundant token share. Keep top-five file/evidence metrics as diagnostics. Record pool recall separately from selected-context recall. Report per-task wins/losses and confidence intervals clustered by task family; latency repetitions are not extra independent tasks.

Provisional gates for the fresh acceptance set, to freeze before evaluating:

- Small edits: at least 95% implementation-anchor hit@3 and 90% complete required evidence; no regression on exact-reference or scope/exclusion fixtures.
- Features: at least 90% mean required-evidence coverage and 80% complete tasks under the declared budget. These are targets, not current performance.
- New capabilities: independent review of extension points, scope and proposed missing pieces; do not certify this with a single non-exhaustive gold location.
- Negatives: no unsupported claim that the requested implementation exists; report abstention and false-abstention separately. No false “absent” claim on positive fixtures. Ten cases are a starting gate, not a reliability guarantee.
- All modes: no source outside explicit scope, no stale evidence, no fully serialized prompt-budget overflow. Report memory and index costs.
- Retain the earlier 150 ms warm p95 local-search target on the reviewed CUDA machine. Declare a separate provisional 2 s p95 retrieval budget if Qwen rewriting is enabled; measure tokenizer/network overhead and test model-server contention. Do not use single samples from this prototype to claim either target passed.

Freeze the chosen candidate before running acceptance. If results drive further tuning, retire that set into development and commission new acceptance families. A score such as 11/12 remains a smoke check, not a release criterion.

Add a second pinned, established corpus with roughly 1,000–5,000 tracked text files and a language Devstill parses structurally before claiming large-repository or cross-language readiness. Inventory source/test/docs size, generated/vendor content, parser coverage and truncation before indexing. Keep it under a separate temporary workspace and archive the commit, labels and reports persistently. Do not install or run that project's application just to retrieve its code. Confirm both cold/warm latency, incremental indexing, resident/GPU memory and scaling under equal hardware load. The current runner is Werkzeug-specific: generalize its manifest/validation deliberately before adding a corpus, rather than merely swapping its directory.

Finally evaluate Qwen plans from the chosen context: trace every claimed existing symbol/file to source, check missing-role disclosure and impact coverage, and have a reviewer assess whether the plan could implement the request. Valid JSON alone is not a good plan. Evaluate actual patch correctness in a later coding track with repository tests; this handoff does not claim that result.

## Reproduce the probes

Use the existing isolated index or follow the README's prepare/index instructions. Run serially; ordinary Devstill writers must not use the benchmark index. The installed runtime supplies CUDA; the development `.venv` is CPU-only on this machine.

```bash
export PYTHONPATH="$PWD/src"
BENCH_PY="$HOME/.local/share/devstill/venv/bin/python"
BENCH_CONFIG=/tmp/devstill-retrieval-bench/configs/baseline.json
BENCH_DATA=benchmarks/retrieval/werkzeug.changes.v2_1.json

"$BENCH_PY" -m benchmarks.retrieval.runner validate --dataset "$BENCH_DATA"

# A fresh model-assisted run. Choose a new output path for every run.
"$BENCH_PY" -m benchmarks.retrieval.change_probes \
  --config "$BENCH_CONFIG" --dataset "$BENCH_DATA" \
  --agent-config devstill.json \
  --output .devstill/retrieval-design-v2/fresh-v2_1

# Repeat with fixed generated queries to isolate retrieval/prototype changes.
# Qwen's tokenizer endpoint is still required; this is not an offline replay.
"$BENCH_PY" -m benchmarks.retrieval.change_probes \
  --config "$BENCH_CONFIG" --dataset "$BENCH_DATA" \
  --agent-config devstill.json \
  --reuse-rewrites .devstill/retrieval-design-v2/corrected-probes/queries.jsonl \
  --output .devstill/retrieval-design-v2/fixed-rewrites-v2_1

# Rescore stored contexts without model or embedding calls.
.venv/bin/python -m benchmarks.retrieval.rescore_probes \
  --input .devstill/retrieval-design-v2/corrected-probes/queries.jsonl \
  --dataset "$BENCH_DATA" \
  --output .devstill/retrieval-design-v2/rescore-check

.venv/bin/python -m pytest tests/test_change_probes.py tests/test_retrieval_benchmark.py -q
.venv/bin/python -m ruff check benchmarks tests/test_change_probes.py tests/test_retrieval_benchmark.py
```

The prototype records source, dataset, index identity and rewrite provenance. Rewrites are matched by exact query text. It assumes the supplied cases' empty filters and known workloads; it is not a general production adapter. Verify scope/exclusion handling and automatic routing in a proper implementation. Output directories are immutable; copy reports rather than overwriting past runs.

## Handoff to implementation and initial review

Give the next agent this document, both versioned change datasets, the scripts/tests and the authoritative artifacts. Ask it to first audit the evidence and proposed contracts, then implement the smallest justified production change with focused correctness tests and one controlled ablation. Review actual production diff and per-query failures before combining mechanisms. Preserve the pre-existing changes in `search.py`, `pi_runtime.py`, their tests, `.gitignore` and `devstill.json`; do not reset the working tree or silently install the prototype as the runtime.

Suggested opening instruction:

> Read `benchmarks/retrieval/NEXT_PLAN.md` and review `.devstill/retrieval-design-v2/rescored/queries.jsonl`. Independently check the relevance labels and failure diagnoses first. Freeze new acceptance families. Implement deterministic test eligibility, scoped exact anchors/overload handling, and context packing as separately measurable changes, starting with the smallest justified one. Keep source provenance, full-prompt budgets and original useful evidence. Prove filters, typing lookups and tiny edits do not regress. Treat Qwen facets and relationship expansion as later bounded experiments. Deliver a reviewed diff, repeated reproducible measurements, per-task regressions and an explicit release recommendation; do not call these development probes held-out evidence.
