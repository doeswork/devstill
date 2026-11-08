# Native Quick versus Pi: active test plan

Status: first campaign completed; see [fresh results and improvements](NATIVE_QUICK_FRESH_RESULTS.md).
The correctness gate failed. [Subsequent bounded fixes and diagnostics](QUICK_CONTEXT_REPAIR_RESULTS.md)
recover explicit deliveries but leave converter check-generation failures.
The instructions below preserve the pre-run protocol;
fixtures now exist, and these requests are now exposed development cases. Do not
reuse the original output directories or launch long repeats.

Goal: Devstill Quick must complete changes at least as reliably as native Pi and
be faster, using the same local model. Correctness comes first. A faster failure
is not a win. Normal Quick is now Devstill's own editor, not the legacy Pi wrapper.

## 1. Evidence and the next decision

The [latest known-case comparison](NATIVE_QUICK_RESULTS.md) completed 20 attempts:
10/10 correct workflows for each arm, with Quick faster on 9/10 requests and a
median paired wall ratio of 0.571. These ten prompts have been used to debug the
candidate. They are regression evidence, not fresh validation or feature parity.

The next live test should be **ten genuinely fresh small requests, one attempt
per method: 20 attempts total**, split into two five-case batches. Do not begin
by repeating all historical campaigns. First verify the fixtures and freeze the
candidate; stop expansion when a concrete failure needs investigation.

Leave the existing ten cases in `quick_fast.v1.json` unchanged. Do not rename them
as fresh, blend results across candidate versions, or select each method's best draw.

## 2. Exactly what to compare

| Arm | Runner name | Configuration |
| --- | --- | --- |
| Native Devstill Quick | `runtime` | Actual prepare, saved proposal reload, generated checks, and apply |
| Native Pi | `pi` | Normal native tools; request-level reasoning explicitly disabled |

Always pass `--variants pi,runtime --pi-disable-reasoning`. The runner's default
variants are historical. **Do not use `quick` for today's native Quick**: that arm
still calls the legacy campaign path. `minimal` is the older experimental editor,
not the shipped runtime. Neither belongs in the primary comparison.

Use the same server/model identity, original request, pinned repository contents,
prepared dependencies, reasoning setting, and 30-second outer worker deadline.
Retain each executor's normal tool/generation policy; this measures products, not
identical token budgets. Verify zero emitted reasoning from captured responses,
rather than assuming Pi's `--thinking off` alone controls the server.

The `runtime` arm currently saves/reloads and automatically approves a patch. It
covers the shared core, not human review time or actual TUI rendering. Test those
interfaces separately below. Pi must get the same installed project tools as the
candidate; a missing dependency must not be scored as an algorithm advantage.

## 3. Fresh-case specification

Author the following ten cases before running the frozen candidate. IDs below are
reserved labels, **not implemented fixtures**. Inspect the pinned source to choose
actual APIs and requirements; do not invent an unsupported function name or force
a category onto an unsuitable source file.

| ID | Request type | What acceptance must establish |
| --- | --- | --- |
| `nq01` | Explicit named symbol; simple behavior change | Requested behavior plus adjacent unchanged behavior |
| `nq02` | Small class-method edit | Constructor/import requirements respected; unrelated methods preserved |
| `nq03` | Natural-language request with no API/path named | Correct target found through hybrid fallback; observable behavior correct |
| `nq04` | Second natural-language lookup in a different module | Avoids tuning the semantic route to one request family |
| `nq05` | Edit deep inside a large source file | Correct function changed; beginning/end and unrelated APIs preserved |
| `nq06` | Another large-file edit involving an edge case | Boundaries and original error behavior retained |
| `nq07` | Ambiguous/repeated symbol name | Correct owner changed; similarly named sibling unchanged |
| `nq08` | Short related two-file change | Both requirements delivered and integration verified; no task decomposition needed |
| `nq09` | Small new module plus public import/export | Module imports through requested public API; no missing root export |
| `nq10` | Regression-sensitive formatting/type/escaping change | Behavioral assertions, source preservation and relevant static checks |

Balance direct references and semantic requests. Require distinct behavior or
source families from the ten tuned cases; changing an input constant or rewording
an old prompt is insufficient. Record previous exposure explicitly in each case.

Start on the existing pinned Werkzeug corpus, using a different set of APIs. It
has about 302 tracked files; do not describe it as a massive repository. If a
category is unsuitable, document and substitute a genuinely equivalent case
before seeing model output. A second pinned Python project is a subsequent gate,
not an undocumented corpus addition to this first comparison.

Use the established case format: `id`, `corpus`, `workload`, `query`, independent
`checks`, reference `edits`, and applicable `check_file`/`package`. Use `small` for
this bounded suite, even for related two-file work, and add a separate category
field for reporting. Reference edits and checks are evaluator data, never prompt
context. Additional metadata: category, source family, exposure, allowed change
areas, mandatory preserved behavior, and static/scope review requirements.

### Fixture acceptance before any model call

