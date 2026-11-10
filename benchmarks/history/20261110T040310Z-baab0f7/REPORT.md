# Benchmark campaign 20261110T040310Z-baab0f7

**Status:** completed · **Started:** 2026-11-10T04:03:10+00:00 · **Finished:** 2026-11-10T05:29:25+00:00 (1.44 h)

**Code:** bench-test-ui-ux @ baab0f7 · **Model:** vllm-qwen38-27b at https://llm.codemonkey.study/v1 · **Pi:** 1.5.0 · **Sampling:** default

**Note:** first React/TS campaign (zustand); Devstill JS support 3aa4240, remote endpoint

## Results

Pass = the agent finished and the independent acceptance check passed (hidden behaviour check plus protected tests; SWE-bench: the official grader's `resolved`). Times are worker wall seconds; the time ratio is the median of Devstill/Pi over cases both passed (below 1 = Devstill faster).

| Suite | Status | Trials | Pi passed | Devstill passed | Both | Pi only | Devstill only | Median s, shared (Pi / Devstill) | Time ratio | Devstill faster |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | ---: | ---: |
| react-dev | completed | 48/48 | 1/24 | 2/24 | 1 | 0 | 1 | 99.4 / 26.8 | 0.27 | 1/1 |
| react-holdout | completed | 48/48 | 10/24 | 12/24 | 7 | 3 | 5 | 46.4 / 29.1 | 0.63 | 6/7 |
| **All** | | 96 | **11/48** | **14/48** | 8 | 3 | 6 | 50.8 / 27.9 | **0.6** | 7/8 |

## What went wrong

### Infrastructure (harness, model server, crashes): fix these first

| Suite | Case | Repeat | Arm | Category | Detail | Trial files |
| --- | --- | ---: | --- | --- | --- | --- |
| react-dev | zd01 | 1 | Pi | harness_error | harness_error: ValueError: Corpus dependencies missing or changed; prepare again | `suites/react-dev/zd01-1-pi` |
| react-dev | zd01 | 1 | Devstill | harness_error | harness_error: ValueError: Corpus dependencies missing or changed; prepare again | `suites/react-dev/zd01-1-runtime` |
| react-dev | zd02 | 0 | Pi | harness_error | harness_error: ValueError: Corpus dependencies missing or changed; prepare again | `suites/react-dev/zd02-0-pi` |
| react-dev | zd02 | 1 | Pi | harness_error | harness_error: ValueError: Corpus dependencies missing or changed; prepare again | `suites/react-dev/zd02-1-pi` |
| react-dev | zd02 | 1 | Devstill | harness_error | harness_error: ValueError: Corpus dependencies missing or changed; prepare again | `suites/react-dev/zd02-1-runtime` |
| react-dev | zd03 | 0 | Pi | harness_error | harness_error: ValueError: Corpus dependencies missing or changed; prepare again | `suites/react-dev/zd03-0-pi` |
| react-dev | zd03 | 0 | Devstill | harness_error | harness_error: ValueError: Corpus dependencies missing or changed; prepare again | `suites/react-dev/zd03-0-runtime` |
| react-dev | zd03 | 1 | Pi | harness_error | harness_error: ValueError: Corpus dependencies missing or changed; prepare again | `suites/react-dev/zd03-1-pi` |
| react-dev | zd03 | 1 | Devstill | harness_error | harness_error: ValueError: Corpus dependencies missing or changed; prepare again | `suites/react-dev/zd03-1-runtime` |
| react-dev | zd04 | 0 | Pi | harness_error | harness_error: ValueError: Corpus dependencies missing or changed; prepare again | `suites/react-dev/zd04-0-pi` |
| react-dev | zd04 | 0 | Devstill | harness_error | harness_error: ValueError: Corpus dependencies missing or changed; prepare again | `suites/react-dev/zd04-0-runtime` |
| react-dev | zd04 | 1 | Pi | harness_error | harness_error: ValueError: Corpus dependencies missing or changed; prepare again | `suites/react-dev/zd04-1-pi` |
| react-dev | zd04 | 1 | Devstill | harness_error | harness_error: ValueError: Corpus dependencies missing or changed; prepare again | `suites/react-dev/zd04-1-runtime` |
| react-dev | zd05 | 0 | Pi | harness_error | harness_error: ValueError: Corpus dependencies missing or changed; prepare again | `suites/react-dev/zd05-0-pi` |
| react-dev | zd05 | 0 | Devstill | harness_error | harness_error: ValueError: Corpus dependencies missing or changed; prepare again | `suites/react-dev/zd05-0-runtime` |
| react-dev | zd05 | 1 | Pi | harness_error | harness_error: ValueError: Corpus dependencies missing or changed; prepare again | `suites/react-dev/zd05-1-pi` |
| react-dev | zd05 | 1 | Devstill | harness_error | harness_error: ValueError: Corpus dependencies missing or changed; prepare again | `suites/react-dev/zd05-1-runtime` |
| react-dev | zd06 | 0 | Pi | harness_error | harness_error: ValueError: Corpus dependencies missing or changed; prepare again | `suites/react-dev/zd06-0-pi` |
| react-dev | zd06 | 0 | Devstill | harness_error | harness_error: ValueError: Corpus dependencies missing or changed; prepare again | `suites/react-dev/zd06-0-runtime` |
| react-dev | zd06 | 1 | Pi | harness_error | harness_error: ValueError: Corpus dependencies missing or changed; prepare again | `suites/react-dev/zd06-1-pi` |
| react-dev | zd06 | 1 | Devstill | harness_error | harness_error: ValueError: Corpus dependencies missing or changed; prepare again | `suites/react-dev/zd06-1-runtime` |
| react-dev | zd07 | 0 | Pi | harness_error | harness_error: ValueError: Corpus dependencies missing or changed; prepare again | `suites/react-dev/zd07-0-pi` |
| react-dev | zd07 | 0 | Devstill | harness_error | harness_error: ValueError: Corpus dependencies missing or changed; prepare again | `suites/react-dev/zd07-0-runtime` |
| react-dev | zd07 | 1 | Pi | harness_error | harness_error: ValueError: Corpus dependencies missing or changed; prepare again | `suites/react-dev/zd07-1-pi` |
| react-dev | zd07 | 1 | Devstill | harness_error | harness_error: ValueError: Corpus dependencies missing or changed; prepare again | `suites/react-dev/zd07-1-runtime` |
| react-dev | zd08 | 0 | Pi | harness_error | harness_error: ValueError: Corpus dependencies missing or changed; prepare again | `suites/react-dev/zd08-0-pi` |
| react-dev | zd08 | 0 | Devstill | harness_error | harness_error: ValueError: Corpus dependencies missing or changed; prepare again | `suites/react-dev/zd08-0-runtime` |
| react-dev | zd08 | 1 | Pi | harness_error | harness_error: ValueError: Corpus dependencies missing or changed; prepare again | `suites/react-dev/zd08-1-pi` |
| react-dev | zd08 | 1 | Devstill | harness_error | harness_error: ValueError: Corpus dependencies missing or changed; prepare again | `suites/react-dev/zd08-1-runtime` |
| react-dev | zd09 | 0 | Pi | harness_error | harness_error: ValueError: Corpus dependencies missing or changed; prepare again | `suites/react-dev/zd09-0-pi` |
| react-dev | zd09 | 0 | Devstill | harness_error | harness_error: ValueError: Corpus dependencies missing or changed; prepare again | `suites/react-dev/zd09-0-runtime` |
| react-dev | zd09 | 1 | Pi | harness_error | harness_error: ValueError: Corpus dependencies missing or changed; prepare again | `suites/react-dev/zd09-1-pi` |
| react-dev | zd09 | 1 | Devstill | harness_error | harness_error: ValueError: Corpus dependencies missing or changed; prepare again | `suites/react-dev/zd09-1-runtime` |
| react-dev | zd10 | 0 | Pi | harness_error | harness_error: ValueError: Corpus dependencies missing or changed; prepare again | `suites/react-dev/zd10-0-pi` |
| react-dev | zd10 | 0 | Devstill | harness_error | harness_error: ValueError: Corpus dependencies missing or changed; prepare again | `suites/react-dev/zd10-0-runtime` |
| react-dev | zd10 | 1 | Pi | harness_error | harness_error: ValueError: Corpus dependencies missing or changed; prepare again | `suites/react-dev/zd10-1-pi` |
| react-dev | zd10 | 1 | Devstill | harness_error | harness_error: ValueError: Corpus dependencies missing or changed; prepare again | `suites/react-dev/zd10-1-runtime` |
| react-dev | zd11 | 0 | Pi | harness_error | harness_error: ValueError: Corpus dependencies missing or changed; prepare again | `suites/react-dev/zd11-0-pi` |
| react-dev | zd11 | 0 | Devstill | harness_error | harness_error: ValueError: Corpus dependencies missing or changed; prepare again | `suites/react-dev/zd11-0-runtime` |
| react-dev | zd11 | 1 | Pi | harness_error | harness_error: ValueError: Corpus dependencies missing or changed; prepare again | `suites/react-dev/zd11-1-pi` |
| react-dev | zd11 | 1 | Devstill | harness_error | harness_error: ValueError: Corpus dependencies missing or changed; prepare again | `suites/react-dev/zd11-1-runtime` |
| react-dev | zd12 | 0 | Pi | harness_error | harness_error: ValueError: Corpus dependencies missing or changed; prepare again | `suites/react-dev/zd12-0-pi` |
| react-dev | zd12 | 0 | Devstill | harness_error | harness_error: ValueError: Corpus dependencies missing or changed; prepare again | `suites/react-dev/zd12-0-runtime` |
| react-dev | zd12 | 1 | Pi | harness_error | harness_error: ValueError: Corpus dependencies missing or changed; prepare again | `suites/react-dev/zd12-1-pi` |
| react-dev | zd12 | 1 | Devstill | harness_error | harness_error: ValueError: Corpus dependencies missing or changed; prepare again | `suites/react-dev/zd12-1-runtime` |

### Devstill failures: improvement targets

| Suite | Case | Repeat | Arm | Category | Detail | Trial files |
| --- | --- | ---: | --- | --- | --- | --- |
| react-holdout | zh04 | 0 | Devstill | no_change | turn_limit: No verified change within 20 model calls | `suites/react-holdout/zh04-0-runtime` |
| react-holdout | zh04 | 1 | Devstill | no_change | turn_limit: Your latest check fails after your last edit (re-run automatically): … </di | `suites/react-holdout/zh04-1-runtime` |
| react-holdout | zh06 | 0 | Devstill | no_change | turn_limit: No verified change within 20 model calls | `suites/react-holdout/zh06-0-runtime` |
| react-holdout | zh06 | 1 | Devstill | no_change | turn_limit: Tests still fail: tests/basic.test.tsx > can call useBoundStore with progressively more arguments, tests/basic.test.tsx > can throw an error in equality checker, tests/basic.test.… | `suites/react-holdout/zh06-1-runtime` |
| react-holdout | zh08 | 0 | Devstill | wrong_fix:regression_tests | JSON report written to /tmp/devstill-accept-40tf02aq/vitest-report.json | `suites/react-holdout/zh08-0-runtime` |
| react-holdout | zh09 | 1 | Devstill | no_change | turn_limit: Your latest check fails after your last edit (re-run automatically): … at check line 36: expect(seen).toEqual([[0, 0], [42, 0], | `suites/react-holdout/zh09-1-runtime` |
| react-holdout | zh10 | 0 | Devstill | timeout | no finished change within the time limit | `suites/react-holdout/zh10-0-runtime` |
| react-holdout | zh10 | 1 | Devstill | no_change | turn_limit: No verified change within 20 model calls | `suites/react-holdout/zh10-1-runtime` |
| react-holdout | zh11 | 0 | Devstill | no_change | turn_limit: No verified change within 20 model calls | `suites/react-holdout/zh11-0-runtime` |
| react-holdout | zh11 | 1 | Devstill | no_change | turn_limit: No verified change within 20 model calls | `suites/react-holdout/zh11-1-runtime` |
| react-holdout | zh12 | 0 | Devstill | wrong_fix:regression_tests | JSON report written to /tmp/devstill-accept-jscw_6i7/vitest-report.json | `suites/react-holdout/zh12-0-runtime` |
| react-holdout | zh12 | 1 | Devstill | no_change | turn_limit: No verified change within 20 model calls | `suites/react-holdout/zh12-1-runtime` |

### Pi failures (for comparison)

| Suite | Case | Repeat | Arm | Category | Detail | Trial files |
| --- | --- | ---: | --- | --- | --- | --- |
| react-holdout | zh02 | 0 | Pi | wrong_fix:regression_tests | JSON report written to /tmp/devstill-accept-a2cc6rtf/vitest-report.json | `suites/react-holdout/zh02-0-pi` |
| react-holdout | zh03 | 1 | Pi | timeout_after_correct_edit | edit was correct but the agent did not stop in time | `suites/react-holdout/zh03-1-pi` |
| react-holdout | zh04 | 0 | Pi | timeout | no finished change within the time limit | `suites/react-holdout/zh04-0-pi` |
| react-holdout | zh04 | 1 | Pi | timeout | no finished change within the time limit | `suites/react-holdout/zh04-1-pi` |
| react-holdout | zh05 | 0 | Pi | timeout_after_correct_edit | edit was correct but the agent did not stop in time | `suites/react-holdout/zh05-0-pi` |
| react-holdout | zh06 | 0 | Pi | timeout | no finished change within the time limit | `suites/react-holdout/zh06-0-pi` |
| react-holdout | zh06 | 1 | Pi | timeout | no finished change within the time limit | `suites/react-holdout/zh06-1-pi` |
| react-holdout | zh08 | 0 | Pi | timeout_after_correct_edit | edit was correct but the agent did not stop in time | `suites/react-holdout/zh08-0-pi` |
| react-holdout | zh08 | 1 | Pi | timeout | no finished change within the time limit | `suites/react-holdout/zh08-1-pi` |
| react-holdout | zh09 | 0 | Pi | timeout | no finished change within the time limit | `suites/react-holdout/zh09-0-pi` |
| react-holdout | zh10 | 0 | Pi | timeout | no finished change within the time limit | `suites/react-holdout/zh10-0-pi` |
| react-holdout | zh10 | 1 | Pi | timeout | no finished change within the time limit | `suites/react-holdout/zh10-1-pi` |
| react-holdout | zh12 | 0 | Pi | wrong_fix:behavior_check | 'T' could be instantiated with an arbitrary type which could be unrelated to 'T \| { dispatch: any; }'. | `suites/react-holdout/zh12-0-pi` |
| react-holdout | zh12 | 1 | Pi | timeout_after_correct_edit | edit was correct but the agent did not stop in time | `suites/react-holdout/zh12-1-pi` |

### Failure counts

| Arm | Category | Trials |
| --- | --- | ---: |
| Devstill | harness_error | 22 |
| Devstill | no_change | 9 |
| Devstill | timeout | 1 |
| Devstill | wrong_fix:regression_tests | 2 |
| Pi | harness_error | 23 |
| Pi | timeout | 8 |
| Pi | timeout_after_correct_edit | 4 |
| Pi | wrong_fix:behavior_check | 1 |
| Pi | wrong_fix:regression_tests | 1 |

## Code quality (trials that changed code)

Size ratio compares changed lines with the reference fix. Lint counts new ruff findings (default rules, project settings ignored) in changed Python files.

| Suite | Arm | Trials | Median changed lines | Median size vs reference | Touched files beyond the reference | Touched tests | New lint findings |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| react-dev | Pi | 1 | 112 | 11.2 | 0 | 1 | 0 |
| react-dev | Devstill | 2 | 23.5 | 1.16 | 0 | 0 | 0 |
| react-holdout | Pi | 24 | 58.5 | 6.76 | 2 | 15 | 0 |
| react-holdout | Devstill | 14 | 16.0 | 1.1 | 2 | 0 | 0 |

## Per case

### react-dev

| Case | Pi passed | Devstill passed | Pi median s | Devstill median s |
| --- | ---: | ---: | ---: | ---: |
| zd01 | 1/2 | 1/2 | 99.4 | 26.8 |
| zd02 | 0/2 | 1/2 | – | 76.5 |
| zd03 | 0/2 | 0/2 | – | – |
| zd04 | 0/2 | 0/2 | – | – |
| zd05 | 0/2 | 0/2 | – | – |
| zd06 | 0/2 | 0/2 | – | – |
| zd07 | 0/2 | 0/2 | – | – |
| zd08 | 0/2 | 0/2 | – | – |
| zd09 | 0/2 | 0/2 | – | – |
| zd10 | 0/2 | 0/2 | – | – |
| zd11 | 0/2 | 0/2 | – | – |
| zd12 | 0/2 | 0/2 | – | – |

### react-holdout

| Case | Pi passed | Devstill passed | Pi median s | Devstill median s |
| --- | ---: | ---: | ---: | ---: |
| zh01 | 2/2 | 2/2 | 68.8 | 29.9 |
| zh02 | 1/2 | 2/2 | 46.4 | 61.4 |
| zh03 | 1/2 | 2/2 | 104.6 | 54.2 |
| zh04 | 0/2 | 0/2 | – | – |
| zh05 | 1/2 | 2/2 | 18.9 | 62.2 |
| zh06 | 0/2 | 0/2 | – | – |
| zh07 | 2/2 | 2/2 | 43.8 | 26.3 |
| zh08 | 0/2 | 1/2 | – | 45.5 |
| zh09 | 1/2 | 1/2 | 45.1 | 42.3 |
| zh10 | 0/2 | 0/2 | – | – |
| zh11 | 2/2 | 0/2 | 117.3 | – |
| zh12 | 0/2 | 0/2 | – | – |

## How this was run

- Command: `bench_suite.py run --suite react-dev --suite react-holdout --repeats 2 --note first React/TS campaign (zustand); Devstill JS support 3aa4240, remote endpoint`
- Host: USNBRosenbergJ; tools: git git version 2.43.0, node v24.14.1, pytest pytest 8.3.5, python Python 3.12.3, ruff ruff 0.17.4
- react-dev: repeats 2, timeout 120 s, arms pi,runtime, dataset `benchmarks/retrieval/zustand_dev.v1.json`
- react-holdout: repeats 2, timeout 120 s, arms pi,runtime, dataset `benchmarks/retrieval/zustand_holdout.v1.json`
- Rebuild or check this report: `python -m benchmarks.bench_suite report .devstill/bench-history/20261110T040310Z-baab0f7 [--check]`

Single trials vary between runs of the same code (sampling, server batching, timing), so judge changes by pass counts over repeats and across suites, not by one case.
