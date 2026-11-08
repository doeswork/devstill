# Quick repair results — 2026-11-08

The protocol/context fixes are implemented, but **the promotion gate did not
pass**. The final schema fix produced a valid response whose patch passed offline
acceptance. It has not completed a paired coding comparison. The private installed
Devstill was not updated, and Stage B and broader benchmarks were not run.

This follows [the approved repair plan](QUICK_REPAIR_PLAN.md). All cases are
exposed development cases. Failures and interrupted attempts remain retained;
results from different candidates are not pooled.

## Implemented changes

- Repair schema and feedback use the actual retained check state. Rejected checks
  have no repair indices. Passing and runtime delivery checks cannot be retired.
- Read-only requests can use `checks: []`. Draft checks do not block guarded
  reads, get retained or execute. A read still consumes one of two responses and
  cannot complete the task, including after earlier edits.
- Actual multiline `python_lines` entries are accepted. The resulting source is
  limited to 120 lines and 24,000 characters and validated before writes. Literal
  Python string escapes, syntax checks and the behavior/tautology guards remain.
- Constructor context resolves relevant imports under recognized `TYPE_CHECKING`
  blocks, including aliases and relative imports. Verified local import hints and
  constructor signatures are supplied for at most two dependency targets, one
  extra hop, within the existing budget. No project imports execute during retrieval.
- Editing/completion requires checks in the generation schema; only read-only
  requests may omit them. Each schema alternative now contains the complete
  object shape. Repair feedback repeats the original request and prioritizes
  correcting implementations that violate requested or preserved behavior.
- Runtime failures carry a stage, and independently rejected completed patches
  are distinguished in benchmark results. The saved review/apply, TUI and headless
  interfaces remain intact; no additional generation loop was introduced.

## Budget and frozen runs

Same local `vllm-qwen38-27b` model and endpoint, Pi 1.5.0, explicit request-level
reasoning off, 30-second worker cap, original dataset and independent acceptance
checks. Runtime uses actual prepare, saved-review reload and automatic approval.
Human review delay is excluded. No evaluator reference patch/check was supplied
to the model.

| Candidate/run | Attempts | Quick correct completed | Pi correct completed | Decision |
| --- | --- | ---: | ---: | --- |
| v6 Stage A | 12 completed | 1/6 | 4/6 | Fail; investigate before expansion |
| v7 Stage A retry | 10 completed, 1 interrupted, 1 unstarted | 1/5 completed attempts | 4/5 completed attempts | Stopped for schema incompatibility |
| v8 schema probe | 1 generation-only probe | Not a workflow score | Not run | Schema valid; offline patch replay passed |

**Budget used: 24 started attempts/probes:** 12 + 11 + 1. The interrupted v7
attempt counts against the budget even though it has no final score. No further
model calls were made. The v7 stop is a deviation from completing the planned
retry: continuing a known incompatible schema would waste the remaining calls.
Its source stayed frozen until it stopped. The final change was checked with the
one remaining probe, not relabeled as a completed Stage A.

v6 had only one shared successful pair, as did the completed portion of v7.
Neither meets the minimum of four shared successes for the planned speed gate.
Quick's lower time on those isolated successes does not establish a speed win.

## Stage A details and concrete failures

| Case / repetition | Pi result / seconds | Quick result / seconds |
| --- | --- | --- |
| nq01 / 0 | pass / 12.50 | fail / 11.85 |
| nq03 / 0 | timeout / 30.00 | fail / 10.54 |
| nq10 / 0 | pass / 20.07 | fail / 7.04 |
| nq01 / 1 | pass / 10.29 | pass / 3.73 |
| nq03 / 1 | pass / 16.36 | independent rejection / 15.85 |
| nq10 / 1 | timeout / 30.00 | fail / 5.63 |

- **nq01/0:** implementation looked appropriate, but checks compared a tuple to
  `None`. Repair corrected that expectation, then added unrelated cases and a
  nonexistent `ETags.strong` API. The workflow failed its generated checks.
