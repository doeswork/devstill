from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from devstill.agent import AgentClient
from devstill.context import (
    DEEP_PLAN_MIN_CHUNKS,
    context_candidate_supported,
    is_test_chunk,
)
from devstill.models import SearchResult
from devstill.planning import (
    PlanLabel,
    PlanOutput,
    generate_deep_plan,
    generate_plan,
)

DeepSearch = Callable[[list[str]], list[list[SearchResult]]]
InitialCallback = Callable[[PlanOutput], None]
ContextCallback = Callable[[PlanOutput, list[SearchResult]], None]


@dataclass(frozen=True, slots=True)
class DeepPlanRun:
    initial_plan: PlanOutput
    final_plan: PlanOutput
    initial_results: list[SearchResult]
    expanded_results: list[SearchResult]
    search_queries: list[str]


def deep_plan_queries(
    plan: PlanOutput,
    *,
    max_queries: int | None = None,
) -> list[str]:
    if max_queries is not None and max_queries < 1:
        return []
    queries: list[str] = []
    seen: set[str] = set()
    priority = {
        PlanLabel.SEARCH: 0,
        PlanLabel.MODIFY: 1,
        PlanLabel.ADD: 1,
        PlanLabel.DELETE: 1,
        PlanLabel.NEW_FILE: 2,
        PlanLabel.READ: 3,
    }
    ordered = list(enumerate(plan.items))
    if max_queries is not None:
        ordered.sort(key=lambda value: (priority[value[1].label], value[0]))
    for _, item in ordered:
        query = (
            f"{item.target}: {item.reason}"
            if item.label is PlanLabel.SEARCH
            else item.reason
        ).strip()
        normalized = query.casefold()
        if normalized and normalized not in seen:
            seen.add(normalized)
            queries.append(query)
            if max_queries is not None and len(queries) >= max_queries:
                break
    return queries


def merge_deep_context(
    initial_results: list[SearchResult],
    result_groups: list[list[SearchResult]],
    *,
    limit: int,
    include_tests: bool,
    min_limit: int = DEEP_PLAN_MIN_CHUNKS,
) -> list[SearchResult]:
    if limit < len(initial_results):
        raise ValueError("Deep Plan limit must be at least the initial result count")
    if min_limit < 1:
        raise ValueError("Deep Plan minimum must be at least 1")
    merged: list[SearchResult] = []
    seen: set[str] = set()

    def add(result: SearchResult) -> bool:
        if not include_tests and is_test_chunk(result.chunk):
            return False
        if result.chunk.id in seen:
            return False
        seen.add(result.chunk.id)
        merged.append(result)
        return True

    for result in initial_results:
        add(result)

    candidates: dict[str, SearchResult] = {}
    fusion_scores: defaultdict[str, float] = defaultdict(float)
    support_counts: defaultdict[str, int] = defaultdict(int)
    best_ranks: dict[str, int] = {}
    for group in result_groups:
        group_seen: set[str] = set()
        for rank, result in enumerate(group, start=1):
            chunk_id = result.chunk.id
            if chunk_id in seen or chunk_id in group_seen:
                continue
            if not include_tests and is_test_chunk(result.chunk):
                continue
            group_seen.add(chunk_id)
            candidates.setdefault(chunk_id, result)
            fusion_scores[chunk_id] += 1.0 / (60.0 + rank)
            support_counts[chunk_id] += 1
            best_ranks[chunk_id] = min(best_ranks.get(chunk_id, rank), rank)

    ranked: list[SearchResult] = []
    for chunk_id, result in candidates.items():
        scores = dict(result.scores)
        scores["deep_query_support"] = float(support_counts[chunk_id])
        ranked.append(
            SearchResult(
                chunk=result.chunk,
                final_score=fusion_scores[chunk_id],
                scores=scores,
            )
        )
    ranked.sort(
        key=lambda result: (
            -result.final_score,
            -result.scores.get("deep_query_support", 0.0),
            best_ranks[result.chunk.id],
            result.chunk.path,
            result.chunk.start_line,
        )
    )

    target_minimum = min(limit, max(min_limit, len(merged)))
    per_file: defaultdict[str, int] = defaultdict(int)
    for result in merged:
        per_file[result.chunk.path] += 1
    overflow: list[SearchResult] = []
    for candidate in ranked:
        if len(merged) >= limit:
            break
        if per_file[candidate.chunk.path] >= 2:
            overflow.append(candidate)
            continue
        if len(merged) >= target_minimum and not context_candidate_supported(
            merged, candidate
        ):
            continue
        if add(candidate):
            per_file[candidate.chunk.path] += 1

    for candidate in overflow:
        if len(merged) >= target_minimum:
            break
        add(candidate)
    return merged


def run_deep_plan(
    client: AgentClient,
    *,
    query: str,
    repository: str,
    repo_root: Path,
    initial_results: list[SearchResult],
    search: DeepSearch,
    deep_limit: int,
    include_tests: bool,
    deep_minimum: int = DEEP_PLAN_MIN_CHUNKS,
    max_followup_queries: int = 3,
    on_initial: InitialCallback | None = None,
    on_context: ContextCallback | None = None,
) -> DeepPlanRun:
    if deep_limit < len(initial_results):
        raise ValueError("Deep Plan limit must be at least the initial result count")
    initial_plan = generate_plan(
        client,
        query=query,
        repository=repository,
        repo_root=repo_root,
        results=initial_results,
        schema_name="devstill_deep_plan_initial",
        repair_schema_name="devstill_deep_plan_initial_repair",
    )
    if on_initial is not None:
        on_initial(initial_plan)
    queries = deep_plan_queries(initial_plan, max_queries=max_followup_queries)
    result_groups = search(queries) if queries else []
    expanded_results = merge_deep_context(
        initial_results,
        result_groups,
        limit=deep_limit,
        include_tests=include_tests,
        min_limit=deep_minimum,
    )
    if on_context is not None:
        on_context(initial_plan, expanded_results)
    final_plan = generate_deep_plan(
        client,
        query=query,
        repository=repository,
        repo_root=repo_root,
        initial_plan=initial_plan,
        results=expanded_results,
    )
    return DeepPlanRun(
        initial_plan=initial_plan,
        final_plan=final_plan,
        initial_results=initial_results,
        expanded_results=expanded_results,
        search_queries=queries,
    )
