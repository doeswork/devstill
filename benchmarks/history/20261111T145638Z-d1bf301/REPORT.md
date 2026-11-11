# Benchmark campaign 20261111T145638Z-d1bf301

**Status:** completed · **Started:** 2026-11-11T14:56:38+00:00 · **Finished:** 2026-11-11T20:37:33+00:00 (5.68 h)

**Code:** bench-test-ui-ux @ d1bf301 · **Model:** vllm-qwen38-27b at https://llm.codemonkey.study/v1 · **Pi:** 1.5.0 · **Sampling:** default

**Note:** Full Python + React check after round 2 (b0f9490), 120 s, 2 repeats

## Results

Pass = the agent finished and the independent acceptance check passed (hidden behaviour check plus protected tests; SWE-bench: the official grader's `resolved`). Times are worker wall seconds; the time ratio is the median of Devstill/Pi over cases both passed (below 1 = Devstill faster).

| Suite | Status | Trials | Pi passed | Devstill passed | Both | Pi only | Devstill only | Median s, shared (Pi / Devstill) | Time ratio | Devstill faster |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | ---: | ---: |
| nq | completed | 40/40 | 14/20 | 20/20 | 14 | 0 | 6 | 14.3 / 14.7 | 1.04 | 7/14 |
| quick-fast | completed | 40/40 | 20/20 | 20/20 | 20 | 0 | 0 | 11.5 / 9.0 | 0.77 | 15/20 |
| loop-dev | completed | 48/48 | 20/24 | 22/24 | 20 | 0 | 2 | 14.3 / 17.0 | 1.05 | 10/20 |
| loop-holdout | completed | 48/48 | 18/24 | 19/24 | 17 | 1 | 2 | 26.5 / 18.4 | 0.99 | 9/17 |
| react-dev | completed | 48/48 | 9/24 | 17/24 | 8 | 1 | 9 | 44.5 / 50.4 | 0.71 | 6/8 |
| react-holdout | completed | 48/48 | 12/24 | 18/24 | 9 | 3 | 9 | 65.7 / 33.2 | 0.64 | 6/9 |
| **All** | | 272 | **93/136** | **116/136** | 88 | 5 | 28 | 15.9 / 15.0 | **0.86** | 53/88 |

## Change since the previous campaign (20261111T074709Z-32f350a)

Previous run: model vllm-qwen38-27b, code 32f350a.

| Suite | Pi passed | Devstill passed | Time ratio |
| --- | --- | --- | --- |
| react-holdout | 16/24 → 12/24 | 18/24 → 18/24 | 0.84 → 0.64 |

Cases whose outcome changed:

- react-holdout / zh02 (Pi): 2/2 → 0/2
- react-holdout / zh02 (Devstill): 2/2 → 1/2
- react-holdout / zh03 (Devstill): 1/2 → 2/2
- react-holdout / zh04 (Pi): 1/2 → 2/2
- react-holdout / zh04 (Devstill): 1/2 → 0/2
- react-holdout / zh08 (Pi): 1/2 → 0/2
- react-holdout / zh09 (Pi): 2/2 → 1/2
- react-holdout / zh09 (Devstill): 1/2 → 2/2
- react-holdout / zh12 (Pi): 1/2 → 0/2

## What went wrong

### Devstill failures: improvement targets

| Suite | Case | Repeat | Arm | Category | Detail | Trial files |
| --- | --- | ---: | --- | --- | --- | --- |
| loop-dev | xc11 | 0 | Devstill | wrong_fix:behavior_check | click.exceptions.BadParameter: Path '~/run.sh' is not executable. | `suites/loop-dev/xc11-0-runtime` |
| loop-dev | xc11 | 1 | Devstill | wrong_fix:behavior_check | click.exceptions.BadParameter: Path '~/run.sh' is not executable. | `suites/loop-dev/xc11-1-runtime` |
| loop-holdout | xc02 | 1 | Devstill | no_change | turn_limit: No verified change within 20 model calls | `suites/loop-holdout/xc02-1-runtime` |
| loop-holdout | xc04 | 1 | Devstill | no_change | turn_limit: Not finished: your latest check also passes on the original code, so it does not test the requested change. Add assertions that fail without your change and pass with it, run them… | `suites/loop-holdout/xc04-1-runtime` |
| loop-holdout | xc10 | 1 | Devstill | no_change | turn_limit: No verified change within 20 model calls | `suites/loop-holdout/xc10-1-runtime` |
| loop-holdout | xc12 | 0 | Devstill | no_change | turn_limit: No verified change within 20 model calls | `suites/loop-holdout/xc12-0-runtime` |
| loop-holdout | xw12 | 1 | Devstill | no_change | turn_limit: No verified change within 20 model calls | `suites/loop-holdout/xw12-1-runtime` |
| react-dev | zd01 | 1 | Devstill | no_change | turn_limit: No verified change within 30 model calls | `suites/react-dev/zd01-1-runtime` |
| react-dev | zd02 | 0 | Devstill | wrong_fix:regression_tests | JSON report written to /tmp/devstill-accept-2y9u_6yb/vitest-report.json | `suites/react-dev/zd02-0-runtime` |
| react-dev | zd06 | 0 | Devstill | no_change | deadline: Tests still fail: src/traditional.ts(53,9): error TS2322: Type 'StateSlice' is not assigna … d to 'TState'., src/traditional.ts(59,17): error TS2322: Type 'TState' is not assignable t | `suites/react-dev/zd06-0-runtime` |
| react-dev | zd08 | 0 | Devstill | no_change | deadline: No verified change before the deadline | `suites/react-dev/zd08-0-runtime` |
| react-dev | zd08 | 1 | Devstill | wrong_fix:regression_tests | JSON report written to /tmp/devstill-accept-atad4ddx/vitest-report.json | `suites/react-dev/zd08-1-runtime` |
| react-dev | zd09 | 0 | Devstill | wrong_fix:regression_tests | JSON report written to /tmp/devstill-accept-u11sk999/vitest-report.json | `suites/react-dev/zd09-0-runtime` |
| react-dev | zd12 | 0 | Devstill | wrong_fix:regression_tests | JSON report written to /tmp/devstill-accept-mqe_nfho/vitest-report.json | `suites/react-dev/zd12-0-runtime` |
| react-holdout | zh02 | 1 | Devstill | wrong_fix:regression_tests | JSON report written to /tmp/devstill-accept-34v90gj_/vitest-report.json | `suites/react-holdout/zh02-1-runtime` |
| react-holdout | zh04 | 0 | Devstill | wrong_fix:regression_tests | JSON report written to /tmp/devstill-accept-r6clzig0/vitest-report.json | `suites/react-holdout/zh04-0-runtime` |
| react-holdout | zh04 | 1 | Devstill | no_change | deadline: No verified change before the deadline | `suites/react-holdout/zh04-1-runtime` |
| react-holdout | zh06 | 0 | Devstill | no_change | deadline: No verified change before the deadline | `suites/react-holdout/zh06-0-runtime` |
| react-holdout | zh06 | 1 | Devstill | no_change | deadline: No verified change before the deadline | `suites/react-holdout/zh06-1-runtime` |
| react-holdout | zh10 | 0 | Devstill | wrong_fix:regression_tests | JSON report written to /tmp/devstill-accept-p3gw2awk/vitest-report.json | `suites/react-holdout/zh10-0-runtime` |

### Pi failures (for comparison)

| Suite | Case | Repeat | Arm | Category | Detail | Trial files |
| --- | --- | ---: | --- | --- | --- | --- |
| nq | nq03 | 0 | Pi | wrong_fix:behavior_check | TypeError: unhashable type: 'list' | `suites/nq/nq03-0-pi` |
| nq | nq06 | 0 | Pi | no_change | agent ended its turn after 1 tool call(s) without editing (stop reason: stop) | `suites/nq/nq06-0-pi` |
| nq | nq06 | 1 | Pi | no_change | agent ended its turn after 1 tool call(s) without editing (stop reason: stop) | `suites/nq/nq06-1-pi` |
| nq | nq09 | 0 | Pi | wrong_fix:behavior_check | ModuleNotFoundError: No module named 'werkzeug.header_names' | `suites/nq/nq09-0-pi` |
| nq | nq10 | 0 | Pi | no_change | agent ended its turn after 1 tool call(s) without editing (stop reason: stop) | `suites/nq/nq10-0-pi` |
| nq | nq10 | 1 | Pi | wrong_fix:behavior_check | AssertionError | `suites/nq/nq10-1-pi` |
| loop-dev | xc11 | 0 | Pi | wrong_fix:behavior_check | click.exceptions.BadParameter: Path '~/run.sh' is not executable. | `suites/loop-dev/xc11-0-pi` |
| loop-dev | xc11 | 1 | Pi | wrong_fix:behavior_check | click.exceptions.BadParameter: Path '~/run.sh' is not executable. | `suites/loop-dev/xc11-1-pi` |
| loop-dev | xw11 | 0 | Pi | timeout_after_correct_edit | edit was correct but the agent did not stop in time | `suites/loop-dev/xw11-0-pi` |
| loop-dev | xw11 | 1 | Pi | timeout_after_correct_edit | edit was correct but the agent did not stop in time | `suites/loop-dev/xw11-1-pi` |
| loop-holdout | xc02 | 1 | Pi | no_change | agent ended its turn after 3 tool call(s) without editing (stop reason: stop) | `suites/loop-holdout/xc02-1-pi` |
| loop-holdout | xc04 | 1 | Pi | wrong_fix:behavior_check | AssertionError: (0, '', 'Traceback (most recent call last):\n  File "<string>", line 72, in <module>\nAssertionError: [\'Usage: three [OPTIONS]\', "Try \'three --help\' for help."… | `suites/loop-holdout/xc04-1-pi` |
| loop-holdout | xc08 | 0 | Pi | wrong_fix:behavior_check | AssertionError: /tmp/tmpt4vzrluc/home/.foo-bar | `suites/loop-holdout/xc08-0-pi` |
| loop-holdout | xc10 | 1 | Pi | timeout_after_correct_edit | edit was correct but the agent did not stop in time | `suites/loop-holdout/xc10-1-pi` |
| loop-holdout | xc12 | 0 | Pi | timeout | no finished change within the time limit | `suites/loop-holdout/xc12-0-pi` |
| loop-holdout | xw12 | 0 | Pi | timeout | no finished change within the time limit | `suites/loop-holdout/xw12-0-pi` |
| react-dev | zd01 | 1 | Pi | wrong_fix:behavior_check | 'T' could be instantiated with an arbitrary type which could be unrelated to 'Partial<T>'. | `suites/react-dev/zd01-1-pi` |
| react-dev | zd03 | 0 | Pi | timeout_after_correct_edit | edit was correct but the agent did not stop in time | `suites/react-dev/zd03-0-pi` |
| react-dev | zd03 | 1 | Pi | timeout_after_correct_edit | edit was correct but the agent did not stop in time | `suites/react-dev/zd03-1-pi` |
| react-dev | zd04 | 1 | Pi | wrong_fix:behavior_check | src/react/shallow.ts(46,10): error TS18048: 'cacheRef.current' is possibly 'undefined'. | `suites/react-dev/zd04-1-pi` |
| react-dev | zd05 | 1 | Pi | timeout | no finished change within the time limit | `suites/react-dev/zd05-1-pi` |
| react-dev | zd06 | 0 | Pi | timeout | no finished change within the time limit | `suites/react-dev/zd06-0-pi` |
| react-dev | zd06 | 1 | Pi | timeout_after_correct_edit | edit was correct but the agent did not stop in time | `suites/react-dev/zd06-1-pi` |
| react-dev | zd08 | 0 | Pi | timeout | no finished change within the time limit | `suites/react-dev/zd08-0-pi` |
| react-dev | zd08 | 1 | Pi | timeout | no finished change within the time limit | `suites/react-dev/zd08-1-pi` |
| react-dev | zd09 | 0 | Pi | timeout | no finished change within the time limit | `suites/react-dev/zd09-0-pi` |
| react-dev | zd09 | 1 | Pi | timeout | no finished change within the time limit | `suites/react-dev/zd09-1-pi` |
| react-dev | zd10 | 0 | Pi | timeout | no finished change within the time limit | `suites/react-dev/zd10-0-pi` |
| react-dev | zd10 | 1 | Pi | timeout | no finished change within the time limit | `suites/react-dev/zd10-1-pi` |
| react-dev | zd12 | 0 | Pi | timeout_after_correct_edit | edit was correct but the agent did not stop in time | `suites/react-dev/zd12-0-pi` |
| react-dev | zd12 | 1 | Pi | timeout | no finished change within the time limit | `suites/react-dev/zd12-1-pi` |
| react-holdout | zh01 | 1 | Pi | wrong_fix:behavior_check | No index signature with a parameter of type 'string' was found on type '{}'. | `suites/react-holdout/zh01-1-pi` |
| react-holdout | zh02 | 0 | Pi | wrong_fix:regression_tests | JSON report written to /tmp/devstill-accept-e3bgqcyf/vitest-report.json | `suites/react-holdout/zh02-0-pi` |
| react-holdout | zh02 | 1 | Pi | timeout | no finished change within the time limit | `suites/react-holdout/zh02-1-pi` |
| react-holdout | zh06 | 0 | Pi | timeout | no finished change within the time limit | `suites/react-holdout/zh06-0-pi` |
| react-holdout | zh06 | 1 | Pi | timeout | no finished change within the time limit | `suites/react-holdout/zh06-1-pi` |
| react-holdout | zh08 | 0 | Pi | timeout | no finished change within the time limit | `suites/react-holdout/zh08-0-pi` |
| react-holdout | zh08 | 1 | Pi | timeout | no finished change within the time limit | `suites/react-holdout/zh08-1-pi` |
| react-holdout | zh09 | 1 | Pi | timeout | no finished change within the time limit | `suites/react-holdout/zh09-1-pi` |
| react-holdout | zh10 | 1 | Pi | timeout_after_correct_edit | edit was correct but the agent did not stop in time | `suites/react-holdout/zh10-1-pi` |
| react-holdout | zh11 | 1 | Pi | timeout_after_correct_edit | edit was correct but the agent did not stop in time | `suites/react-holdout/zh11-1-pi` |
| react-holdout | zh12 | 0 | Pi | timeout | no finished change within the time limit | `suites/react-holdout/zh12-0-pi` |
| react-holdout | zh12 | 1 | Pi | timeout_after_correct_edit | edit was correct but the agent did not stop in time | `suites/react-holdout/zh12-1-pi` |

### Failure counts

| Arm | Category | Trials |
| --- | --- | ---: |
| Devstill | no_change | 11 |
| Devstill | wrong_fix:behavior_check | 2 |
| Devstill | wrong_fix:regression_tests | 7 |
| Pi | no_change | 4 |
| Pi | timeout | 18 |
| Pi | timeout_after_correct_edit | 10 |
| Pi | wrong_fix:behavior_check | 10 |
| Pi | wrong_fix:regression_tests | 1 |

## Code quality (trials that changed code)

Size ratio compares changed lines with the reference fix. Lint counts new ruff findings (default rules, project settings ignored) in changed Python files.

| Suite | Arm | Trials | Median changed lines | Median size vs reference | Touched files beyond the reference | Touched tests | New lint findings |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| nq | Pi | 17 | 6 | 1.75 | 1 | 1 | 1 |
| nq | Devstill | 20 | 7.0 | 2.0 | 0 | 0 | 0 |
| quick-fast | Pi | 20 | 3.0 | 1.0 | 0 | 1 | 0 |
| quick-fast | Devstill | 20 | 2.0 | 1.0 | 0 | 0 | 0 |
| loop-dev | Pi | 24 | 9.0 | 1.0 | 2 | 6 | 1 |
| loop-dev | Devstill | 24 | 7.5 | 1.0 | 2 | 4 | 1 |
| loop-holdout | Pi | 23 | 16 | 1.25 | 0 | 4 | 6 |
| loop-holdout | Devstill | 19 | 9 | 1.09 | 1 | 2 | 5 |
| react-dev | Pi | 24 | 87.0 | 9.21 | 2 | 18 | 0 |
| react-dev | Devstill | 21 | 21 | 1.28 | 0 | 0 | 0 |
| react-holdout | Pi | 24 | 59.0 | 5.27 | 3 | 16 | 0 |
| react-holdout | Devstill | 21 | 14 | 1.17 | 1 | 0 | 0 |

## Per case

### nq

| Case | Pi passed | Devstill passed | Pi median s | Devstill median s |
| --- | ---: | ---: | ---: | ---: |
| nq01 | 2/2 | 2/2 | 8.9 | 9.5 |
| nq02 | 2/2 | 2/2 | 13.5 | 17.3 |
| nq03 | 1/2 | 2/2 | 42.3 | 14.9 |
| nq04 | 2/2 | 2/2 | 14.7 | 12.1 |
| nq05 | 2/2 | 2/2 | 14.5 | 18.1 |
| nq06 | 0/2 | 2/2 | – | 23.4 |
| nq07 | 2/2 | 2/2 | 25.6 | 14.9 |
| nq08 | 2/2 | 2/2 | 15.3 | 11.5 |
| nq09 | 1/2 | 2/2 | 10.9 | 15.7 |
| nq10 | 0/2 | 2/2 | – | 14.4 |

### quick-fast

| Case | Pi passed | Devstill passed | Pi median s | Devstill median s |
| --- | ---: | ---: | ---: | ---: |
| w_age | 2/2 | 2/2 | 13.7 | 10.1 |
| w_close | 2/2 | 2/2 | 11.5 | 9.2 |
| w_description | 2/2 | 2/2 | 11.1 | 9.9 |
| w_header_lines | 2/2 | 2/2 | 13.8 | 8.7 |
| w_hop_whitespace | 2/2 | 2/2 | 14.5 | 7.9 |
| w_parse_age_types | 2/2 | 2/2 | 12.4 | 9.0 |
| w_repr | 2/2 | 2/2 | 22.4 | 8.4 |
| w_salt | 2/2 | 2/2 | 10.1 | 8.2 |
| w_secure_pathlike | 2/2 | 2/2 | 11.2 | 11.2 |
| w_storage_truth | 2/2 | 2/2 | 8.9 | 10.0 |

### loop-dev

| Case | Pi passed | Devstill passed | Pi median s | Devstill median s |
| --- | ---: | ---: | ---: | ---: |
| xc01 | 2/2 | 2/2 | 41.7 | 17.0 |
| xc03 | 2/2 | 2/2 | 52.0 | 85.4 |
| xc05 | 2/2 | 2/2 | 27.8 | 65.1 |
| xc07 | 2/2 | 2/2 | 7.5 | 3.7 |
| xc09 | 2/2 | 2/2 | 14.3 | 19.8 |
| xc11 | 0/2 | 0/2 | – | – |
| xw01 | 2/2 | 2/2 | 5.5 | 4.9 |
| xw03 | 2/2 | 2/2 | 5.2 | 12.6 |
| xw05 | 2/2 | 2/2 | 15.9 | 28.3 |
| xw07 | 2/2 | 2/2 | 63.2 | 26.9 |
| xw09 | 2/2 | 2/2 | 11.6 | 11.8 |
| xw11 | 0/2 | 2/2 | – | 62.0 |

### loop-holdout

| Case | Pi passed | Devstill passed | Pi median s | Devstill median s |
| --- | ---: | ---: | ---: | ---: |
| xc02 | 1/2 | 1/2 | 52.8 | 43.4 |
| xc04 | 1/2 | 1/2 | 33.9 | 58.3 |
| xc06 | 2/2 | 2/2 | 17.2 | 27.6 |
| xc08 | 1/2 | 2/2 | 89.6 | 17.0 |
| xc10 | 1/2 | 1/2 | 52.4 | 65.1 |
| xc12 | 1/2 | 1/2 | 60.1 | 51.0 |
| xw02 | 2/2 | 2/2 | 10.2 | 16.3 |
| xw04 | 2/2 | 2/2 | 47.6 | 17.2 |
| xw06 | 2/2 | 2/2 | 20.5 | 12.6 |
| xw08 | 2/2 | 2/2 | 23.2 | 14.4 |
| xw10 | 2/2 | 2/2 | 16.2 | 22.0 |
| xw12 | 1/2 | 1/2 | 43.9 | 49.4 |

### react-dev

| Case | Pi passed | Devstill passed | Pi median s | Devstill median s |
| --- | ---: | ---: | ---: | ---: |
| zd01 | 1/2 | 1/2 | 48.0 | 33.7 |
| zd02 | 2/2 | 1/2 | 68.3 | 29.9 |
| zd03 | 0/2 | 2/2 | – | 74.2 |
| zd04 | 1/2 | 2/2 | 95.9 | 64.0 |
| zd05 | 1/2 | 2/2 | 89.1 | 57.6 |
| zd06 | 0/2 | 1/2 | – | 112.6 |
| zd07 | 2/2 | 2/2 | 30.5 | 75.8 |
| zd08 | 0/2 | 0/2 | – | – |
| zd09 | 0/2 | 1/2 | – | 40.8 |
| zd10 | 0/2 | 2/2 | – | 100.5 |
| zd11 | 2/2 | 2/2 | 35.3 | 24.1 |
| zd12 | 0/2 | 1/2 | – | 111.0 |

### react-holdout

| Case | Pi passed | Devstill passed | Pi median s | Devstill median s |
| --- | ---: | ---: | ---: | ---: |
| zh01 | 1/2 | 2/2 | 47.2 | 24.0 |
| zh02 | 0/2 | 1/2 | – | 73.3 |
| zh03 | 2/2 | 2/2 | 72.2 | 65.5 |
| zh04 | 2/2 | 0/2 | 70.1 | – |
| zh05 | 2/2 | 2/2 | 53.1 | 36.1 |
| zh06 | 0/2 | 0/2 | – | – |
| zh07 | 2/2 | 2/2 | 33.2 | 22.8 |
| zh08 | 0/2 | 2/2 | – | 76.1 |
| zh09 | 1/2 | 2/2 | 98.0 | 91.9 |
| zh10 | 1/2 | 1/2 | 93.0 | 61.6 |
| zh11 | 1/2 | 2/2 | 76.1 | 29.9 |
| zh12 | 0/2 | 2/2 | – | 67.0 |

## How this was run

- Command: `bench_suite.py run --tag core --tag react --repeats 2 --note Full Python + React check after round 2 (b0f9490), 120 s, 2 repeats`
- Host: USNBRosenbergJ; tools: git git version 2.43.0, node v24.14.1, pytest pytest 8.3.5, python Python 3.12.3, ruff ruff 0.17.4
- nq: repeats 2, timeout 120 s, arms pi,runtime, dataset `benchmarks/retrieval/native_quick_fresh.v1.json`
- quick-fast: repeats 2, timeout 120 s, arms pi,runtime, dataset `benchmarks/retrieval/quick_fast.v1.json`
- loop-dev: repeats 2, timeout 120 s, arms pi,runtime, dataset `benchmarks/retrieval/quick_loop_dev.v1.json`
- loop-holdout: repeats 2, timeout 120 s, arms pi,runtime, dataset `benchmarks/retrieval/quick_loop_holdout.v1.json`
- react-dev: repeats 2, timeout 120 s, arms pi,runtime, dataset `benchmarks/retrieval/zustand_dev.v1.json`
- react-holdout: repeats 2, timeout 120 s, arms pi,runtime, dataset `benchmarks/retrieval/zustand_holdout.v1.json`
- Rebuild or check this report: `python -m benchmarks.bench_suite report .devstill/bench-history/20261111T145638Z-d1bf301 [--check]`

Single trials vary between runs of the same code (sampling, server batching, timing), so judge changes by pass counts over repeats and across suites, not by one case.
