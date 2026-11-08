# Small execution diagnostic results

The revised minimal editor matched native Pi on four known small edits and was
faster on all four paired draws. This is a promising development result, **not
proof of general superiority or permission to resume broad campaigns**. Production
Quick/Review/YOLO remain unchanged. All experiment processes have finished.

## Three approaches, same model, reasoning explicitly disabled

Run: `.devstill/quick-three-way-v1`, 12 attempts, one draw per case and arm.

| Approach | Completed and correct | Correct patches | Median successful worker wall | Total model calls |
| --- | ---: | ---: | ---: | ---: |
| Native Pi | 4/4 | 4/4 | 10.62 s | 29 |
| Pi + Quick-selected context | 4/4 | 4/4 | 13.16 s | 20 |
| Minimal editor v1 | 1/4 | 2/4 | 8.44 s (one success only) | 7 |

Context preserved correctness and reduced model calls, but did not beat native
Pi on wall time. Its median paired wall ratio was 1.432; embedding startup and
index setup add roughly four seconds per disposable worker. Warm coding medians
were 10.43 s for Pi and 8.85 s for Pi plus context. A persistent warm app session
might reduce startup cost, but this experiment did not measure that.

The minimal editor's first revision failed through prose instead of JSON, mixed
read/edit responses rejected by the harness, incorrectly escaped generated check
code, and an unnecessary identical-text edit. One correct iterator patch failed
its generated check and did not complete repair. These failures remain counted.

## One bounded correction and recheck

Revision 2 requires a JSON schema response, supports checks-only repairs after
applied edits, accepts reads alongside edits, and clarifies escaping. It retains
the same retrieval, model, two-call limit, checks and exact-match requirements.
No case-specific solution or hidden test was put into its prompt.

Run: `.devstill/quick-minimal-v2`, eight attempts, fresh Pi controls; do not pool
these timings with the earlier run as if they were paired observations.

| Case | Pi completed correctly | Minimal v2 completed correctly | Pi wall | Minimal wall | Pi/minimal model calls |
| --- | --- | --- | ---: | ---: | ---: |
| Unparsed UserAgent representation | Yes | Yes | 8.84 s | 8.14 s | 7 / 2 |
| Boolean salt length rejection | Yes | Yes | 10.99 s | 9.29 s | 6 / 1 |
| Idempotent iterator close | Yes | Yes | 12.44 s | 8.00 s | 6 / 1 |
| Boolean age rejection in large file | Yes | Yes | 13.10 s | 10.04 s | 9 / 1 |

Both arms: **4/4 completed and correct**. Minimal's median paired wall ratio was
0.806 (19.4% lower); separate arm medians were 8.72 s versus 11.72 s. Total worker
time was 35.46 s versus 45.37 s, 21.8% lower. Model calls were 5 versus 28.
Warm coding medians were 4.51 s versus 11.53 s. Including independent acceptance,
median end-to-end times were 10.16 s versus 13.01 s. The patches were inspected:
each changes the relevant source file without truncating unrelated files.

Three of four minimal attempts completed in one generation; representation used
the second response. No multi-task or feature-coordination claim follows from this.

## Earlier normal-Pi pilot

The already-running `.devstill/pi-context-pilot-v1` finished 24 attempts over the
same four cases, three repetitions per arm, with normal native Pi server behavior.

| Approach | Completed and correct | Correct patches | Total worker time | Model calls |
| --- | ---: | ---: | ---: | ---: |
| Native Pi | 11/12 | 12/12 | 483.13 s | 135 |
| Pi + context | 12/12 | 12/12 | 309.24 s | 105 |

Pi's timeout had already produced a correct patch; it is a workflow failure.
On the 11 shared successes, Pi plus context's median paired wall ratio was 1.105
(10.5% slower), with arm medians 24.28 s versus 21.97 s. Avoiding one timeout
improved aggregate cost but does not establish a consistent latency advantage.
Do not combine this campaign with the reasoning-disabled runs.

## What this says about current Quick

The existing Quick failures are concrete: selected evidence does not reach its
executor, unrelated file-prefix reads exhaust its cumulative read budget, and
whole-file writes can then truncate unseen code. `w_close-0-quick` in the stopped
v2 broad run removed `wrap_file`; `w_age-0-quick` edited the wrong function and
truncated the changelog. See [diagnostic design](QUICK_DIAGNOSTIC.md).

The new minimal editor addresses these mechanisms experimentally: it receives
the selected code and line ranges directly, uses unique exact replacements,
checks Python syntax before writes, and runs behavioral checks with one repair.
Native Pi with evidence also resolves both cases without custom scoped tools.
The experiment does not isolate which individual restriction caused each failure.

## Integrity, scope and next gate

- Pi 1.5.0, served model `vllm-qwen38-27b`, endpoint
  `http://192.168.0.131:1234/v1`; same resolved Pi bundle as previously approved.
- Werkzeug 3.1.3, commit `6389612fd1ee1bd93579eed5026e8fd471d04abd`, approximately
  302 tracked files. These are known Python small-edit cases, not a large-project
  feature evaluation or a fresh holdout set.
- Both new runs explicitly sent `enable_thinking=false` for every arm. Captured
  responses contained **zero reasoning characters**. Native Pi tools/system prompt
  remained unchanged. Original and overridden requests are preserved.
- Same prepared Python dependencies and independent hidden acceptance, with
  original regression tests restored for evaluation. Model-written checks alone
  cannot earn success. Runs use separate disposable clones and private indexes.
- Every run completed and passed its frozen source/runtime, Pi, toolchain and
  retrieval-input audits. No concurrent model campaigns. All attempts retained.
- 24 focused harness tests passed; Ruff and `git diff --check` passed. Tests cover
  no partial writes on invalid batches, syntax/path rejection, large-file reads,
  bounded repair, checks-only repair and preservation of proxy requests/streams.

Do not replace production Quick yet. Freeze v2, repeat this small set, then use a
small fresh set containing both easy edits and large-file/related-file edits.
Require at least Pi's completed correctness, inspect every regression, and compare
paired wall time including retrieval and failed attempts. Measure a genuinely
warm persistent retrieval session separately rather than subtracting startup and
calling it observed end-to-end speed. Stay with short runs until that gate passes.

Artifacts: each run contains `protocol.json`, `results.json`, `summary.json`,
`final-audit.json`, frozen source, request/response transport, patches, independent
acceptance, and editor traces. The frozen original Quick was not rerun here; its
earlier results remain historical and are not pooled with these campaigns.
