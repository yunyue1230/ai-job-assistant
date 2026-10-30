"""RAG retrieval suite.

Scores the retrieval half of the knowledge-base pipeline: given a fixed corpus
and labelled relevant chunks, measure Recall@1/3/5 and MRR for the configured
embedding provider.

The live mode embeds everything with the real provider, then ranks with exact
cosine similarity. The production query runs through pgvector HNSW, which is an
approximate index, so these numbers are the achievable upper bound for the same
vectors; use them to compare embedding models, chunk sizes and overlaps.
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path
from typing import Any, Sequence

from evals.core.datasets import dataset_path, load_jsonl, resolve_relevant_chunks
from evals.core.metrics import mean_reciprocal_rank, recall_at_k_average
from evals.core.predictions import (
    read_predictions,
    resolve_snapshot,
    snapshot_model,
    write_predictions,
)
from evals.core.reporting import (
    RunMetadata,
    compare_reports,
    load_report,
    print_comparison,
    print_report,
    report_path,
    write_report,
)

SUITE = "rag_retrieval"
MAX_RANKING = 20


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return dot / (left_norm * right_norm)


def run_live(
    corpus: list[dict[str, Any]], queries: list[dict[str, Any]], top_k: int
) -> tuple[list[dict[str, Any]], str, str]:
    from backend.app.ai.embeddings import EmbeddingService
    from backend.app.core.config import get_settings

    service = EmbeddingService.from_settings()
    settings = get_settings()
    texts = [chunk["text"] for chunk in corpus]
    corpus_vectors = service.embed(texts)
    query_vectors = service.embed([query["query"] for query in queries])

    records: list[dict[str, Any]] = []
    for query, vector in zip(queries, query_vectors):
        scored = sorted(
            (
                (_cosine(vector, corpus_vectors[index]), corpus[index]["chunk_id"])
                for index in range(len(corpus))
            ),
            key=lambda item: item[0],
            reverse=True,
        )
        records.append(
            {
                "id": query["id"],
                "ranking": [chunk_id for _, chunk_id in scored[: max(top_k, MAX_RANKING)]],
            }
        )
    return records, settings.embedding_provider, settings.embedding_model


def score(
    queries: list[dict[str, Any]],
    predictions: list[dict[str, Any]],
    corpus: list[dict[str, Any]],
) -> dict[str, Any]:
    by_id = {record["id"]: record for record in predictions}
    rankings: list[list[str]] = []
    relevant_sets: list[list[str]] = []
    for query in queries:
        prediction = by_id.get(query["id"])
        if prediction is None:
            continue
        rankings.append(list(prediction.get("ranking", [])))
        relevant_sets.append(resolve_relevant_chunks(query, corpus))
    if not rankings:
        return {"samples": len(queries), "scored_samples": 0}
    return {
        "samples": len(queries),
        "scored_samples": len(rankings),
        "recall@1": recall_at_k_average(rankings, relevant_sets, 1),
        "recall@3": recall_at_k_average(rankings, relevant_sets, 3),
        "recall@5": recall_at_k_average(rankings, relevant_sets, 5),
        "mrr": mean_reciprocal_rank(rankings, relevant_sets),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", default=str(dataset_path("rag_corpus.jsonl")))
    parser.add_argument("--queries", default=str(dataset_path("rag_queries.jsonl")))
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--live", action="store_true", help="call the embedding provider")
    parser.add_argument("--snapshot", help="prediction snapshot to score (offline mode)")
    parser.add_argument("--notes", help="free-text note stored in the report")
    parser.add_argument("--compare", action="store_true", help="diff against the baseline")
    args = parser.parse_args()

    if not Path(args.corpus).exists() or not Path(args.queries).exists():
        print(
            "rag_retrieval skipped: no retrieval dataset. Add "
            "evals/datasets/rag_corpus.jsonl and rag_queries.jsonl "
            "(see evals/build_corpus.py) to enable this suite."
        )
        return 0
    corpus = load_jsonl(args.corpus)
    queries = load_jsonl(args.queries)
    if args.live:
        try:
            predictions, provider, model = run_live(corpus, queries, args.top_k)
        except RuntimeError as error:
            print(f"Embedding provider is not configured: {error}")
            return 1
        snapshot = write_predictions(SUITE, model, predictions)
        mode = "live"
    else:
        snapshot_path = Path(args.snapshot) if args.snapshot else resolve_snapshot(SUITE)
        if snapshot_path is None or not snapshot_path.exists():
            print(
                "No snapshot found. Run with --live once to create "
                "evals/snapshots/rag_retrieval.<model>.latest.jsonl"
            )
            return 0
        predictions = read_predictions(snapshot_path)
        provider, model, snapshot = "snapshot", snapshot_model(snapshot_path), snapshot_path
        mode = "offline"

    metrics = score(queries, predictions, corpus)
    metadata = RunMetadata.build(
        SUITE,
        mode,
        provider,
        model,
        args.queries,
        len(queries),
        snapshot=str(snapshot),
        notes=args.notes,
    )
    path = write_report(SUITE, metrics, metadata)
    print_report(path, metrics, metadata)

    baseline_path = report_path(SUITE, "baseline")
    if args.compare and baseline_path.exists():
        rows = compare_reports(load_report(baseline_path), load_report(path))
        print_comparison(SUITE, rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
