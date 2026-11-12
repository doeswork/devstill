# Focused Quick diagnostic v5 — 2026-11-08

The narrower source context and readable excerpts matched Pi on this draw: both
completed 4/6 requests correctly. Quick was faster on all four shared successes,
with median paired Quick/Pi worker-wall ratio 0.609 (39.1% lower). This is a small,
exposed development sample, not established general superiority. Two cases still
fail for both methods, so the diagnostic is not reliable enough for a broad run.

Same six cases as v4, one draw each, native runtime versus stock Pi with request-level
reasoning disabled, common 30-second cap, automatic saved-review approval. No
implementation changes were made during the run. All applicable final audits pass;
zero reasoning characters were recorded. Original evaluator checks remain private.

| Case | Pi result / seconds | Quick result / seconds |
| --- | --- | --- |
| nq01 | pass / 10.79 | pass / 4.08 |
| nq03 | fail / 4.93 | fail / 15.81 |
| nq07 | pass / 13.27 | pass / 8.14 |
| nq08 | pass / 18.13 | pass / 13.23 |
| nq09 | pass / 15.63 | pass / 9.44 |
| nq10 | fail / 21.49 | fail / 11.62 |

Total worker time, including failures: **Pi 84.24 s; Quick 62.33 s** (26% lower).
Model calls: Pi 47; Quick 10. Call count is not token cost; Pi token reporting is
incomplete. All four successful Quick patches were independently accepted.

## Failures and next repairs

- **Quick nq03:** the first response includes an always-true assertion and imports
  Map from the wrong package. Validation rejects the checks before retaining any.
  The second response claims to repair retained index 0, which does not exist.
  It proposes a plausible implementation fix but preserves the incorrect import.
  No patch is applied. Fix feedback/schema so rejected checks and retained checks
  cannot be confused, and supply the actual import/usage of constructor dependencies.
- **Quick nq10:** the first check puts multiple source lines into one array item
  and is rejected. Again the repair refers to nonexistent retained check 0.
  Both proposed edits also add `encoding=None` rather than removing plus from
  the safe characters; protocol repair alone would not fix this behavior.
  The next diagnostic must distinguish schema/check failures from wrong edits.
- **Pi nq03:** exits normally with an empty patch; independent behavior fails.
  Counted as failure despite a normal process return code.
- **Pi nq10:** removes slash as well as plus from the safe set. This violates the
  explicit slash-preservation requirement. Independent acceptance rejects it.

## Comparison with the preceding candidate

v4 also completed 4/6, but the failures differ: Quick's UUID case recovered and
escaping regressed. Pi's failures changed too. The apparent improvement in paired
speed is promising; correctness remains variable. Do not combine successful cases
from different draws or describe this as 6/6. Preserve the two-generation bound
and fix rejected-check feedback before another small repeat. No broad campaign is
warranted yet.

## Evidence and scope

Raw run: `.devstill/native-quick-focused-v5` (12 attempts), with source/protocol
manifests, requests/responses, patches, acceptance and final audit. Previous v4/v3
results remain unchanged. The code had 38 focused tests passing before this run;
Ruff and whitespace checks pass. This turn ran the benchmark and recorded findings;
no product code or private installation update was made during the comparison.
The latest context changes are in the checkout; the last installed version remains
v4. A protected routing/datastructure regression replay is retained alongside the
run as `check_regressions.py` and `supplemental-regressions.json`.

Supplemental replay completed: all output patches pass the 240 protected routing/
datastructure tests except Pi nq10, which also fails routing regressions for slash
encoding. Empty Quick failure patches pass unchanged regressions but still fail
requested acceptance; these are not successful workflows.
