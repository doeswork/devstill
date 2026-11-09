# Native Quick evaluation

The goal is native Devstill Quick with at least Pi's coding correctness and lower
end-to-end task time. Keep comparisons small, frozen and independently checked.

- [Active repair diagnostic](QUICK_REPAIR_PLAN.md): bounded protocol/context fixes,
  a 12-attempt gate, and at most 12 follow-up attempts before any broader run.
- [Original fresh test plan](TEST_PLAN.md): ten fresh requests, 20 attempts, two short batches;
  fixture preparation, interface tests, correctness/scope/speed gates, and handoff.
- [Previous diagnostic and fixes](QUICK_CONTEXT_REPAIR_RESULTS.md): constructors, retained checks,
  explicit deliveries; Quick and Pi 4/6 completed on six development cases.
- [Fresh first-pass results](NATIVE_QUICK_FRESH_RESULTS.md): Quick 5/10 versus Pi 7/10;
  correctness gate failed. All original attempts remain retained.
- [Runtime usage and limitations](../../MINIMAL_RUNTIME.md): current TUI/headless behavior.
- [Historical archive](../../archive/README.md): superseded plans, experiments and reports.

Latest: [Quick agent loop vs Pi](QUICK_LOOP_RESULTS.md) (the loop engine replaced the
structured editor). Previous: [repair results and schema compatibility finding](QUICK_REPAIR_RESULTS.md).
The paired gate failed; the final schema fix passed one live format probe and
offline patch replay. It has not passed a paired coding comparison and was not
installed. The 24-attempt/probe budget is exhausted; no broad run was started.

Previous: [focused-context results](QUICK_FOCUSED_RESULTS.md): Quick and Pi 4/6
on six cases, one draw each. Do not pool that run with the new diagnostic.

## Tools and naming

Use [pi_comparison.py](pi_comparison.py) with explicit
`--variants pi,runtime --pi-disable-reasoning`. `runtime` is today's native Quick;
`quick` is the historical campaign path and `minimal` is the older prototype.
Do not accept the runner's legacy defaults for a native Quick comparison.

[coding_campaign.py](coding_campaign.py) verifies case datasets before model calls.
[runtime_candidate.py](runtime_candidate.py) exercises actual runtime prepare,
saved-proposal reload and apply. [recording_proxy.py](recording_proxy.py) preserves
requests/responses and measures actual model traffic without authorization headers.

`quick_fast.v1.json` and earlier datasets remain in place as known regression
fixtures. `native_quick_fresh.v1.json` has now been authored, verified and run; these
requests are exposed development cases. Executable tools, reference fixtures,
frozen source and raw `.devstill` results were not archived or removed.

Latest fresh evaluation: [results and improvement notes](NATIVE_QUICK_FRESH_RESULTS.md). The correctness gate failed; do not launch long repeats.
