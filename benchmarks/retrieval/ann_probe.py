"""Compare approximate candidates to exhaustive cosine on one frozen index.

This is a diagnostic for moderate corpora, not a production retrieval strategy.
The optional target is used only to score the retrieved candidates.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from devstill.config import load_config
from devstill.search import HybridSearch


def run(args):
    engine = HybridSearch(args.config, load_config(args.config))
    try:
        vector = np.asarray(engine.embedder.encode([args.query])[0], dtype=np.float32)
        approximate = engine.chroma.query_embedding(vector.tolist(), args.limit)
        data = engine.chroma.collection.get(include=["embeddings", "metadatas"])
        matrix = np.asarray(data["embeddings"], dtype=np.float32)
        norms = np.linalg.norm(matrix, axis=1) * np.linalg.norm(vector)
        scores = matrix @ vector / np.maximum(norms, np.finfo(np.float32).eps)
        ordered = sorted(
            range(len(scores)), key=lambda i: (-float(scores[i]), data["ids"][i])
        )
        ids = [m["chunk_id"] for m in data["metadatas"]]
        exact_top = {ids[i] for i in ordered[: args.limit]}
        found = {key for key, _ in approximate}
        report = {
            "query": args.query,
            "config": str(args.config),
            "limit": args.limit,
            "units": len(ids),
            "approximate_recall_vs_exact": len(exact_top & found) / len(exact_top),
            "approximate_ids": [key for key, _ in approximate],
            "exact_ids": [ids[i] for i in ordered[: args.limit]],
            "note": "Exhaustive vector comparison for diagnostics; source relevance is a separate metric.",
        }
        if args.target_chunk_id:
            target_index = ids.index(args.target_chunk_id)
            report["target"] = {
                "id": args.target_chunk_id,
                "exact_rank": ordered.index(target_index) + 1,
                "approximate_rank": next(
                    (
                        i
                        for i, (key, _) in enumerate(approximate, 1)
                        if key == args.target_chunk_id
                    ),
                    None,
                ),
                "cosine": float(scores[target_index]),
            }
        with args.output.open("x") as f:
            json.dump(report, f, indent=2)
            f.write("\n")
        print(
            json.dumps(
                {
                    k: v
                    for k, v in report.items()
                    if k not in {"exact_ids", "approximate_ids"}
                },
                indent=2,
            )
        )
    finally:
        engine.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--config", type=Path, required=True)
    p.add_argument("--query", required=True)
    p.add_argument("--target-chunk-id")
    p.add_argument("--limit", type=int, default=50)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    if args.limit < 1:
        p.error("--limit must be positive")
    run(args)
