# Native Quick cutover and ten-case comparison

Quick now uses Devstill's own runtime by default, in TUI and headless mode. The
private installation was updated and its default/configuration, runtime module,
console entry point, startup and package integrity verified. Pi is not called by
normal Quick. `task --engine legacy` explicitly selects the retained old path;
setting `editing.enabled=false` is also an explicit legacy selection. The separate
legacy feature coordinator is not replaced by this change.

## Changes

- Native Quick default enabled; old workflow tests explicitly request legacy.
- Generated checks are arrays of Python source lines, joined locally. String-literal
  escapes are preserved; checks still must parse and pass before application.
- Exact-symbol/lexical retrieval runs first. When an exact symbol resolves to one
  source path, use the lexical candidate pool; otherwise use full hybrid retrieval.
- Include complete source files up to 6 KB, deduplicated and counted in the actual
  prompt budget. This supplies constructors/imports/neighboring behavior that a
  method-only snippet may omit. No extra planning/model call is introduced.
- Preserve bounded two-generation repair, source freshness, isolated preparation,
  saved/digest-checked patch application, check validation and regression evaluation.

## Final frozen comparison

Run `.devstill/quick-native-v2`: ten exposed Werkzeug requests, one draw per arm,
20 attempts, same local model, native Pi with request-level reasoning explicitly
disabled, 30-second common worker cap. Both arms produced zero recorded reasoning
characters. Candidate is the actual runtime prepare/reload/apply path, not the old
benchmark-only editor. Timing includes process startup, retrieval/index preparation,
workspace construction, generated checks and application. Independent acceptance is
reported separately. Review is auto-approved without human delay in the benchmark.

| Metric | Native Pi | Native Devstill Quick |
| --- | ---: | ---: |
| Correct completed workflows | 10/10 | 10/10 |
| Independent behavior/regression passes | 10/10 | 10/10 |
| Model requests | 71 | 12 |
| Median worker wall | 11.28 s | 7.26 s |
| Total worker wall | 118.42 s | 69.33 s |
| Median through independent acceptance | 12.74 s | 8.73 s |

The median **paired** Quick/Pi wall ratio is **0.571**, or **42.9% lower**. Quick was
faster on nine of ten cases; representation was slower (8.44 versus 7.44 seconds).
Total worker time was 41.5% lower. Do not derive the paired improvement from the
ratio of the separate medians. Model-call count is not total token cost.

## Retained failures and limits

The preceding `.devstill/quick-native-v1` candidate passed 7/10 versus Pi's 8/10
completed workflows (Pi produced 10/10 correct patches, with two timeouts).
Line-array checks resolved the recorded newline-format problem, but constructor
and other incorrect API assumptions remained. That failed candidate is retained,
not pooled with the final run or overwritten by it.

The final revision changes retrieval/context after inspecting known failures.
These ten requests are therefore development cases, not a fresh holdout. This is
one repetition and establishes a win on this suite, not general superiority or
feature-building parity. Warm-session performance has not been separately measured.

Patch inspection found no bulk truncation. Two outputs contain unnecessary edits:
the age case removes an unrelated `type: ignore`, and the PathLike case adds a
version-change note. Behavioral acceptance does not establish ideal scope/style or
static typing correctness; these patches are experimental artifacts, not upstream
changes to ship. Fresh tests should include scope preservation and relevant static
checks as well as behavioral correctness.

All applicable frozen source/runtime/Pi/toolchain/retrieval audits passed for both
campaigns. The final test suite passed **552 tests, 1 skipped**. The evidence-packing
test covers the exact-symbol route and complete small-file context; other tests
cover line-array decoding, native Quick defaults, saved review application and the
explicit legacy path. Ruff and whitespace checks pass.

Next gate: freeze this installed candidate, author separately verified fresh cases,
and compare in small batches. Preserve every failure and paired latency; do not
restart long campaigns or silently re-enable Pi fallback if Quick struggles.
