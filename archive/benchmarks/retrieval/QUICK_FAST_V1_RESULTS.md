> Archived historical document. Statements and commands describe the workflow at that time. Use the [active test plan](../../../benchmarks/retrieval/TEST_PLAN.md) for current work. Original path: `benchmarks/retrieval/QUICK_FAST_V1_RESULTS.md`.

# Ten-case fast editor comparison — 2026-11-07

Completed **20 attempts over 10 distinct Python requests in 4 minutes 22 seconds**. Native Pi and the unchanged revision-2 minimal editor were each given one draw per case and a common **30-second worker deadline**. No attempt timed out. This is a short development diagnostic, not a held-out or repeated reliability evaluation.

| Outcome | Native Pi | Minimal editor v2 |
| --- | ---: | ---: |
| Completed and independently correct | **8/10** | **6/10** |
| Independent behavior and regression checks passed | 8/10 | **10/10** |
| Median worker wall on six shared successes | 10.98 s | 9.90 s |
| Total generation requests, including failures | 59 | 16 |
| Total worker time across all attempts | 109.55 s | 101.03 s |

The median of paired minimal/Pi wall ratios was **0.907**, or about 9.3% lower. Minimal was faster on **3/6** shared successes and slower on the other three. Timing includes retrieval/index setup and embedding startup. Independent acceptance is additional. Failed-fast runs are excluded from speed wins and retained in total time/call counts. Different aggregate generation/tool budgets remain part of the executor comparison; this is not an equal-total-token experiment.

## Per-case outcomes

| Request | Pi completion | Minimal completion | Minimal independent checks | Pi wall | Minimal wall |
| --- | --- | --- | --- | ---: | ---: |
| User-agent representation | Pass | Fail | Pass | 11.09 s | 7.84 s |
| Boolean salt length | Pass | Pass | Pass | 8.03 s | 9.00 s |
| Header CR/LF rejection | Pass | Pass | Pass | 18.66 s | 12.83 s |
| Idempotent iterator cleanup | Pass | Fail | Pass | 22.54 s | 9.09 s |
| Boolean age rejection | Pass | Pass | Pass | 11.16 s | 11.80 s |
| Exception newline normalization | Fail | Fail | Pass | 3.13 s | 8.29 s |
| Whitespace-only upload filename | Pass | Pass | Pass | 11.75 s | 8.89 s |
| PathLike filename support | Fail | Fail | Pass | 2.67 s | 15.38 s |
| Header lookup whitespace | Pass | Pass | Pass | 10.81 s | 7.09 s |
| Non-string age input | Pass | Pass | Pass | 9.70 s | 10.80 s |

## What failed

- Minimal produced correct patches for representation, iterator cleanup and exception newlines, but omitted local checks and did not supply them within the two-response allowance. The representation repair returned empty actions; the iterator repair requested a read instead of completing validation. These are counted as workflow failures despite independent acceptance passing.
- On PathLike support, minimal changed both the implementation and type annotation correctly, but its generated check first referenced an unimported name and then compared a union annotation with a tuple. Both checks failed; the repair budget was exhausted. The independent runtime checks passed.
- Native Pi ended normally after a single search tool on exception normalization and PathLike support, with an empty final response and no patch. Independent acceptance failed in both cases. Neither was a timeout.

**The four-case 4/4 result did not establish stable workflow reliability.** The immediate editor weakness in this draw is check generation and repair, rather than an observed inability to make the requested source edits. The next bounded revision should reject missing checks with explicit feedback and improve generated-check validation, then use new paired controls. This experiment made no editor or production Quick changes. Do not relax completion criteria or silently rerun these failures.

## Scope and integrity

- Six cases were existing development requests, including the four from the earlier diagnostic. Four newly authored development cases cover upload filename truthiness, PathLike sanitizing inputs, hop-by-hop header whitespace, and non-string age parsing. They were not independently authored/held out. There are seven source modules, all in Werkzeug; no Rust, multi-file feature or production generality claim follows.
- Dataset: `quick_fast.v1.json`. Before generation all 10 independent checks failed on starting source and passed with their reference changes. The regular protected Werkzeug regression suite also ran. Evaluator source/checks were absent from model prompts.
- Editor SHA-256: `c4bcd810ba3e575bc736214e37019f23e6c3ee586ccb7c0e35396369d22df88c`, identical to the reported v2. Code/config remained frozen throughout; all attempts and failures are retained.
- Pi 1.5.0 and the same `vllm-qwen38-27b` endpoint. Reasoning explicitly disabled for both arms; captured reasoning output was zero characters. Method order alternated across cases. No concurrent benchmark process was running.
- Final source, runtime, Pi, coding toolchain and retrieval-input audits passed. Patches were inspected for source preservation; the minimal patches contained no bulk truncation. Acceptance measures the supplied checks, not all possible edge cases.
- The six editor unit tests passed before the campaign. No runtime code changed. All-attempt costs include failures; missing aggregate token usage is not presented as zero.

Raw artifacts: `.devstill/quick-fast-v1/` (protocol, results, summary, audits, frozen source, requests/responses, editor traces, patches and acceptance). Reference verification: `.devstill/quick-fast-v1-verified/`. Pre-run plan: `.devstill/quick-fast-v1-plan.json`. These artifacts are ignored by Git and must be transferred separately.

## Repeat the same bounded protocol

Use a new output directory; do not pool results with a different editor revision or timeout.

```bash
PYTHONPATH=src:. "$HOME/.local/share/devstill/venv/bin/python" \
  -m benchmarks.retrieval.pi_comparison run \
  --variants pi,minimal --pi-disable-reasoning --repeats 1 --timeout 30 \
  --dataset benchmarks/retrieval/quick_fast.v1.json \
  --verified .devstill/quick-fast-v1-verified \
  --output .devstill/quick-fast-NEW
```
