# Benchmark campaign 20261109T181124Z-8bdf9f3

**Status:** completed · **Started:** 2026-11-09T18:11:24+00:00 · **Finished:** 2026-11-09T19:37:41+00:00 (1.44 h)

**Code:** bench-test-ui-ux @ 8bdf9f3 · **Model:** vllm-qwen38-27b at https://llm.codemonkey.study/v1 · **Pi:** 1.5.0 · **Sampling:** default

**Note:** loop improvements 8bdf9f3, remote endpoint llm.codemonkey.study, 2 repeats

## Results

Pass = the agent finished and the independent acceptance check passed (hidden behaviour check plus protected tests; SWE-bench: the official grader's `resolved`). Times are worker wall seconds; the time ratio is the median of Devstill/Pi over cases both passed (below 1 = Devstill faster).

| Suite | Status | Trials | Pi passed | Devstill passed | Both | Pi only | Devstill only | Median s, shared (Pi / Devstill) | Time ratio | Devstill faster |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | ---: | ---: |
| nq | completed | 40/40 | 19/20 | 20/20 | 19 | 0 | 1 | 17.9 / 18.9 | 0.79 | 13/19 |
| quick-fast | completed | 40/40 | 19/20 | 20/20 | 19 | 0 | 1 | 12.8 / 10.2 | 0.85 | 15/19 |
| loop-dev | completed | 48/48 | 17/24 | 22/24 | 16 | 1 | 6 | 13.9 / 14.0 | 0.93 | 9/16 |
| **All** | | 128 | **55/64** | **62/64** | 54 | 1 | 8 | 16.4 / 12.8 | **0.85** | 37/54 |

## Change since the previous campaign (20261109T041313Z-4771449)

Previous run: model vllm-qwen38-27b, code 4771449.

| Suite | Pi passed | Devstill passed | Time ratio |
| --- | --- | --- | --- |
| nq | 9/10 → 19/20 | 10/10 → 20/20 | 0.8 → 0.79 |
| quick-fast | 10/10 → 19/20 | 10/10 → 20/20 | 0.61 → 0.85 |
| loop-dev | 11/12 → 17/24 | 11/12 → 22/24 | 0.98 → 0.93 |

Cases whose outcome changed:

- nq / nq06 (Pi): 1/1 → 1/2
- nq / nq10 (Pi): 0/1 → 2/2
- quick-fast / w_description (Pi): 1/1 → 1/2
- loop-dev / xc01 (Pi): 1/1 → 0/2
- loop-dev / xc03 (Pi): 1/1 → 1/2
- loop-dev / xc09 (Pi): 1/1 → 1/2
- loop-dev / xc11 (Pi): 1/1 → 0/2
- loop-dev / xc11 (Devstill): 0/1 → 1/2
- loop-dev / xw11 (Pi): 0/1 → 1/2
- loop-dev / xw11 (Devstill): 1/1 → 1/2

## What went wrong

### Devstill failures: improvement targets

| Suite | Case | Repeat | Arm | Category | Detail | Trial files |
| --- | --- | ---: | --- | --- | --- | --- |
| loop-dev | xc11 | 0 | Devstill | wrong_fix:behavior_check | click.exceptions.BadParameter: Path '~/run.sh' is not executable. | `suites/loop-dev/xc11-0-runtime` |
| loop-dev | xw11 | 0 | Devstill | no_change | turn_limit: Not finished: your latest check also passes on the original code, so it does not test the requested change. Add assertions that fail without your change and pass with it, run them… | `suites/loop-dev/xw11-0-runtime` |

### Pi failures (for comparison)

| Suite | Case | Repeat | Arm | Category | Detail | Trial files |
| --- | --- | ---: | --- | --- | --- | --- |
| nq | nq06 | 1 | Pi | no_change | agent ended its turn after 1 tool call(s) without editing (stop reason: stop) | `suites/nq/nq06-1-pi` |
| quick-fast | w_description | 0 | Pi | no_change | agent ended its turn after 2 tool call(s) without editing (stop reason: stop) | `suites/quick-fast/w_description-0-pi` |
| loop-dev | xc01 | 0 | Pi | wrong_fix:regression_tests | FAILED tests/test_imports.py::test_light_imports - AssertionError: assert 'ma... | `suites/loop-dev/xc01-0-pi` |
| loop-dev | xc01 | 1 | Pi | wrong_fix:regression_tests | FAILED tests/test_imports.py::test_light_imports - AssertionError: assert 'ma... | `suites/loop-dev/xc01-1-pi` |
| loop-dev | xc03 | 0 | Pi | timeout_after_correct_edit | edit was correct but the agent did not stop in time | `suites/loop-dev/xc03-0-pi` |
| loop-dev | xc09 | 1 | Pi | timeout_after_correct_edit | edit was correct but the agent did not stop in time | `suites/loop-dev/xc09-1-pi` |
| loop-dev | xc11 | 0 | Pi | wrong_fix:behavior_check | click.exceptions.BadParameter: Path '/tmp/tmpd5dntpcw/run.sh' is not executable. | `suites/loop-dev/xc11-0-pi` |
| loop-dev | xc11 | 1 | Pi | wrong_fix:behavior_check | click.exceptions.BadParameter: Path '~/run.sh' is not executable. | `suites/loop-dev/xc11-1-pi` |
| loop-dev | xw11 | 1 | Pi | timeout | no finished change within the time limit | `suites/loop-dev/xw11-1-pi` |

### Failure counts

| Arm | Category | Trials |
| --- | --- | ---: |
| Devstill | no_change | 1 |
| Devstill | wrong_fix:behavior_check | 1 |
| Pi | no_change | 2 |
| Pi | timeout | 1 |
| Pi | timeout_after_correct_edit | 2 |
| Pi | wrong_fix:behavior_check | 2 |
| Pi | wrong_fix:regression_tests | 2 |

## Code quality (trials that changed code)

Size ratio compares changed lines with the reference fix. Lint counts new ruff findings (default rules, project settings ignored) in changed Python files.

| Suite | Arm | Trials | Median changed lines | Median size vs reference | Touched files beyond the reference | Touched tests | New lint findings |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| nq | Pi | 19 | 6 | 2.0 | 1 | 1 | 0 |
| nq | Devstill | 20 | 5.0 | 1.0 | 0 | 0 | 1 |
| quick-fast | Pi | 19 | 3 | 1.0 | 0 | 1 | 0 |
| quick-fast | Devstill | 20 | 2.0 | 1.0 | 0 | 0 | 0 |
| loop-dev | Pi | 24 | 13.5 | 1.11 | 2 | 8 | 4 |
| loop-dev | Devstill | 23 | 7 | 1.0 | 1 | 4 | 3 |

## Per case

### nq

| Case | Pi passed | Devstill passed | Pi median s | Devstill median s |
| --- | ---: | ---: | ---: | ---: |
| nq01 | 2/2 | 2/2 | 12.2 | 15.7 |
| nq02 | 2/2 | 2/2 | 17.0 | 15.5 |
| nq03 | 2/2 | 2/2 | 21.7 | 27.9 |
| nq04 | 2/2 | 2/2 | 14.6 | 20.0 |
| nq05 | 2/2 | 2/2 | 22.2 | 15.9 |
| nq06 | 1/2 | 2/2 | 62.1 | 31.9 |
| nq07 | 2/2 | 2/2 | 24.2 | 22.3 |
| nq08 | 2/2 | 2/2 | 18.0 | 11.9 |
| nq09 | 2/2 | 2/2 | 18.1 | 12.8 |
| nq10 | 2/2 | 2/2 | 19.0 | 26.1 |

### quick-fast

| Case | Pi passed | Devstill passed | Pi median s | Devstill median s |
| --- | ---: | ---: | ---: | ---: |
| w_age | 2/2 | 2/2 | 18.7 | 9.9 |
| w_close | 2/2 | 2/2 | 11.7 | 10.8 |
| w_description | 1/2 | 2/2 | 14.6 | 9.6 |
| w_header_lines | 2/2 | 2/2 | 14.7 | 14.2 |
| w_hop_whitespace | 2/2 | 2/2 | 16.5 | 9.3 |
| w_parse_age_types | 2/2 | 2/2 | 13.6 | 11.3 |
| w_repr | 2/2 | 2/2 | 14.3 | 10.5 |
| w_salt | 2/2 | 2/2 | 11.6 | 9.9 |
| w_secure_pathlike | 2/2 | 2/2 | 15.3 | 14.0 |
| w_storage_truth | 2/2 | 2/2 | 11.2 | 11.9 |

### loop-dev

| Case | Pi passed | Devstill passed | Pi median s | Devstill median s |
| --- | ---: | ---: | ---: | ---: |
| xc01 | 0/2 | 2/2 | – | 29.5 |
| xc03 | 1/2 | 2/2 | 109.8 | 90.0 |
| xc05 | 2/2 | 2/2 | 49.5 | 87.1 |
| xc07 | 2/2 | 2/2 | 9.9 | 5.7 |
| xc09 | 1/2 | 2/2 | 94.2 | 34.7 |
| xc11 | 0/2 | 1/2 | – | 45.4 |
| xw01 | 2/2 | 2/2 | 8.2 | 7.2 |
| xw03 | 2/2 | 2/2 | 10.0 | 8.7 |
| xw05 | 2/2 | 2/2 | 20.3 | 27.3 |
| xw07 | 2/2 | 2/2 | 74.5 | 29.4 |
| xw09 | 2/2 | 2/2 | 11.4 | 13.7 |
| xw11 | 1/2 | 1/2 | 57.1 | 37.2 |

## How this was run

- Command: `bench_suite.py run --suite nq --suite quick-fast --suite loop-dev --repeats 2 --note loop improvements 8bdf9f3, remote endpoint llm.codemonkey.study, 2 repeats`
- Host: USNBRosenbergJ; tools: git git version 2.43.0, node v24.14.1, pytest pytest 8.3.5, python Python 3.12.3, ruff ruff 0.17.4
- nq: repeats 2, timeout 120 s, arms pi,runtime, dataset `benchmarks/retrieval/native_quick_fresh.v1.json`
- quick-fast: repeats 2, timeout 120 s, arms pi,runtime, dataset `benchmarks/retrieval/quick_fast.v1.json`
- loop-dev: repeats 2, timeout 120 s, arms pi,runtime, dataset `benchmarks/retrieval/quick_loop_dev.v1.json`
- Rebuild or check this report: `python -m benchmarks.bench_suite report .devstill/bench-history/20261109T181124Z-8bdf9f3 [--check]`

Single trials vary between runs of the same code (sampling, server batching, timing), so judge changes by pass counts over repeats and across suites, not by one case.