1. Original repository passes the selected existing regression tests.
2. Original repository fails the new behavioral acceptance for the intended reason.
3. Reference patch passes both new acceptance and existing regressions.
4. Assertion failures cannot be masked by `or True`, broad exception swallowing,
   printing-only checks, deleted tests, or imports from an installed old package.
5. Tests verify every explicit user requirement, including exports, preservation
   and multi-file integration where relevant.
6. Review reference changes for plausibility and scope. An alternative correct
   implementation must not fail solely because its text differs from the reference.
7. Freeze and hash fixture contents before candidate execution. If the same agent
   authors cases and interprets results, disclose that; do not call it independent
   adjudication. Avoid letting reference solutions enter the coding conversation.

Model-written checks are supplemental. Independent evaluator checks and protected
original regressions determine behavioral scores.

## 4. Source quality is a separate gate

The last successful suite still produced an unnecessary `type: ignore` removal
and an invented version-change note. Add review beyond “tests passed.”

For every patch, assess:

- All requested behavior implemented; no fabricated API/version claims.
- No unrelated annotation/comment/docstring removals or bulk truncation.
- No weakening/deletion of tests, replacing implementations with stubs, broad
  catch-and-ignore behavior, or hard-coded acceptance examples.
- Imports/exports and existing type contracts remain coherent.
- Any new files are actually used, and unrelated files stay intact.

Use automated unchanged-region/file checks where a requirement is objective. Do
not insist on an exact reference patch. Record a separate scope/static-quality
pass and reasons; a known material source regression blocks promotion even when
behavioral tests pass. Apply the same review standard to Pi and Quick, ideally
with arm labels hidden during patch review. Preserve raw automated scores rather
than silently rewriting them after manual inspection.

## 5. Offline product checks before the live batch

Run the supported project tests and focused runtime tests. The current recorded
baseline is 552 passing tests and one skipped test; counts are descriptive, not a
gate by themselves. No model requests are needed for these checks.

```bash
.venv/bin/python -m pytest tests/test_minimal_runtime.py tests/test_task_workflow.py tests/test_pi_comparison.py -q
.venv/bin/python -m pytest -q
```

Required behavior coverage:

| Area | Required check |
| --- | --- |
| Default routing | Ordinary Quick uses native runtime; Pi only on explicit legacy request |
| Review | No live source writes during prepare; show actual diff and validation |
| Saved apply | Reload after process restart; same digest; no generation on apply |
| YOLO parity | Same prepared patch and checks as Review under response replay |
| Stale review | Edited target/validation input causes conflict; user work preserved |
| Filesystem | Unique anchors, creation preconditions, exclusions, CRLF/mode preservation, symlink rejection |
| Failure handling | Bad schema/check syntax rejected before writes; at most two generations |
| Recovery | Partial-write rollback and interrupted journal detection |
| Processes | Timeout/cancellation cleans check process groups; no live patch on failed prepare |
| Retrieval | Exact route, semantic fallback, complete small files, refreshed index after edits |
| Interfaces | TUI approval submits saved digest; headless JSON is clean; no Textual/Pi dependency for native CLI |
| Session | Consecutive tasks see changed/new files; encoder reuse does not reuse stale evidence or model history |

Some rows may require additional tests, especially consecutive real-index tasks,
semantic fallback, crash recovery, and true Review/YOLO replay equivalence. Mark
coverage gaps before promotion; do not infer them from aggregate test counts.

Use deterministic response replay for interface permutations. Separately perform
one short live TUI Review/apply and one headless YOLO smoke at a milestone. Do not
double the entire model campaign for identical approval modes. Human delay is not
machine latency; disclose automatic approval in all benchmark reports.

## 6. Freeze and preflight

Before both live batches, record:

- Source manifest including runtime, schema/prompt, retrieval and benchmark code;
  dirty diff hash and installed-versus-checkout identity.
- Dataset and reference-verification hashes; corpus commit and source status.
- Model/server identity, context limits, output budget, reasoning and sampling
  settings, Pi executable/version/bundle hash, Python/check environment.
- Index identity and embedding model/device; cold/warm mode; resource contention.
- One baseline identifier used across batches. Do not edit implementation between
  batches and present the combined table as one frozen candidate.

The existing runner freezes per-invocation source, performs toolchain/reference
checks, rotates arm order and uses a benchmark lock. Compare manifests across
batch directories too; per-directory audit success alone does not prove that two
batches used the same implementation. Respect the server lock and ensure there
is no unrelated coding campaign competing for the same endpoint.

Run `verify` for the entire fresh dataset, even when the live job selects a subset:

```bash
PYTHONPATH=src:. /home/jeffy4080/.local/share/devstill/venv/bin/python \
  -m benchmarks.retrieval.coding_campaign verify \
  --dataset benchmarks/retrieval/native_quick_fresh.v1.json \
  --output .devstill/native-quick-fresh-v1-verified
```

The dataset has now been authored and verified; see the results report. Output
directories must be new. Never replace an earlier verification or result directory.

## 7. The first live campaign: 20 attempts, two short jobs

Batch A, first five fresh cases:

