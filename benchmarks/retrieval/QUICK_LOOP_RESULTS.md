# Quick agent loop vs Pi — results (2026-11-08)

Quick's default engine is now a retrieval-primed tool loop
([design](../../MINIMAL_RUNTIME.md)). This report compares it with stock Pi
1.5.0 on the same local model, repositories and requests.

## Setup

- **Model:** `vllm-qwen38-27b` (Qwen3.8-27B NVFP4, vLLM on one RTX 5090),
  reasoning disabled for both arms, server default sampling.
- **Harness:** `benchmarks.retrieval.pi_comparison run --variants pi,runtime
  --pi-disable-reasoning --timeout 120`. Trials run strictly one at a time with
  alternating arm order, each in a fresh clone; the runtime arm uses the production
  prepare → saved review → apply path. **Success = completed workflow + independent
  acceptance** (hidden check script plus protected upstream tests). Times are
  worker wall seconds, including process start, retrieval and Pi start-up.
- **Machine:** CPU-only WSL client (no GPU for embeddings); the model server is a
  separate GPU box. Indexes were built on CPU.
- **Acceptance regressions** were widened for this campaign: Werkzeug now runs 14
  upstream test files (adding routing, datastructures, utils, urls, formparser,
  local, test, internal, send_file) because most cases edit those areas; click runs
  its whole suite. The benchmark venv pins pytest 8.3.5 (click 8.1.8's suite does
  not collect under pytest 9).
