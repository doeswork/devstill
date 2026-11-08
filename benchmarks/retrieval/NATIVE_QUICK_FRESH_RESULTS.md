# Fresh native Quick comparison — 2026-11-07 local / Nov 8 UTC

**Gate failed: Quick is faster on shared successes but less reliable than Pi.**
Do not expand to a long campaign or claim parity from this run.

## Frozen first pass

Ten source-authored requests, one draw per arm, two five-case batches. Same
vllm-qwen38-27b endpoint, Pi with request-level reasoning disabled, native runtime
prepare/reload/automatic approval/apply, 30-second worker limit. All recorded
responses had zero reasoning characters. These cases were new to this candidate,
but authored and adjudicated by the same agent; this is not independent validation.
They exercise new APIs/behavior in pinned Werkzeug, not a new project. Several
cases share modules. Cold worker startup is included; human review time is excluded.

| Metric | Pi | Native Quick |
| --- | ---: | ---: |
| Correct completed workflows | 7/10 | 5/10 |
| Correct patches, including timeouts | 8/10 | 5/10 |
| Total worker seconds, including failures | 184.90 | 116.66 |
| Model calls | 93 | 18 |

Four shared completed successes: median paired Quick/Pi wall ratio **0.779**
(22.1% lower). Quick was faster in three of those four. This small successful
subset cannot offset the correctness gap. Pi token reporting is incomplete, so
call counts do not establish token-cost savings.

| Case | Request | Pi completed / wall seconds | Quick completed / wall seconds |
| --- | --- | --- | --- |
| nq01 | Whitespace-only entity tag | pass / 12.06 | pass / 5.43 |
| nq02 | Snapshot Range constructor input | pass / 15.11 | pass / 10.35 |
| nq03 | Semantic lookup: unhashable converter value | pass / 20.83 | fail / 12.27 |
| nq04 | Semantic lookup: header conversion TypeError | pass / 12.60 | pass / 14.46 |
| nq05 | Content-range unit normalization | pass / 13.70 | pass / 11.95 |
| nq06 | Unique header values, including unhashable values | timeout, correct patch / 30.00 | pass / 14.41 |
| nq07 | UUID string canonicalization | pass / 18.26 | fail / 10.49 |
| nq08 | Range property plus package-root export | pass / 12.31 | fail / 14.14 |
| nq09 | New header_names module plus root export | fail / 20.02 | fail / 14.57 |
| nq10 | Encode plus while preserving slash | timeout, incorrect patch / 30.00 | fail / 8.59 |

Artifacts (repository-relative, ignored local evidence):
- `.devstill/native-quick-fresh-v1-verified`: all ten base/reference checks and patches.
- `.devstill/native-quick-fresh-v1-a` and `...-b`: all 20 attempts, requests,
  responses, patches, independent acceptance, protocol, source hashes and audits.
- `.devstill/native-quick-fresh-supplemental.json`: original routing/datastructure
  regressions on base, references and all first-pass output patches.

Both batches have identical source manifests and passing applicable final audits.
All base fixtures fail their requested new behavior; all ten reference patches
pass. Existing harness regressions pass on base/references. Supplemental routing
and datastructure regressions pass for base/all references and all candidate
patches except Pi nq10, where slash encoding regresses. This supplemental test was
added after authoring to cover modules outside the harness's fixed regression list;
it is not included in reported worker time. No model saw evaluator checks/patches.

## Concrete findings and priority

1. **Preserve requirements during repair.** Quick nq08 applied a property, then
   removed the failing root-export assertion instead of implementing the export.
   Its generated checks passed, but evaluator acceptance failed. A successful
   generated check does not mean the original request was completed. Next design
   work should retain requirement-bearing checks and distinguish a missing feature
   from an invalid check; a prompt reminder alone is not enforcement.
2. **Include constructor/base-class context.** Quick nq03 found AnyConverter but
   invented invalid routing setup and `.url_adapter`. nq07 repeatedly constructed
   UUIDConverter without its required Map. nq10 generated invalid/inadequate checks.
   Prioritize bounded constructor/import/base-class evidence over unrelated chunks;
   do not solve this by unlimited reasoning or more retries.
3. **Respect explicit new-file locations.** Both arms put nq09 in http.py instead
   of the requested header_names module. Quick even declared it applied. Surface
   explicitly requested paths in context and verify delivery across all locations.
4. **Static quality remains a gap.** nq06 in both arms misses at least one overload
   signature update and invents version-history text. Quick nq08/nq09 also invent
   version notes. Behavioral pass counts above remain raw, not quality-adjusted.
   The nq06 reference also lacks overload updates; strengthen reference/static
   acceptance in a separately versioned dataset before claiming typing coverage.
5. **Do not infer speed parity from model-call counts.** Quick uses fewer calls,
   but semantic retrieval and repair cost still vary; nq04 is slower than Pi.

## Small fixes made after freezing the first pass

- Read-plus-edit responses now return **post-edit** source in repair feedback.
  Previously repair was told edits were applied while seeing the old source.
- Repair guidance explicitly says a missing requested export/behavior needs an
  implementation edit, rather than deletion of the check. This is guidance only.
- Check subprocesses set `PYTHONDONTWRITEBYTECODE=1`: the new repair regression
  exposed stale bytecode for rapid same-size edits. Isolated workspaces exclude
  pre-existing bytecode; this prevents the first check from creating stale cache
  for the second. This is not a general guarantee against arbitrary check-created
  caches.
- Added a runtime regression covering edit/read/failing check/second edit and
  confirming the live repository remains untouched during preparation.

Focused runtime tests: 22 passed. Pre-change full suite: 552 passed, 1 skipped.
Ruff and whitespace checks passed. Post-change full-suite and bounded retry results
are recorded below. No constructor-context redesign, new editor, or extended
retry loop was introduced.

## Coverage limits and next work

No live TUI smoke, warm session measurement, repeated fresh draws, independent
static type campaign, second corpus, or larger feature campaign was run. The
suite is ten distinct requests, not ten independent source families. The unique
option is a modest API addition rather than a second independent deep-file family.
No long confirmation run is warranted. Fix requirement retention and bounded
constructor context next, then rerun these failures plus easy controls. Treat
these now-exposed cases as development cases; use fresh requests only after the
small diagnostic set becomes reliable.

## Bounded post-fix retry

`.devstill/native-quick-fresh-v2-repair`: four attempts on already-exposed nq03
and nq08, one draw per arm. Pi passed 2/2 (21.28 and 12.65 seconds); Quick passed
0/2 (15.67 and 14.57 seconds). Both Quick attempts exhausted two generations and
left no live patch. Applicable final audits passed. These draws belong to the
modified candidate and are **not pooled** with the original twenty attempts.
The small fixes are tested machinery improvements, not a demonstrated model
reliability improvement. Post-change full suite: **553 passed, 1 skipped**.

Total live work: 24 attempts. The initial attempt to request a runtime-only retry
was rejected by runner argument validation before any model call; the runner
requires Pi plus a candidate. No failed or partial model draw was discarded.
Supplemental regression driver retained at
`.devstill/native-quick-fresh-supplemental.py` alongside its JSON output.

Subsequent implementation and bounded diagnostic results are recorded in
[constructor context and delivery repair](QUICK_CONTEXT_REPAIR_RESULTS.md).
