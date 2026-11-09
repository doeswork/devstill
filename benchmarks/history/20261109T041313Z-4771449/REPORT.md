# Benchmark campaign 20261109T041313Z-4771449

**Status:** completed · **Started:** 2026-11-09T04:13:13+00:00 · **Finished:** 2026-11-09T05:38:08+00:00 (1.42 h)

**Code:** bench-test-ui-ux @ 4771449 · **Model:** vllm-qwen38-27b at http://192.168.0.131:1234/v1 · **Pi:** 1.5.0 · **Sampling:** default

**Note:** overnight run; default sampling (relaunch after --purpose fix)

## Results

Pass = the agent finished and the independent acceptance check passed (hidden behaviour check plus protected tests; SWE-bench: the official grader's `resolved`). Times are worker wall seconds; the time ratio is the median of Devstill/Pi over cases both passed (below 1 = Devstill faster).

| Suite | Status | Trials | Pi passed | Devstill passed | Both | Pi only | Devstill only | Median s, shared (Pi / Devstill) | Time ratio | Devstill faster |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | ---: | ---: |
| nq | completed | 20/20 | 9/10 | 10/10 | 9 | 0 | 1 | 16.8 / 15.1 | 0.8 | 7/9 |
| quick-fast | completed | 20/20 | 10/10 | 10/10 | 10 | 0 | 0 | 14.8 / 8.6 | 0.61 | 10/10 |
| loop-dev | completed | 24/24 | 11/12 | 11/12 | 10 | 1 | 1 | 16.3 / 19.8 | 0.98 | 5/10 |
| loop-holdout | completed | 48/48 | 16/24 | 18/24 | 13 | 3 | 5 | 18.0 / 21.9 | 1.07 | 6/13 |
| swebench-small | skipped: Docker Desktop's WSL integration is off for this distro (Docker Desktop -> Settings -> Re… | 0 | – | – | – | – | – | – | – | – |
| **All** | | 112 | **46/56** | **49/56** | 42 | 4 | 7 | 16.9 / 14.3 | **0.83** | 28/42 |

## What went wrong

### Devstill failures: improvement targets

| Suite | Case | Repeat | Arm | Category | Detail | Trial files |
| --- | --- | ---: | --- | --- | --- | --- |
| loop-dev | xc11 | 0 | Devstill | wrong_fix:behavior_check | click.exceptions.BadParameter: Path '~/run.sh' is not executable. | `suites/loop-dev/xc11-0-runtime` |
| loop-holdout | xc02 | 1 | Devstill | no_change | turn_limit: No verified change within 20 model calls | `suites/loop-holdout/xc02-1-runtime` |
| loop-holdout | xc04 | 0 | Devstill | no_change | turn_limit: No verified change within 20 model calls | `suites/loop-holdout/xc04-0-runtime` |
| loop-holdout | xc04 | 1 | Devstill | no_change | turn_limit: No verified change within 20 model calls | `suites/loop-holdout/xc04-1-runtime` |
| loop-holdout | xc12 | 0 | Devstill | no_change | turn_limit: No verified change within 20 model calls | `suites/loop-holdout/xc12-0-runtime` |
| loop-holdout | xc12 | 1 | Devstill | wrong_fix:behavior_check | AssertionError: [<Option b>] | `suites/loop-holdout/xc12-1-runtime` |
| loop-holdout | xw04 | 1 | Devstill | no_change | turn_limit: No verified change within 20 model calls | `suites/loop-holdout/xw04-1-runtime` |

### Pi failures (for comparison)

| Suite | Case | Repeat | Arm | Category | Detail | Trial files |
| --- | --- | ---: | --- | --- | --- | --- |
| nq | nq10 | 0 | Pi | timeout_after_correct_edit | edit was correct but the agent did not stop in time | `suites/nq/nq10-0-pi` |
| loop-dev | xw11 | 0 | Pi | timeout_after_correct_edit | edit was correct but the agent did not stop in time | `suites/loop-dev/xw11-0-pi` |
| loop-holdout | xc02 | 0 | Pi | no_change | agent ended its turn after 2 tool call(s) without editing (stop reason: stop) | `suites/loop-holdout/xc02-0-pi` |
| loop-holdout | xc02 | 1 | Pi | timeout | no finished change within the time limit | `suites/loop-holdout/xc02-1-pi` |
| loop-holdout | xc04 | 1 | Pi | wrong_fix:behavior_check | AssertionError: (0, '', 'Traceback (most recent call last):\n  File "<string>", line 72, in <module>\nAssertionError: [\'Usage: three [OPTIONS]\', "Try \'three --help\' for help."… | `suites/loop-holdout/xc04-1-pi` |
| loop-holdout | xw04 | 0 | Pi | timeout_after_correct_edit | edit was correct but the agent did not stop in time | `suites/loop-holdout/xw04-0-pi` |
| loop-holdout | xw04 | 1 | Pi | timeout_after_correct_edit | edit was correct but the agent did not stop in time | `suites/loop-holdout/xw04-1-pi` |
| loop-holdout | xw06 | 1 | Pi | timeout_after_correct_edit | edit was correct but the agent did not stop in time | `suites/loop-holdout/xw06-1-pi` |
| loop-holdout | xw12 | 0 | Pi | timeout_after_correct_edit | edit was correct but the agent did not stop in time | `suites/loop-holdout/xw12-0-pi` |
| loop-holdout | xw12 | 1 | Pi | timeout_after_correct_edit | edit was correct but the agent did not stop in time | `suites/loop-holdout/xw12-1-pi` |

### Failure counts

| Arm | Category | Trials |
| --- | --- | ---: |
| Devstill | no_change | 5 |
| Devstill | wrong_fix:behavior_check | 2 |
| Pi | no_change | 1 |
| Pi | timeout | 1 |
| Pi | timeout_after_correct_edit | 7 |
| Pi | wrong_fix:behavior_check | 1 |

## Skipped, unfinished and excluded

- **swebench-small**: skipped — Docker Desktop's WSL integration is off for this distro (Docker Desktop -> Settings -> Resources -> WSL integration)

## Code quality (trials that changed code)

Size ratio compares changed lines with the reference fix. Lint counts new ruff findings (default rules, project settings ignored) in changed Python files.

| Suite | Arm | Trials | Median changed lines | Median size vs reference | Touched files beyond the reference | Touched tests | New lint findings |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| nq | Pi | 10 | 5.5 | 1.75 | 0 | 1 | 1 |
| nq | Devstill | 10 | 6.5 | 1.67 | 0 | 0 | 1 |
| quick-fast | Pi | 10 | 3.5 | 1.75 | 0 | 1 | 0 |
| quick-fast | Devstill | 10 | 2.0 | 1.0 | 0 | 0 | 0 |
| loop-dev | Pi | 12 | 11.0 | 1.0 | 1 | 5 | 2 |
| loop-dev | Devstill | 12 | 7.5 | 1.0 | 0 | 2 | 2 |
| loop-holdout | Pi | 23 | 14 | 1.27 | 2 | 2 | 13 |
| loop-holdout | Devstill | 19 | 10 | 1.0 | 0 | 2 | 7 |

## Per case

### nq

| Case | Pi passed | Devstill passed | Pi median s | Devstill median s |
| --- | ---: | ---: | ---: | ---: |
| nq01 | 1/1 | 1/1 | 16.1 | 8.5 |
| nq02 | 1/1 | 1/1 | 18.6 | 14.9 |
| nq03 | 1/1 | 1/1 | 27.2 | 15.1 |
| nq04 | 1/1 | 1/1 | 16.8 | 16.0 |
| nq05 | 1/1 | 1/1 | 16.8 | 15.9 |
| nq06 | 1/1 | 1/1 | 58.5 | 20.2 |
| nq07 | 1/1 | 1/1 | 19.3 | 41.3 |
| nq08 | 1/1 | 1/1 | 16.2 | 11.8 |
| nq09 | 1/1 | 1/1 | 11.5 | 13.6 |
| nq10 | 0/1 | 1/1 | – | 18.1 |

### quick-fast

| Case | Pi passed | Devstill passed | Pi median s | Devstill median s |
| --- | ---: | ---: | ---: | ---: |
| w_age | 1/1 | 1/1 | 13.1 | 8.4 |
| w_close | 1/1 | 1/1 | 11.6 | 8.7 |
| w_description | 1/1 | 1/1 | 12.1 | 10.2 |
| w_header_lines | 1/1 | 1/1 | 16.5 | 9.6 |
| w_hop_whitespace | 1/1 | 1/1 | 17.5 | 10.2 |
| w_parse_age_types | 1/1 | 1/1 | 10.4 | 8.6 |
| w_repr | 1/1 | 1/1 | 17.0 | 7.8 |
| w_salt | 1/1 | 1/1 | 17.7 | 8.0 |
| w_secure_pathlike | 1/1 | 1/1 | 23.3 | 9.5 |
| w_storage_truth | 1/1 | 1/1 | 10.8 | 8.4 |

### loop-dev

| Case | Pi passed | Devstill passed | Pi median s | Devstill median s |
| --- | ---: | ---: | ---: | ---: |
| xc01 | 1/1 | 1/1 | 53.1 | 29.1 |
| xc03 | 1/1 | 1/1 | 48.9 | 86.6 |
| xc05 | 1/1 | 1/1 | 60.6 | 66.4 |
| xc07 | 1/1 | 1/1 | 6.1 | 3.9 |
| xc09 | 1/1 | 1/1 | 18.5 | 16.0 |
| xc11 | 1/1 | 0/1 | 24.8 | – |
| xw01 | 1/1 | 1/1 | 5.6 | 4.6 |
| xw03 | 1/1 | 1/1 | 4.6 | 13.2 |
| xw05 | 1/1 | 1/1 | 14.1 | 22.4 |
| xw07 | 1/1 | 1/1 | 72.3 | 37.8 |
| xw09 | 1/1 | 1/1 | 11.9 | 17.2 |
| xw11 | 0/1 | 1/1 | – | 25.0 |

### loop-holdout

| Case | Pi passed | Devstill passed | Pi median s | Devstill median s |
| --- | ---: | ---: | ---: | ---: |
| xc02 | 0/2 | 1/2 | – | 55.9 |
| xc04 | 1/2 | 0/2 | 41.8 | – |
| xc06 | 2/2 | 2/2 | 15.8 | 21.0 |
| xc08 | 2/2 | 2/2 | 23.5 | 25.7 |
| xc10 | 2/2 | 2/2 | 58.8 | 67.2 |
| xc12 | 2/2 | 0/2 | 70.3 | – |
| xw02 | 2/2 | 2/2 | 9.3 | 11.2 |
| xw04 | 0/2 | 1/2 | – | 35.6 |
| xw06 | 1/2 | 2/2 | 21.7 | 14.6 |
| xw08 | 2/2 | 2/2 | 16.6 | 18.5 |
| xw10 | 2/2 | 2/2 | 21.2 | 17.8 |
| xw12 | 0/2 | 2/2 | – | 58.5 |

## How this was run

- Command: `bench_suite.py run --note overnight run; default sampling (relaunch after --purpose fix)`
- Host: USNBRosenbergJ; tools: git git version 2.43.0, node v24.14.1, pytest pytest 8.3.5, python Python 3.12.3, ruff ruff 0.17.4
- nq: repeats 1, timeout 120 s, arms pi,runtime, dataset `benchmarks/retrieval/native_quick_fresh.v1.json`
- quick-fast: repeats 1, timeout 120 s, arms pi,runtime, dataset `benchmarks/retrieval/quick_fast.v1.json`
- loop-dev: repeats 1, timeout 120 s, arms pi,runtime, dataset `benchmarks/retrieval/quick_loop_dev.v1.json`
- loop-holdout: repeats 2, timeout 120 s, arms pi,runtime, dataset `benchmarks/retrieval/quick_loop_holdout.v1.json`
- swebench-small: repeats 1, timeout 300 s, arms pi,runtime, dataset `benchmarks/datasets/swebench_small.v1.json`
- Rebuild or check this report: `python -m benchmarks.bench_suite report .devstill/bench-history/20261109T041313Z-4771449 [--check]`

Single trials vary between runs of the same code (sampling, server batching, timing), so judge changes by pass counts over repeats and across suites, not by one case.
