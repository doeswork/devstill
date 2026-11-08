# Feature coordinator revision: measured progress, parity not achieved

## Current default: Quick first

Feature now submits the original request to the ordinary Quick review. A runnable
review is saved and executed directly: no coordinator call, rewritten instruction,
second classification or second retrieval. Only a review containing unresolved
Search items invokes coordination, capped at three cohesive tasks. Invalid output,
stale source and coding failures do not silently start another planner.

`.devstill/feature-quick-first-v1` completed all **24 attempts**: 12 existing feature
cases, one draw per method. Whole-feature success was **5/12 candidate versus 6/12
frozen Quick**. Behavioral acceptance was 6/12 each; one candidate passed behavior
but left a later task unfinished and is counted as a workflow failure.

Six requests used Quick directly and six entered coordination. The candidate's
successful cases were context iterator, digest, bulk environment, platform batch
and text module. Digest and platform batch completed dependent tasks. On the three
shared successes, median retrieval-through-acceptance time was **14.00 s candidate
versus 14.06 s baseline**; review medians were 3.70 s versus 3.61 s. Logged model calls
across all attempts totaled 108 candidate versus 83 baseline. The fallback still
has a cost. This one development pass does not prove quality parity or superiority.

The candidate source snapshot and audit are saved in that run directory. Candidate
and baseline source audits passed. A subsequent UI-only shutdown guard and type
annotation fix do not change the evaluated retrieval/planning/execution behavior.
The final suite passed **507 tests, with 1 skipped**; lint and diff checks passed.
Tests verify identical Quick prompts/plans, saved-review loading via `feature --run`,
one execution without reclassification, configured checks, and coordinated fallback.

Use `executor.validation_commands` for checks shared by both routes. Direct Quick
retains its normal execution budget and validation policy; the `feature` policy and
shared deadline apply when coordination is used. Previously saved feature reviews
still execute their recorded plan.

## Earlier coordinator-first revision


The numeric-reference coordinator failed all 36 feature attempts in the completed
144-attempt comparison. The revised coordinator completed **4 of 12** feature
attempts against **6 of 12** for frozen Quick in a new comparison. Three of its
successes used dependent Quick tasks. This establishes working composition on
some cases, but does **not** establish parity, speed improvement, or release readiness.

## Completed comparisons

All runs used the configured `vllm-qwen38-27b` endpoint, pinned Werkzeug/Cargo
corpora, disposable repositories and independently verified acceptance checks.
The baseline is restored Quick at commit `831b199`. It is not the abandoned
structured Task editor. Candidate/baseline source audits passed for each run.

| Campaign | Cases and repetitions | Baseline whole-feature passes | Candidate whole-feature passes |
| --- | --- | ---: | ---: |
| `.devstill/feature-quick-dev-v1` | 12 features × 3 | 12/36 | 0/36 |
| `.devstill/feature-quick-v2-pilot` | 4 selected features × 1 | 2/4 | 0/4 |
| `.devstill/feature-quick-v3-dev` | 12 features × 1 | 5/12 | 1/12 |
| `.devstill/feature-quick-v4-dev` | 12 features × 1 | 6/12 | 4/12 |

The original campaign also completed 12 small edits × 3 per method: frozen Quick
passed **25/36**, current Quick passed **28/36**. That slight difference is compatible
with sampling variation on the same Quick algorithm. Small edits were not rerun in
the feature-only revision campaigns; the exact Quick contract regression tests pass.

These are exposed development cases, used during diagnosis. Repetitions are not
additional distinct cases. The final revision has one draw per case, so its report's
`consistent_cases` field is merely the successful-case list, not evidence of repeated
reliability. Intermediate campaigns are retained, not pooled or replaced by better draws.

## What changed

The model now returns named task briefs containing outcomes, acceptance conditions,
file lists and named prerequisites. The prompt includes the JSON shape explicitly.
`feature_proposals.py` assigns IDs, resolves names, infers create/update from actual
file existence, orders shared reads/writes and derives predecessor outputs. Unknown
references, cycles, unsafe paths and contradictory operations still stop the plan.
The existing saved FeaturePlan format and Quick executor remain in use.

`feature_context.py` supplies bounded current source for the reviewed existing inputs
and write targets. Missing semantic hits receive exact-file lookup, including new
predecessors and empty files. Each child keeps Quick's existing token/chunk limits.
Other semantic neighbors are omitted when the task already specifies existing context;
a task creating files without existing inputs receives at most two convention examples.
No extra model context-search loop is added.

