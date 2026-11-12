# Benchmark campaign 20261112T002133Z-cc553e6

**Status:** completed · **Started:** 2026-11-12T00:21:33+00:00 · **Finished:** 2026-11-12T02:52:16+00:00 (2.51 h)

**Code:** bench-test-ui-ux @ cc553e6 · **Model:** vllm-qwen38-27b at https://llm.codemonkey.study/v1 · **Pi:** 1.5.0 · **Sampling:** default

**Note:** USB import verification: Quick Python and React/JS; local checker baseline-wait fix

## Results

Pass = the agent finished and the independent acceptance check passed (hidden behaviour check plus protected tests; SWE-bench: the official grader's `resolved`). Times are worker wall seconds; the time ratio is the median of Devstill/Pi over cases both passed (below 1 = Devstill faster).

| Suite | Status | Trials | Pi passed | Devstill passed | Both | Pi only | Devstill only | Median s, shared (Pi / Devstill) | Time ratio | Devstill faster |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | ---: | ---: |
| nq | completed | 20/20 | 9/10 | 9/10 | 8 | 1 | 1 | 14.6 / 10.4 | 0.73 | 7/8 |
| quick-fast | completed | 20/20 | 10/10 | 10/10 | 10 | 0 | 0 | 12.7 / 8.0 | 0.64 | 10/10 |
| loop-dev | completed | 24/24 | 11/12 | 10/12 | 10 | 1 | 0 | 13.1 / 16.9 | 0.85 | 7/10 |
| loop-holdout | completed | 48/48 | 20/24 | 16/24 | 15 | 5 | 1 | 25.2 / 13.4 | 0.6 | 11/15 |
| react-dev | completed | 24/24 | 3/12 | 7/12 | 1 | 2 | 6 | 46.8 / 22.4 | 0.48 | 1/1 |
| react-holdout | completed | 24/24 | 8/12 | 9/12 | 7 | 1 | 2 | 74.8 / 29.7 | 0.34 | 4/7 |
| **All** | | 160 | **61/80** | **61/80** | 51 | 10 | 10 | 16.2 / 12.0 | **0.66** | 40/51 |

## Change since the previous campaign (20261111T145638Z-d1bf301)

Previous run: model vllm-qwen38-27b, code d1bf301.

| Suite | Pi passed | Devstill passed | Time ratio |
| --- | --- | --- | --- |
| nq | 14/20 → 9/10 | 20/20 → 9/10 | 1.04 → 0.73 |
| quick-fast | 20/20 → 10/10 | 20/20 → 10/10 | 0.77 → 0.64 |
| loop-dev | 20/24 → 11/12 | 22/24 → 10/12 | 1.05 → 0.85 |
| loop-holdout | 18/24 → 20/24 | 19/24 → 16/24 | 0.99 → 0.6 |
| react-dev | 9/24 → 3/12 | 17/24 → 7/12 | 0.71 → 0.48 |
| react-holdout | 12/24 → 8/12 | 18/24 → 9/12 | 0.64 → 0.34 |

Cases whose outcome changed:

- nq / nq03 (Pi): 1/2 → 1/1
- nq / nq03 (Devstill): 2/2 → 0/1
- nq / nq09 (Pi): 1/2 → 1/1
- nq / nq10 (Pi): 0/2 → 1/1
- loop-dev / xc05 (Devstill): 2/2 → 0/1
- loop-dev / xw11 (Pi): 0/2 → 1/1
- loop-holdout / xc04 (Pi): 1/2 → 2/2
- loop-holdout / xc08 (Pi): 1/2 → 2/2
- loop-holdout / xc08 (Devstill): 2/2 → 1/2
- loop-holdout / xc10 (Pi): 1/2 → 2/2
- loop-holdout / xc10 (Devstill): 1/2 → 0/2
- loop-holdout / xc12 (Pi): 1/2 → 0/2
- loop-holdout / xw12 (Devstill): 1/2 → 0/2
- react-dev / zd01 (Pi): 1/2 → 1/1
- react-dev / zd01 (Devstill): 1/2 → 0/1
- react-dev / zd02 (Pi): 2/2 → 0/1
- react-dev / zd02 (Devstill): 1/2 → 1/1
- react-dev / zd04 (Pi): 1/2 → 0/1
- react-dev / zd05 (Pi): 1/2 → 0/1
- react-dev / zd06 (Devstill): 1/2 → 1/1
- react-dev / zd07 (Devstill): 2/2 → 0/1
- react-dev / zd09 (Devstill): 1/2 → 0/1
- react-dev / zd12 (Devstill): 1/2 → 0/1
- react-holdout / zh01 (Pi): 1/2 → 0/1
- react-holdout / zh02 (Pi): 0/2 → 1/1
- react-holdout / zh02 (Devstill): 1/2 → 1/1
- react-holdout / zh04 (Pi): 2/2 → 0/1
- react-holdout / zh09 (Pi): 1/2 → 1/1
- react-holdout / zh10 (Pi): 1/2 → 1/1
- react-holdout / zh10 (Devstill): 1/2 → 1/1
- react-holdout / zh11 (Pi): 1/2 → 1/1
- react-holdout / zh12 (Pi): 0/2 → 1/1
- react-holdout / zh12 (Devstill): 2/2 → 0/1

## What went wrong

### Devstill failures: improvement targets

| Suite | Case | Repeat | Arm | Category | Detail | Trial files |
| --- | --- | ---: | --- | --- | --- | --- |
| nq | nq03 | 0 | Devstill | wrong_fix:behavior_check | TypeError: unhashable type: 'list' | `suites/nq/nq03-0-runtime` |
| loop-dev | xc05 | 0 | Devstill | wrong_fix:behavior_check | AssertionError: {'build': 'Build the project.', 'clean': 'Remove build outputs.', 'deploy': 'Deploy the app secret deploy note', 'mirror': 'Mirror every tracked file and directory… | `suites/loop-dev/xc05-0-runtime` |
| loop-dev | xc11 | 0 | Devstill | wrong_fix:behavior_check | click.exceptions.BadParameter: Path '~/run.sh' is not executable. | `suites/loop-dev/xc11-0-runtime` |
| loop-holdout | xc02 | 1 | Devstill | no_change | turn_limit: No verified change within 20 model calls | `suites/loop-holdout/xc02-1-runtime` |
| loop-holdout | xc04 | 1 | Devstill | no_change | turn_limit: Not finished: your latest check also passes on the original code, so it does not test the requested change. Add assertions that fail without your change and pass with it, run them… | `suites/loop-holdout/xc04-1-runtime` |
| loop-holdout | xc08 | 1 | Devstill | wrong_fix:behavior_check | AssertionError: /tmp/tmp9impn2th/home/.foo-bar | `suites/loop-holdout/xc08-1-runtime` |
| loop-holdout | xc10 | 0 | Devstill | no_change | turn_limit: No verified change within 20 model calls | `suites/loop-holdout/xc10-0-runtime` |
| loop-holdout | xc10 | 1 | Devstill | no_change | turn_limit: No verified change within 20 model calls | `suites/loop-holdout/xc10-1-runtime` |
| loop-holdout | xc12 | 0 | Devstill | no_change | turn_limit: No verified change within 20 model calls | `suites/loop-holdout/xc12-0-runtime` |
| loop-holdout | xw12 | 0 | Devstill | no_change | turn_limit: Not finished: your latest check also passes on the original code, so it does not test the requested change. Add assertions that fail without your change and pass with it, run them… | `suites/loop-holdout/xw12-0-runtime` |
| loop-holdout | xw12 | 1 | Devstill | no_change | turn_limit: Your latest check fails after your last edit (re-run automatically): … "(\'private\', \'\\"Authorization\\"\')", "(\'max-age\', \'3600\')", "(\'no-transform\', N | `suites/loop-holdout/xw12-1-runtime` |
| react-dev | zd01 | 0 | Devstill | no_change | deadline: No verified change before the deadline | `suites/react-dev/zd01-0-runtime` |
| react-dev | zd07 | 0 | Devstill | no_change | turn_limit: No verified change within 30 model calls | `suites/react-dev/zd07-0-runtime` |
| react-dev | zd08 | 0 | Devstill | no_change | deadline: No verified change before the deadline | `suites/react-dev/zd08-0-runtime` |
| react-dev | zd09 | 0 | Devstill | no_change | turn_limit: No verified change within 30 model calls | `suites/react-dev/zd09-0-runtime` |
| react-dev | zd12 | 0 | Devstill | no_change | turn_limit: No verified change within 30 model calls | `suites/react-dev/zd12-0-runtime` |
| react-holdout | zh04 | 0 | Devstill | wrong_fix:regression_tests | JSON report written to /tmp/devstill-accept-tr2xhd48/vitest-report.json | `suites/react-holdout/zh04-0-runtime` |
| react-holdout | zh06 | 0 | Devstill | no_change | deadline: No verified change before the deadline | `suites/react-holdout/zh06-0-runtime` |
| react-holdout | zh12 | 0 | Devstill | wrong_fix:regression_tests | JSON report written to /tmp/devstill-accept-hf0qaxt8/vitest-report.json | `suites/react-holdout/zh12-0-runtime` |

### Pi failures (for comparison)

| Suite | Case | Repeat | Arm | Category | Detail | Trial files |
| --- | --- | ---: | --- | --- | --- | --- |
| nq | nq06 | 0 | Pi | no_change | agent ended its turn after 1 tool call(s) without editing (stop reason: stop) | `suites/nq/nq06-0-pi` |
| loop-dev | xc11 | 0 | Pi | wrong_fix:behavior_check | click.exceptions.BadParameter: Path '~/run.sh' is not executable. | `suites/loop-dev/xc11-0-pi` |
| loop-holdout | xc02 | 1 | Pi | no_change | agent ended its turn after 1 tool call(s) without editing (stop reason: stop) | `suites/loop-holdout/xc02-1-pi` |
| loop-holdout | xc12 | 0 | Pi | timeout_after_correct_edit | edit was correct but the agent did not stop in time | `suites/loop-holdout/xc12-0-pi` |
| loop-holdout | xc12 | 1 | Pi | wrong_fix:behavior_check | AssertionError: [<Option b>] | `suites/loop-holdout/xc12-1-pi` |
| loop-holdout | xw12 | 1 | Pi | timeout | no finished change within the time limit | `suites/loop-holdout/xw12-1-pi` |
| react-dev | zd02 | 0 | Pi | timeout_after_correct_edit | edit was correct but the agent did not stop in time | `suites/react-dev/zd02-0-pi` |
| react-dev | zd03 | 0 | Pi | timeout_after_correct_edit | edit was correct but the agent did not stop in time | `suites/react-dev/zd03-0-pi` |
| react-dev | zd04 | 0 | Pi | timeout_after_correct_edit | edit was correct but the agent did not stop in time | `suites/react-dev/zd04-0-pi` |
| react-dev | zd05 | 0 | Pi | timeout_after_correct_edit | edit was correct but the agent did not stop in time | `suites/react-dev/zd05-0-pi` |
| react-dev | zd06 | 0 | Pi | timeout | no finished change within the time limit | `suites/react-dev/zd06-0-pi` |
| react-dev | zd08 | 0 | Pi | timeout | no finished change within the time limit | `suites/react-dev/zd08-0-pi` |
| react-dev | zd09 | 0 | Pi | no_change | agent ended its turn after 6 tool call(s) without editing (stop reason: stop) | `suites/react-dev/zd09-0-pi` |
| react-dev | zd10 | 0 | Pi | no_change | Pi did not complete normally: exit=0, stop=length | `suites/react-dev/zd10-0-pi` |
| react-dev | zd12 | 0 | Pi | timeout_after_correct_edit | edit was correct but the agent did not stop in time | `suites/react-dev/zd12-0-pi` |
| react-holdout | zh01 | 0 | Pi | wrong_fix:behavior_check | No index signature with a parameter of type 'string' was found on type '{}'. | `suites/react-holdout/zh01-0-pi` |
| react-holdout | zh04 | 0 | Pi | timeout_after_correct_edit | edit was correct but the agent did not stop in time | `suites/react-holdout/zh04-0-pi` |
| react-holdout | zh06 | 0 | Pi | timeout | no finished change within the time limit | `suites/react-holdout/zh06-0-pi` |
| react-holdout | zh08 | 0 | Pi | timeout_after_correct_edit | edit was correct but the agent did not stop in time | `suites/react-holdout/zh08-0-pi` |

### Failure counts

| Arm | Category | Trials |
| --- | --- | ---: |
| Devstill | no_change | 13 |
| Devstill | wrong_fix:behavior_check | 4 |
| Devstill | wrong_fix:regression_tests | 2 |
| Pi | no_change | 4 |
| Pi | timeout | 4 |
| Pi | timeout_after_correct_edit | 8 |
| Pi | wrong_fix:behavior_check | 3 |

## Code quality (trials that changed code)

Size ratio compares changed lines with the reference fix. Lint counts new ruff findings (default rules, project settings ignored) in changed Python files.

| Suite | Arm | Trials | Median changed lines | Median size vs reference | Touched files beyond the reference | Touched tests | New lint findings |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| nq | Pi | 9 | 6 | 2.0 | 0 | 0 | 0 |
| nq | Devstill | 10 | 3.0 | 1.0 | 0 | 0 | 0 |
| quick-fast | Pi | 10 | 2.0 | 1.0 | 0 | 0 | 0 |
| quick-fast | Devstill | 10 | 2.0 | 1.0 | 0 | 0 | 0 |
| loop-dev | Pi | 12 | 11.0 | 1.0 | 1 | 2 | 2 |
| loop-dev | Devstill | 12 | 6.0 | 1.0 | 0 | 2 | 2 |
| loop-holdout | Pi | 23 | 15 | 1.53 | 2 | 4 | 11 |
| loop-holdout | Devstill | 17 | 7 | 1.0 | 0 | 2 | 2 |
| react-dev | Pi | 10 | 78.0 | 6.75 | 1 | 8 | 0 |
| react-dev | Devstill | 7 | 16 | 1.26 | 0 | 0 | 0 |
| react-holdout | Pi | 12 | 29.0 | 2.27 | 1 | 4 | 0 |
| react-holdout | Devstill | 11 | 19 | 1.08 | 0 | 0 | 0 |

## Per case

### nq

| Case | Pi passed | Devstill passed | Pi median s | Devstill median s |
| --- | ---: | ---: | ---: | ---: |
| nq01 | 1/1 | 1/1 | 14.1 | 13.0 |
| nq02 | 1/1 | 1/1 | 16.3 | 9.6 |
| nq03 | 1/1 | 0/1 | 30.7 | – |
| nq04 | 1/1 | 1/1 | 14.5 | 9.1 |
| nq05 | 1/1 | 1/1 | 10.9 | 8.6 |
| nq06 | 0/1 | 1/1 | – | 41.3 |
| nq07 | 1/1 | 1/1 | 17.0 | 11.3 |
| nq08 | 1/1 | 1/1 | 14.7 | 12.6 |
| nq09 | 1/1 | 1/1 | 13.0 | 14.3 |
| nq10 | 1/1 | 1/1 | 58.4 | 7.7 |

### quick-fast

| Case | Pi passed | Devstill passed | Pi median s | Devstill median s |
| --- | ---: | ---: | ---: | ---: |
| w_age | 1/1 | 1/1 | 15.3 | 8.3 |
| w_close | 1/1 | 1/1 | 12.9 | 9.7 |
| w_description | 1/1 | 1/1 | 11.6 | 7.6 |
| w_header_lines | 1/1 | 1/1 | 16.2 | 8.7 |
| w_hop_whitespace | 1/1 | 1/1 | 10.6 | 6.6 |
| w_parse_age_types | 1/1 | 1/1 | 13.5 | 7.8 |
| w_repr | 1/1 | 1/1 | 9.6 | 7.6 |
| w_salt | 1/1 | 1/1 | 12.5 | 8.1 |
| w_secure_pathlike | 1/1 | 1/1 | 13.1 | 8.3 |
| w_storage_truth | 1/1 | 1/1 | 9.1 | 7.7 |

### loop-dev

| Case | Pi passed | Devstill passed | Pi median s | Devstill median s |
| --- | ---: | ---: | ---: | ---: |
| xc01 | 1/1 | 1/1 | 31.2 | 27.6 |
| xc03 | 1/1 | 1/1 | 98.9 | 81.1 |
| xc05 | 1/1 | 0/1 | 91.1 | – |
| xc07 | 1/1 | 1/1 | 5.6 | 4.0 |
| xc09 | 1/1 | 1/1 | 12.8 | 12.0 |
| xc11 | 0/1 | 0/1 | – | – |
| xw01 | 1/1 | 1/1 | 5.8 | 4.5 |
| xw03 | 1/1 | 1/1 | 3.6 | 12.2 |
| xw05 | 1/1 | 1/1 | 13.4 | 22.6 |
| xw07 | 1/1 | 1/1 | 74.1 | 21.6 |
| xw09 | 1/1 | 1/1 | 7.4 | 10.1 |
| xw11 | 1/1 | 1/1 | 119.1 | 46.7 |

### loop-holdout

| Case | Pi passed | Devstill passed | Pi median s | Devstill median s |
| --- | ---: | ---: | ---: | ---: |
| xc02 | 1/2 | 1/2 | 44.0 | 25.1 |
| xc04 | 2/2 | 1/2 | 59.1 | 74.7 |
| xc06 | 2/2 | 2/2 | 24.7 | 14.3 |
| xc08 | 2/2 | 1/2 | 34.8 | 25.4 |
| xc10 | 2/2 | 0/2 | 91.2 | – |
| xc12 | 0/2 | 1/2 | – | 65.2 |
| xw02 | 2/2 | 2/2 | 7.0 | 12.5 |
| xw04 | 2/2 | 2/2 | 70.1 | 19.7 |
| xw06 | 2/2 | 2/2 | 28.1 | 11.1 |
| xw08 | 2/2 | 2/2 | 14.3 | 11.8 |
| xw10 | 2/2 | 2/2 | 18.5 | 17.0 |
| xw12 | 1/2 | 0/2 | 95.5 | – |

### react-dev

| Case | Pi passed | Devstill passed | Pi median s | Devstill median s |
| --- | ---: | ---: | ---: | ---: |
| zd01 | 1/1 | 0/1 | 52.3 | – |
| zd02 | 0/1 | 1/1 | – | 13.4 |
| zd03 | 0/1 | 1/1 | – | 89.0 |
| zd04 | 0/1 | 1/1 | – | 26.3 |
| zd05 | 0/1 | 1/1 | – | 43.7 |
| zd06 | 0/1 | 1/1 | – | 111.0 |
| zd07 | 1/1 | 0/1 | 24.9 | – |
| zd08 | 0/1 | 0/1 | – | – |
| zd09 | 0/1 | 0/1 | – | – |
| zd10 | 0/1 | 1/1 | – | 93.5 |
| zd11 | 1/1 | 1/1 | 46.8 | 22.4 |
| zd12 | 0/1 | 0/1 | – | – |

### react-holdout

| Case | Pi passed | Devstill passed | Pi median s | Devstill median s |
| --- | ---: | ---: | ---: | ---: |
| zh01 | 0/1 | 1/1 | – | 26.7 |
| zh02 | 1/1 | 1/1 | 41.4 | 102.2 |
| zh03 | 1/1 | 1/1 | 65.4 | 21.1 |
| zh04 | 0/1 | 0/1 | – | – |
| zh05 | 1/1 | 1/1 | 90.5 | 30.9 |
| zh06 | 0/1 | 0/1 | – | – |
| zh07 | 1/1 | 1/1 | 14.6 | 22.6 |
| zh08 | 0/1 | 1/1 | – | 90.8 |
| zh09 | 1/1 | 1/1 | 94.8 | 29.7 |
| zh10 | 1/1 | 1/1 | 74.8 | 87.0 |
| zh11 | 1/1 | 1/1 | 88.5 | 27.8 |
| zh12 | 1/1 | 0/1 | 104.7 | – |

## How this was run

- Command: `bench_suite.py run --tag core --tag react --note USB import verification: Quick Python and React/JS; local checker baseline-wait fix`
- Host: Jeffy4080; tools: git git version 2.43.0, node v24.14.0, pytest pytest 8.3.5, python Python 3.12.3, ruff ruff 0.16.1
- nq: repeats 1, timeout 120 s, arms pi,runtime, dataset `benchmarks/retrieval/native_quick_fresh.v1.json`
- quick-fast: repeats 1, timeout 120 s, arms pi,runtime, dataset `benchmarks/retrieval/quick_fast.v1.json`
- loop-dev: repeats 1, timeout 120 s, arms pi,runtime, dataset `benchmarks/retrieval/quick_loop_dev.v1.json`
- loop-holdout: repeats 2, timeout 120 s, arms pi,runtime, dataset `benchmarks/retrieval/quick_loop_holdout.v1.json`
- react-dev: repeats 1, timeout 120 s, arms pi,runtime, dataset `benchmarks/retrieval/zustand_dev.v1.json`
- react-holdout: repeats 1, timeout 120 s, arms pi,runtime, dataset `benchmarks/retrieval/zustand_holdout.v1.json`
- Rebuild or check this report: `python -m benchmarks.bench_suite report .devstill/bench-history/20261112T002133Z-cc553e6 [--check]`

Single trials vary between runs of the same code (sampling, server batching, timing), so judge changes by pass counts over repeats and across suites, not by one case.