```bash
PYTHONPATH=src:. /home/jeffy4080/.local/share/devstill/venv/bin/python \
  -m benchmarks.retrieval.pi_comparison run \
  --variants pi,runtime --pi-disable-reasoning --repeats 1 \
  --purpose evaluation --timeout 30 \
  --dataset benchmarks/retrieval/native_quick_fresh.v1.json \
  --verified .devstill/native-quick-fresh-v1-verified \
  --case nq01 --case nq02 --case nq03 --case nq04 --case nq05 \
  --output .devstill/native-quick-fresh-v1-a
```

Inspect artifacts and failures before Batch B. If continuation is justified,
repeat with cases `nq06`–`nq10` and output `.devstill/native-quick-fresh-v1-b`.
The case order and cyclic arm order must remain recorded, not cherry-picked after
seeing timing. `--purpose evaluation` is a label, not certification of freshness.

Expected duration is a few minutes per batch. Ten workers at the full cap alone
can take five minutes; setup and independent acceptance add time. **The existing
runner has no whole-campaign wall-budget/resume flag.** Do not claim a hard
six-minute total cap or use invented flags. Keep job sizes small, observe progress,
and stop cleanly if a systemic problem appears. A partial job remains partial;
reruns get new directories and retain interrupted/failed attempts.

Stop before the next batch for an integrity failure, missing environment, changed
model/source, or a reproducible candidate-only failure. One unexplained sampled
failure requires diagnosis; it is not permission to erase the draw. If candidate
code changes, version it and label already-seen cases as development cases.

No automatic long campaign follows success.

## 8. Correctness and speed gates

Report, per category and overall:

1. Correct **completed** workflows, and independently correct patches separately.
   A correct patch followed by timeout is not a completed workflow.
2. Scope/static-quality review and each material violation.
3. Candidate-only and Pi-only successes, errors, timeouts and incomplete attempts.
4. Worker wall, including startup/retrieval/workspace/check/apply; proposal time
   and phase timings where recorded; through-independent-acceptance time separately.
5. Per-pair Quick/Pi ratios on shared successes, their median, all-attempt total
   time including failures, model requests, token usage when available, and repairs.
6. Each case's raw result, not only aggregate pass count and medians.

Development gate:

- No integrity failure or known material scope regression.
- Quick's observed correct-completion count is at least Pi's, with every
  candidate-only failure explained and no repeated regression hidden by aggregate wins.
- Median paired successful wall ratio at most **0.90** (a useful 10% margin),
  and no unexplained increase in failure-inclusive total cost.
- Enough shared successes to assess speed; zero or one successful pair cannot
  establish an overall win. If both methods struggle, the comparison is not a win.

Passing ten cases once supports proceeding, not a general non-inferiority claim.
Show the number of pairs and variation, and add paired uncertainty estimates as
repeated data grows. The 0.90 threshold is fixed before running, not chosen afterward.

## 9. Confirmation, warm performance and later expansion

If the fresh gate passes without changing the candidate, repeat those ten cases
for two additional draws each: 40 more attempts, **optional subsequent work**, in
small jobs. For example, select two cases with `--repeats 2` for eight attempts per
job. Do not launch all repeats automatically. Keep repeat/batch identity explicit
when aggregating; `(case, repeat)` alone is not globally unique across directories.
Counterbalance arm order over repeats and record cache conditions.

For a real warm-session comparison, retain the Devstill process/encoder across
requests while resetting task conversation and repository state. Give Pi equivalent
process/session-start conditions where possible, or label the asymmetry. Warmup
must use a neutral request and cannot reveal the task's solution. Measure first
cold task, subsequent warm tasks, and retained memory separately. The current
per-worker runner does **not** measure this; implement and offline-test a session
adapter before claiming warm results. Never subtract cold setup from logs and
call the remainder measured warm end-to-end latency.

After confirmation, add a second pinned repository with independently prepared
checks and a few related-file edits. More languages, larger features, dirty-tree
fixtures and multi-task coordination are later cohorts. Explicitly test the
current eligible-text-workspace limits; unsupported binary fixtures/symlinks should
be reported rather than treated as demonstrated support.

## 10. Handoff deliverables and reporting

The next executing agent should produce, in order:

1. Fresh fixture dataset with category/exposure metadata and source/scope rationale.
2. Passing base/reference verification and an environment/freeze manifest.
3. An offline coverage note identifying any missing product tests.
4. Batch A results and a continue/stop decision; Batch B only when justified.
5. A complete report linking every run, including failures and partial attempts.

Report template:

```text
Candidate/source hash; dataset/hash/exposure; Pi/model/tool identities
Operating point: reasoning, cold/warm, deadlines, approval policy
Per case: arm, completion, behavior, scope/static review, wall, calls, failure reason
Totals: completion, patch correctness, scope issues, all-attempt cost
Shared successes: N; paired median ratio; per-case regressions/variation
Audits and artifacts; known limitations; next gate or concrete fix
```

Keep the latest validated report and this plan active. Move superseded narrative
plans/reports into the [archive](../../archive/README.md), preserving links and
provenance. Do not move/delete datasets, executable harnesses, frozen code or raw
`.devstill` run directories as part of documentation cleanup. Raw runs may not be
tracked by Git: explicitly back them up before disk cleanup or machine migration.