If Quick discovers a read from an already completed independent task, the runner
records an observed dependency. A conflict with an unfinished writer still stops.
Writes outside the reviewed scope remain blocked. Standalone Quick retrieval,
prompts and Pi execution are unchanged by this revision.

## Final revision results

| Feature | Frozen Quick | Revised coordinator | Revised task count |
| --- | --- | --- | ---: |
| User-agent information | Fail | Pass | 2 |
| JSON error responses | Pass | Pass | 1 |
| Context-managed iterator | Pass | Pass | 2 |
| JSON sequence | Pass | Fail acceptance | 5 |
| Vary middleware | Fail | Blocked | 0 |
| Drop-header middleware | Pass | Pass | 2 |
| Digest helper | Fail | Blocked | 0 |
| Optional file read | Fail | Fail acceptance | 1 |
| Registry URL | Fail | Blocked | 0 |
| Bulk environment | Pass | Fail acceptance | 1 |
| Platform batch | Pass | Partial; behavior passed | 6 |
| Text module | Fail | Fail acceptance | 2 |

All 12 model responses compiled structurally. Three returned explicit blockers,
one stopped after partial execution, and eight completed their planned tasks.
Four of those eight passed independent acceptance. Platform batch passed behavior
but a later task needed more context, so it is **not** a workflow success.

There are three shared-success pairs. Their median time from warm retrieval through
independent acceptance was **15.07 s** for Quick and **36.00 s** for the coordinator.
Review medians were 3.11 s versus 7.52 s. Setup/index-copy time is recorded separately.
Across all 12 attempts, logs recorded 87 baseline model calls versus 138 candidate
calls. Total token usage is not available consistently. These are natural operating
points, not equal aggregate-call/token budgets; failed attempts are not speed wins.

## Remaining work supported by the evidence

1. Reduce unnecessary decomposition. Platform batch became six tasks and JSON
   sequence five; both added cost without a completed correct feature. Keep local
   implementation and tests together, and investigate deterministic merging of
   tasks sharing an edit scope before changing generation budgets.
2. Improve source sufficiency decisions. Vary requested existing package structure;
   digest and registry URL raised unnecessary/contradictory clarifications. A bounded
   check of exact paths named in blockers could supply evidence for the existing
   single repair allowance. Do not silently discard genuine missing requirements.
3. Preserve API/export requirements through task decomposition. The text-module
   tasks finished but omitted the requested root re-export. Inspect requirement
   coverage against the original request; a syntactically valid graph cannot prove it.
4. Address concrete coding failures with local checks: malformed JSON-sequence data,
   an invalid Rust `with_context` call and invalid Rust parameter syntax. Benchmark
   acceptance remains evaluator-only; do not feed its assertions to the model.

Keep Quick as the default. Feature remains opt-in and experimental. Fresh cases,
repeated final-version comparisons and an aggregate-budget experiment are still
required before any claim that features tie or beat Quick.

## Reproduction and artifacts

Each campaign directory contains `protocol.json`, `results.json`, `audit.json`,
`final-audit.json`, per-attempt acceptance results, plans, logs and patches.
The v3/v4 directories also preserve `candidate-source/` and `candidate-files.json`.
The original run includes `server-observation.json`. Ignored artifacts must be
transferred separately from repository documentation.

Recompute the final audit without model calls:

```bash
.venv/bin/python -m benchmarks.retrieval.coding_report \
  .devstill/feature-quick-v4-dev \
  --output .devstill/feature-quick-v4-dev/audit.json
```

To run a declared new comparison, use a new output directory:

```bash
PYTHONPATH=src:. "$HOME/.local/share/devstill/venv/bin/python" \
  -m benchmarks.retrieval.coding_campaign run \
  --candidate feature --baseline .devstill/feature-quick-baseline \
  --verified .devstill/task-v2-case-verification-c \
  --workload feature --repeats 3 --output .devstill/feature-next-comparison
```

## Implementation verification

The final suite passed **491 tests, with 1 skipped**; Ruff and `git diff --check`
passed. One earlier full run hit a UI layout timing failure; the isolated rerun
and subsequent full suite passed. Tests cover source/deadline/scope protections,
compiler name resolution and file lifecycle, recovering missed predecessor context,
empty initializer context and recording only completed observed dependencies.
The local installation was refreshed; all 53 installed package files match source.