- **nq03/0:** the initial implementation relaxed the schema to allow empty checks
  for reads, but also allowed edits with empty checks. Both responses submitted
  edits without checks and were rejected locally. This was a regression introduced
  during this implementation, addressed by the conditional schema.
- **nq03/1:** constructor/import context worked. The model changed the requested
  error behavior and then weakened its own checks. Generated checks passed, but
  independent acceptance correctly rejected the delivered patch. Valid protocol
  and supplied context do not guarantee requirements survive repair.
- **nq10/0:** the first response omitted checks; the second removed slash from the
  safe characters along with plus. Generated preservation checks rejected it.
- **nq10/1:** a read-only request was correctly served. The subsequent edit left
  plus safe while claiming to remove it; the executable check rejected it.
- **Pi:** nq03/0 left a behaviorally correct patch but exceeded the worker deadline;
  nq10/1 timed out without a patch. Neither is a completed success.

v6 totals were Quick 54.64 worker seconds / 11 model calls, Pi 119.23 seconds /
75 calls, including failures. These costs do not compensate for lower correctness.

## Schema compatibility finding and final correction

The v7 conditional schema was valid JSON Schema, but the endpoint did not enforce
the full shape when its `anyOf` alternatives contained only partial overrides.
**9 of 10 captured native responses violated the submitted schema**, including
strings in place of check objects and missing required repair fields. This is
observed request/response evidence; the backend's internal cause is not proven.

The final version makes each alternative a fully specified object, sharing only
root definitions. Offline tests validate each alternative independently so that
dropping sibling constraints cannot permit the observed malformed responses.
The single live v8 probe passed exact schema validation and local check validation.
It used the retained initial nq01 prompt and context, made one generation call,
and emitted no reasoning. Its generated patch/checks then passed independent
acceptance and 360 protected upstream tests in an offline replay.

That probe did **not** rerun retrieval, saved review/application or the repair
branch as a complete workflow. It does not prove that final Quick matches Pi.

## Validation and limits

- Final checkout: **596 tests passed, 1 skipped**; 83 focused runtime/benchmark
  tests passed. Ruff and whitespace checks passed. Tests cover the recorded failure
  shapes, retained/passing/delivery protection, read budgets and exclusions,
  multiline bounds/escapes, type-only imports, aliases and dependency limits.
- A deterministic URL-encoding replay confirms that merely adding `encoding=None`
  still fails behavior checks after protocol repair.
- Every one of the 22 completed paired-attempt output patches passes **360 protected
  HTTP/routing/datastructure tests**. Empty failure patches pass unchanged tests;
  this does not turn them into successful changes. New-request acceptance remains
  essential, as demonstrated by the incorrect v6 nq03 patch.
- Patch review found an unrelated annotation narrowing/removal of `str()` in Pi's
  v6 nq03/1 patch. Its automated pass remains reported; this is a separate source
  quality concern. v7's nonempty output patches stay within the requested changes.
- All applicable frozen-source/runtime/toolchain/retrieval-input audits passed.
  v6 is complete; v7's audit correctly records `complete: false`. No captured
  generation emitted reasoning. Preexisting CLI/index-progress changes were
  preserved and frozen with the candidate, not changed by this work.

## Evidence and next gate

Raw artifacts, patches, schema-compliance audits and protected regression replays:

- `.devstill/native-quick-repair-v6-a`
- `.devstill/native-quick-repair-v7-a` (includes `stop-record.json`)
- `.devstill/native-quick-repair-v8-schema-smoke` (includes `offline-replay.json`)

Replay helpers are `.devstill/quick-repair-regressions.py` and
`.devstill/quick-repair-schema-smoke.py`. The latter intentionally refuses to reuse
its output directory and is not a paired benchmark. Earlier reports and raw runs
remain unchanged.

The next validation should first check read-only and repair-schema compliance on
the actual endpoint, then run the same bounded Stage A on the final frozen code.
Do not promote or launch broad tests based on this probe. Remaining product work
is preserving requested behavior during check repair and correctly handling small
edits with preservation constraints; more constructor context alone will not fix
those failures. No full rewrite or additional planning loop is justified here.