- **Cap:** 120 s per attempt, so correctness is measured separately from speed;
  "within 30 s" (the earlier campaigns' cap) is reported from the recorded times.

## Datasets

| Dataset | Cases | Exposure |
| --- | ---: | --- |
| `native_quick_fresh.v1.json` (nq01–nq10) | 10 | development (exposed in earlier campaigns) |
| `quick_fast.v1.json` (w_*) | 10 | development (exposed) |
| `quick_loop_dev.v1.json` (odd x-ids) | 12 | new, development |
| `quick_loop_holdout.v1.json` (even x-ids) | 12 | new, held out until the final run |

The 24 new cases were written by six independent subagents (two corpora,
werkzeug 3.1.3 and click 8.1.8) and each was adversarially reviewed by another
subagent that re-verified it, tried alternative correct implementations
(over-constraint) and plausible wrong ones (under-constraint), and fixed or
rejected it (16 fixed, 8 accepted as written, 0 rejected). New areas: TOML project config, Sphinx docs,
docstrings, message wording, a multi-file rename, deprecation warnings, keyword
parameters, test authoring, help output, environment-variable parsing and new
options. Every case fails on the original source and passes with its reference
patch plus all protected regressions.

## Results

### Summary by round

One paired attempt per case and arm unless noted. "Shared" counts cases both
arms completed; the ratio is the median of loop/Pi wall time over those.

| Round | Pi completed | Loop completed | ≤30 s (Pi / loop) | Median wall s (Pi / loop) | Shared | Shared median s (Pi / loop) | Median ratio | Loop faster |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| v1 (20 exposed cases) | 19/20 | 19/20 | 18 / 18 | 16.0 / 9.7 | 18 | 15.9 / 9.7 | 0.63 | 17/18 |
| v2 (32 dev cases) | 28/32 | 26/32 | 24 / 23 | 17.3 / 8.0 | 23 | 13.8 / 7.7 | 0.60 | 18/23 |
| v3 (32 dev cases) | 27/32 | 28/32 | 23 / 26 | 13.6 / 9.7 | 25 | 13.5 / 9.7 | 0.68 | 19/25 |
| **Final dev** (32 cases, `37f91b6`) | 28/32 | **30/32** | 22 / 25 | 16.1 / 10.7 | 27 | 16.3 / 9.6 | 0.72 | 22/27 |
| **Final held-out** (12 cases × 2) | 18/24 | **18/24** | 8 / 12 | 30.9 / 22.7 | 13 | 28.6 / 15.8 | 0.76 | 9/13 |
| **Final, all** (56 pairs) | 46/56 | **48/56** | 30 / 37 | 19.0 / 14.0 | 40 | 18.3 / 11.8 | 0.73 | 31/40 |

All final runs passed their frozen-source, runtime, Pi, toolchain and
retrieval-input audits. (In v2 one audit flagged changed retrieval inputs: a
diagnostic probe of mine opened the click index directly, touching SQLite side
files; index data was unchanged.)

### Held-out by codebase

| Corpus | Pi | Loop |
| --- | ---: | ---: |
| werkzeug (6 cases × 2) | 7/12 (5 timeouts) | **12/12** |
| click (6 cases × 2) | **11/12** | 6/12 |

The held-out click cases are larger multi-step changes (threading a setting
through `Context` into the formatter, new `ParamType` options, test authoring
with click's command naming). The loop's click failures mostly ended at its
20-call cap after 80–100 s while it was still iterating on its own checks; Pi
kept going and solved them, sometimes near its 120 s limit (38 calls). See
"Next steps".

### What changed between rounds

- **v1** (`de7c4c1`): the first loop engine — retrieval-primed context, read /
  python / bash / edit / write / done tools, lexical-first retrieval, one example
  exchange, 10-call cap.
- **v2** (`542033d`): lazy Chroma import (−1.8 s per cold run), workspace copy
  overlapped with the first model call, review rendering, the background
  regression guard, named files in context, write protection (a test module had
  been overwritten wholesale), pytest 8.3.5 for click.
- **v3** (`e479070`): the latest check after the last edit decides at the turn
  limit; a warning when a check is bent to match wrong code; the whole test
  directory joins the guard when fast; 20-call cap; no abort when nothing fits
  the context budget.
- **Final** (`37f91b6`): fixes from an independent four-lens code review (53
  findings, 44 confirmed): guard shutdown on every exit, credential-free command
  environments, `verified` counts only real checks, turn-limit acceptance runs
  delivery checks and the guard, the guard's baseline re-runs whole original test
  files, proposals contain only the agent's own changes, context truncation at line
  ends; plus an automatic re-run of the agent's last check after a later edit, and
  reporting of failing tests the agent added.

### Failures and their causes

Loop failures by round, with what addressed them:

| Round | Case | Cause | Change |
| --- | --- | --- | --- |
| v1 | nq10 | Rewrote the whole `safe=` string, dropping `:`; its own checks passed, an upstream routing test failed | regression guard (v2) |
| v2 | nq02 | Correct first edit, then the call cap ran out while fixing its own checks; an earlier failing check blocked acceptance | latest-check rule (v3) |
| v2 | nq10 | Dropped `/` and then rewrote its failing assertion to the wrong output; guard caught it but no calls left | weakening warning, diff in guard feedback (v3) |
| v2 | xc01 | Correct NaN fix with `import math`, breaking click's light-imports test | whole-suite guard run (v3) |
| v2 | xc03 | Still converging at the 10-call cap (Pi needed 77 s) | 20-call cap (v3) |
| v2 | xc05 | Large click class chunks exceeded the budget; preparation aborted | keep best match (v3) |
| v2–v3 | xc11 | Expanded `~` but missed that click's executable check reads the unexpanded value (Pi also failed in v3) | model limitation |
| v3 | nq06 | Final edit deleted a helper still in use; finished without re-checking | automatic re-check (final) |
| v3 | xc09 | Its own new test failed (`only_second` vs `only-second`) | report failing added tests (final) |
| v3 | xw11 | Setter kept the old parameters (passed in v2; Pi timed out) | model variance |

Pi's failures were mostly 120 s timeouts (nq06 in every round; nq09, nq10, xc01,
xc12, xw04, xw07, xw08, xw11 and xw12 in some), plus wrong patches on xc01 (v3)
and xc11 (v3 and final).

## Speed

Final runs, loop successes (n = 48): median **0.40 s** from start to the first
model call (v1: 1.8 s; lazy Chroma, workspace copy overlapped with the first call,
no /tokenize round trips), 0.67 s of process overhead, **4.5 model calls** (mean
6.7) and ~740 output tokens per task. Model decode dominates the remaining time
(~60 tokens/s on this server). 45 of 48 loop successes ended on a passing check of
the agent's own; the rest finished unverified and are labelled so on review.

## Conclusions

- **Correctness:** on everything measured in the final runs the loop completed
  48/56 versus Pi's 46/56; on the held-out cases the two tied at 18/24. Pi's
  misses were nearly all 120 s timeouts; the loop's were wrong or unfinished
  changes, concentrated in larger click features.
- **Speed:** the loop was faster on 31 of 40 shared successes (median ratio
  0.73) and finished within 30 s more often (37 vs 30).
- **Limits of this evidence:** one attempt per case on development sets (two on
  held-out), one model and server, two Python codebases. Exposed development
  cases were used for iteration; the held-out cases were run only in the final
  evaluation, and no code changed after it started.

## Why the loop loses click tasks (post-hoc diagnosis)

After the final runs, independent analysts replayed every failed held-out click
trial (read-only; no further tuning against held-out cases):

- **The agent's own checks were the problem, not its edits.** Of 44
  assert-bearing checks in those trials, 41 also fail against the reference
  solution (click API misuse in the checks: `make_context`/`Exit`,
  `context_settings`, command-name normalization, typos in echoed options).
  Wrong checks took 32% of calls, 54% of time and 60% of output tokens.
- **Two trials had correct code** (xc02/1 after call 11, xc10/1 after call 9) but
  ended failed: a broken check still failed at the call cap, and the automatic
  re-check re-ran a check with a typo.
- **Three trials shipped wrong code as "verified"**: their final passing check
  also passes on the original source (it never exercised the request).
- Pi checked mostly by printing output and running pytest. By acceptance it was
  12/12 on held-out click (one correct patch ran out the clock). Pi received only
  the request (stock Pi, as intended); it pip-installed the corpora into the shared
  benchmark venv (`pip install -e .`), which displaced its `click` package. This
  did not affect results (all checks import from the repository via PYTHONPATH);
  the venv was repaired afterwards.

## Next steps

Ranked by expected effect (estimates from replays of the traces, not measured):

1. **A failing self-check must not override a clean guard when the check fails
   the same way on the original source.** Re-run it with the original files (the
   guard already restores/runs/restores) and keep the patch if the failure is
   identical or moves later; this recovers xc02/1 and xc10/1 and keeps xc12/1
   (which reproduces a real bug) failed.
2. **A passing check that also passes on the original source is not
   verification.** Before accepting, re-run the latest passing assert-bearing check
   on the original sources and refuse once if it passes there (print-only scripts
   never count; exempt test-only changes; run bash checks with `set -o pipefail`).
3. **Better check feedback:** print operands of chained/`and` asserts, mark
   exceptions raised in the check script itself as check errors, refuse
   byte-identical resubmitted checks, and cap check length (click checks over 30
   lines failed 61 of 71 times, shorter ones 6 of 29).
4. Prompt changes (softer "a failing assert means the code is wrong", no checks of
   every preserved behavior) only after 1–3, as a separate arm.

Validate on development cases only (click xc01/03/05/09/11 and the werkzeug set,
three repeats), accepting a change only if click passes rise, werkzeug does not
regress, and werkzeug median calls and wall time stay within +10%.

## Follow-up: checks judged against the original code (`5143f07`)

Steps 1–3 were implemented after the final evaluation: a failing check that
gets further than it does on the original code no longer blocks a change; a
passing check that also passes on the original code is refused once; only
assert-bearing scripts count as checks; identical resubmitted checks are refused;
the check runner explains chained/boolean asserts and errors raised by the check
script itself; bash runs with `pipefail`.

A loop-only A/B on development cases (the 12 new dev cases plus nq06 and nq10,
two repeats, versions alternated, same production path and acceptance):

| Version | Accepted | click | werkzeug (new dev) | nq06/nq10 | Median s | Median calls |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `e6476fb` (final evaluation) | 24/28 | 10/12 | 11/12 | 3/4 | 17.2 | 6 |
| `5143f07` | 24/28 | 9/12 | 12/12 | 3/4 | 16.9 | 6 |

No measurable change: six cases flipped, three each way, and the new rules
rarely fired on these cases (one refusal, no progress-based acceptance). They
target patterns seen in the held-out click traces, which are now exposed; their
effect needs fresh click-heavy cases to measure. The change is kept because it
did not regress anything and closes documented failure modes.

