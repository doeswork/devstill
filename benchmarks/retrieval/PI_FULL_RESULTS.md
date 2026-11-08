# Fast-mode four-way comparison

Status: stopped at the user's request after **48/288 completed attempts**.
This is incomplete diagnostic evidence, not a final superiority comparison.
All final integrity checks passed; `complete` is false. The interrupted next
attempt is not counted as completed. The 72-attempt follow-up is cancelled.

The active replacement is the bounded context pilot
`.devstill/pi-context-pilot-v1`: four diagnostic requests × three repetitions ×
stock Pi / Pi with retrieved snippets (24 attempts). Its isolated harness and
preparation checks are in `.devstill/pi-context-pilot-v1-setup`. Do not resume a
large campaign until a candidate works reliably on these diagnostics.

Run: `.devstill/pi-four-way-dev-v2`. The preceding four-arm integration smoke is
`.devstill/pi-four-way-smoke-v1`: all four attempts passed; all integrity checks
passed. Focused benchmark tests: 25 passed. The protocol is
[PI_COMPARISON.md](PI_COMPARISON.md).

The first campaign, `.devstill/pi-four-way-dev-v1`, was stopped after 46 completed
attempts when inspection found Pi's shell lacked the prepared Python test
dependencies and Cargo path. It remains an incomplete diagnostic; its latency
results cannot establish superiority. The corrected runner supplies the same
prepared toolchain to all arms and checks it before starting. Native Pi's live
shell preflight (`.devstill/pi-toolchain-preflight-v1`) confirmed Python, Python3,
pytest, required imports, Cargo and Rust resolve correctly.

## Frozen comparison

- 24 distinct, previously exposed development requests: 12 small edits and 12
  compact features; three repetitions; four arms; 288 planned attempts.
- `pi`: stock Pi with its normal coding tools and the original request.
- `quick`: current Devstill Quick, YOLO.
- `review`: current Devstill Quick, saved Review reloaded and automatically
  accepted unchanged. Human decision time and terminal rendering are excluded.
- `baseline`: restored Quick code frozen at commit `831b199`, rerun under the
  same current Pi and model. This is separate from historical measurements.
- All feature-sized requests in this comparison use Quick directly. The feature
  coordinator is outside this comparison.
- Pi resolves to version **1.5.0**, Node **v24.14.0**, bundle SHA256
  `3bde07c2b1d45f2cb3a6b3a3278c9c677d01f303b57b18d04a94426f9bb23c4d`.
  This is the same identity as the earlier final smoke; the user confirmed
  using this resolved installation for the campaign.
- Qwen endpoint: `http://192.168.0.131:1234/v1`; model alias
  `vllm-qwen38-27b`. Model weights are identified by the server alias, not
  independently verified.
- Stock Pi's `--thinking off` request does not serialize the Qwen-specific
  `chat_template_kwargs.enable_thinking=false` flag. Its recorded responses
  contain reasoning; Devstill sends the explicit flag and its sampled execution
  responses contain no reasoning. This is a comparison of the configured
  harnesses, not an equal-reasoning-budget experiment. Do not attribute every
  speed or correctness difference solely to retrieval.
- Pinned repositories: Werkzeug 3.1.3 (302 tracked files), Cargo 0.83.0
  (2,574 tracked files). These cases exercise focused library changes, not full
  application-scale delivery.
- Serial trials, rotated arm order, common 180-second worker deadline. Quick
  retains its production 120-second coding timeout and 12-tool allowance.
  Native Pi has no additional tool cap. Total compute budgets are not equal.
- Primary latency includes worker startup, embedding warmup, private index copy,
  retrieval, review, coding and application. Also report coding time excluding
  warmup/index copy, and independent acceptance time separately.
- Correctness requires normal workflow completion plus hidden acceptance and
  regression checks. Compare speed only on matching case/repetition successes.

Each trial saves its query, command, source identity, patch, acceptance logs,
model request/response bodies and stage timings. The final audit must confirm
source, baseline, runtime, Pi and retrieval inputs stayed unchanged.

## Reading the final results

Keep small edits and compact features separate. Report every failure, successes
out of 36 per arm per workload, paired speed ratios, per-case repeat counts and
total model calls. Compare current modes with direct Pi and with the frozen
baseline rerun. Historical campaigns provide context, not an interchangeable
timing sample: their startup/recording boundaries and runtime differ.

Three repeats on 12 cases per workload remain development evidence. A point
score equal to Pi does not prove equivalence. A release claim still needs fresh
cases and inspection of successful patches for weakened checks.

## Concrete issue to investigate after the frozen run

The executor prompt (`src/devstill/executor.py`, `_execution_prompt`) carries the
request and file plan, but not the retrieved source snippets or line ranges.
Its scoped reader returns at most 240 lines per call
(`src/devstill/pi/devstill_policy.ts`). The `dump_age` target starts at line 1067;
observed failed traces spend calls paging through the file, then attempt edits
against mismatched source text. The tool allowance can expire without any patch.

A focused follow-up would pass exact retrieved line evidence into execution and
test bounded symbol lookup within approved files. This is an evidence-backed
hypothesis, not an implemented or measured improvement in this campaign.

## Historical context (separate experiments)

| Saved campaign | Workload | Frozen Quick | Candidate at that time |
| --- | --- | ---: | ---: |
| `feature-quick-dev-v1` | Small edits, 12 cases × 3 | 25/36 | 28/36 |
| `feature-quick-dev-v1` | Features, 12 cases × 3 | 12/36 | 0/36 |
| `feature-quick-v4-dev` | Features, 12 cases × 1 | 6/12 | 4/12 |
| `feature-quick-first-v1` | Features, 12 cases × 1 | 6/12 | 5/12 |

These are completed-workflow acceptance counts, not pooled samples. The old
candidates used different feature-coordination revisions; the new four-way run
uses standalone Quick for every request. Historical timing boundaries differ,
so their seconds are not substituted into the fresh paired comparison. See
[FEATURE_COORDINATION_RESULTS.md](FEATURE_COORDINATION_RESULTS.md) for context.

## Additional explicitly non-reasoning Pi comparison

The user initially requested a separate Pi run with
`chat_template_kwargs.enable_thinking=false` explicitly set, then cancelled the
large benchmark direction in favor of the four-case diagnostic pilot. The
72-attempt follow-up was **not started**. Its benchmark-only changes were staged
in `/tmp/devstill-pi-explicit-reasoning-v1` and passed 27 focused tests; they have
not been copied into the project or deployed. Retain this as optional diagnostic
tooling, not a queued experiment or production change.

If reused later, preserve original and overridden outgoing requests and verify
the responses actually suppress reasoning. Keep sequential experiments separate
from interleaved comparisons; inference-server load/cache state are uncontrolled.
