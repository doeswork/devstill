> Archived historical document. Statements and commands describe the workflow at that time. Use the [active test plan](../../../benchmarks/retrieval/TEST_PLAN.md) for current work. Original path: `benchmarks/retrieval/QUICK_CHECK_HARDENING_RESULTS.md`.

# Check-generation hardening: ten-case paired retests

The final revised editor completed **10/10** requests correctly, matching native Pi at **10/10**, with no timeouts. The final 20-attempt campaign finished in **4 minutes 24 seconds**. The earlier missing-check failures did not recur. This is development evidence on the same ten exposed cases, not independent holdout validation or a production cutover.

## Implementation

- The generation schema and local response validator require 1–3 nonempty check commands on every response, including reads. Read-only checks are deferred until a source edit exists. There is still no separate model planning call.
- All command formats and inline Python syntax are validated before any edits from that response are written. A response with missing or syntactically invalid checks leaves its proposed edits unapplied.
- Repair feedback states whether edits are already applied and gives an explicit next action: produce edits plus checks when nothing was applied, or repair checks alone when the patch is already present. Real traceback output is retained.
- Prompts favor short, self-contained public-behavior assertions with imports, instead of brittle reflection/annotation checks.
- One intermediate successful run revealed assertions masked with `or True`. A further bounded correction rejects literal-success assertions and obvious constant-success boolean combinations before edits or subprocesses. This deliberately narrow AST guard does not prove arbitrary check semantics.
- Two model responses maximum, identical retrieval, 4096-token responses, explicit reasoning off, and the common 30-second outer worker deadline remain unchanged. These changes affect only the benchmark editor. Production Quick is not switched to it.

## Final results

| Metric | Native Pi | Final minimal editor |
| --- | ---: | ---: |
| Independent behavior/regression passes | 10/10 | 10/10 |
| Correct completed workflows | 10/10 | 10/10 |
| Median worker wall | 10.23 s | 9.04 s |
| Median through independent acceptance | 11.87 s | 10.52 s |
| Total model requests, all attempts | 67 | 14 |
| Total worker wall, all attempts | 108.58 s | 103.49 s |

The primary **median of per-case editor/Pi wall ratios is 0.975**: about **2.5% lower**, not the roughly 12% suggested by dividing the separate arm medians. The editor was faster on five cases and slower on five. Total worker time was 4.7% lower. This does not demonstrate a substantial or reliable speed advantage, and it does not reach the suggested practical 10% paired-latency margin. Model requests are not aggregate token cost; missing usage remains unknown.

Worker wall includes embedding/index setup, retrieval and coding. The measured coding-phase medians were 4.24 s versus Pi's 10.00 s, but these exclude editor startup work and are not a measured persistent warm-app result. Startup and unnecessary read follow-ups remain optimization targets.

| Request | Pi wall | Editor wall | Editor generations |
| --- | ---: | ---: | ---: |
| User-agent representation | 7.94 s | 10.17 s | 2 |
| Boolean salt length | 9.54 s | 8.90 s | 1 |
| Header CR/LF rejection | 13.68 s | 13.91 s | 2 |
| Idempotent iterator cleanup | 10.70 s | 8.59 s | 1 |
| Boolean age rejection | 17.39 s | 9.19 s | 1 |
| Exception newline normalization | 10.20 s | 8.35 s | 1 |
| Whitespace-only upload filename | 7.79 s | 8.66 s | 1 |
| PathLike filename support | 10.19 s | 14.34 s | 2 |
| Header lookup whitespace | 10.26 s | 7.59 s | 1 |
| Non-string age input | 10.90 s | 13.80 s | 2 |

Six editor attempts completed in one generation. Three used their follow-up for an additional source read; PathLike support used a checks-only repair after a check incorrectly passed the abstract `os.PathLike` class as an input. The final PathLike check uses actual path instances; it has no disabled `or True` assertions. Its final source patch preserves the original function docstring. All ten final patches were inspected for source preservation; no bulk truncation was observed. The generated version-note addition on the PathLike patch is unnecessary for the runtime change and would need review before shipping.

## Retained comparisons

| Campaign / candidate | Native Pi completion | Editor completion | Median paired wall ratio | Disposition |
| --- | ---: | ---: | ---: | --- |
| `quick-fast-v1`, previous editor v2 | 8/10 | 6/10 | 0.907 | Missing or bad generated checks; retained |
| `quick-fast-v2`, intermediate editor v3 | 9/10 | 10/10 | 0.748 | Pi timeout after correct patch; editor repair contained disabled assertions; not final candidate |
| `quick-fast-v3`, final editor v4 | 10/10 | 10/10 | 0.975 | Final strict check guard; no timeouts |

These are separate frozen versions with fresh native Pi controls, not repetitions to pool. The intermediate run was retained with `review-notes.json`; its 10/10 automated score was not presented as sufficient after manual review. The final correction was made once, then all ten cases were rerun without further code changes. Different Pi draws also show variance; improvements cannot be attributed solely to one prompt/schema change.

## Verification and limits

- **37 focused tests passed**, including real subprocess checks-only repair, rejection before writes, mandatory-check schema, deferred read checks, bounded failure and literal/boolean tautology rejection. Ruff and diff whitespace checks passed.
- The exact dataset and its preexisting reference verification were reused unchanged. Six requests were prior development cases and four were newly authored in the earlier run; all ten are now exposed/tuned development cases.
- Pi 1.5.0, the same served `vllm-qwen38-27b`, alternating method order, private clones/indexes and evaluator-only acceptance. Both arms explicitly disabled reasoning; final captured reasoning output was zero characters.
- All applicable final source/runtime/Pi/toolchain/retrieval audits passed; working-tree source stayed unchanged during each live campaign. Every attempt and failure is retained.
- Generated checks are not a semantic proof. Syntax/nonempty/tautology guards catch specific defects but cannot establish completeness. Independent behavior and protected regression tests determine benchmark correctness; they do not cover every possible behavior or source-quality issue.
- No further broad campaign or production Quick migration was performed. Next evidence should come from a few separately authored fresh cases. Measure a genuinely warm retained retrieval session separately if startup is optimized; do not subtract startup and label it measured end-to-end speed.

Final raw artifacts: `.devstill/quick-fast-v3/`. Intermediate artifacts: `.devstill/quick-fast-v2/`. Pre-run plans: `.devstill/quick-fast-v2-plan.json` and `.devstill/quick-fast-v3-plan.json`. Frozen candidate code, full request/response traces, patches, checks, acceptance and audits are included. Dataset: `quick_fast.v1.json`; original fast-suite result: `QUICK_FAST_V1_RESULTS.md`.
