# Historical Devstill documents

These documents record earlier designs and measurements. They are preserved for
provenance, not instructions to resume superseded workflows. Claims of “current”
inside them refer to their original context; code examples may target legacy APIs.

Start current work with the [active test plan](../benchmarks/retrieval/TEST_PLAN.md),
[current runtime guide](../MINIMAL_RUNTIME.md), and
[latest native Quick results](../benchmarks/retrieval/NATIVE_QUICK_RESULTS.md).

## Design history

- [Original rebuild proposal](REBUILD_PLAN.md): the architecture decision and staged
  migration plan. Some goals remain unfinished; consult the current runtime guide
  rather than treating every planned capability as implemented.
- [Old implementation/backlog notes](ToDo.md).
- [Original retrieval index and handoff](benchmarks/retrieval/README.md).
- [Retrieval follow-up plan](benchmarks/retrieval/NEXT_PLAN.md).
- [One-shot composition strategy](benchmarks/retrieval/ONE_SHOT_STRATEGY.md).

## Retrieval and task experiments

- [Initial retrieval results](benchmarks/retrieval/RESULTS.md).
- [Retrieval implementation results](benchmarks/retrieval/IMPLEMENTATION_RESULTS.md).
- [Task implementation](benchmarks/retrieval/TASK_IMPLEMENTATION.md) and
  [comparison](benchmarks/retrieval/TASK_COMPARISON_RESULTS.md).
- [Task v2 implementation](benchmarks/retrieval/TASK_V2_IMPLEMENTATION.md) and
  [results](benchmarks/retrieval/TASK_V2_RESULTS.md).
- [Quick restoration](benchmarks/retrieval/QUICK_RESTORE_RESULTS.md).

## Feature coordination history

- [Design](benchmarks/retrieval/FEATURE_TASK_PLAN.md),
  [implementation](benchmarks/retrieval/FEATURE_IMPLEMENTATION.md), and
  [results](benchmarks/retrieval/FEATURE_COORDINATION_RESULTS.md).

## Pi and minimal-editor experiments

- [Earlier Pi comparison protocol](benchmarks/retrieval/PI_COMPARISON.md),
  [smoke results](benchmarks/retrieval/PI_SMOKE_RESULTS.md), and
  [stopped broad campaign](benchmarks/retrieval/PI_FULL_RESULTS.md).
- [Small diagnostic design](benchmarks/retrieval/QUICK_DIAGNOSTIC.md) and
  [results](benchmarks/retrieval/QUICK_DIAGNOSTIC_RESULTS.md).
- [First fast-suite results](benchmarks/retrieval/QUICK_FAST_V1_RESULTS.md).
- [Experimental check hardening](benchmarks/retrieval/QUICK_CHECK_HARDENING_RESULTS.md).

## Preservation policy

The original directory hierarchy is retained under this folder. Each archived
document identifies its former location; relative Markdown links were adjusted.
The former benchmark README was preserved before replacing the active index.
Historical wording and measured outcomes were not rewritten to match today's
architecture. No documents were discarded.

Only narrative Markdown documents moved. Runnable Python tools, case JSON files,
references, corpus pins, frozen runtime snapshots and raw `.devstill` run artifacts
remain at their existing locations. Do not delete raw evidence merely because its
summary is archived. Historical command strings still assume the original project
root; use the active plan's verified commands for new runs.
