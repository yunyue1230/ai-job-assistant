"""Mock interview scoring suite.

For each labelled question the suite scores the answer tiers that the sample
provides -- a strong answer (repeated, for stability), a medium answer that is
correct but thin, an off-topic answer, and a plainly wrong answer -- then
measures:

- ``schema_valid_rate``   -- the evaluation parsed into the Pydantic model.
- ``discrimination_rate`` -- a strong answer really scores above a wrong one.
- ``mean_gap``            -- how large that separation is (too small = noise).
- ``score_spread_median`` -- repeat-call instability of the same answer.
- ``tier_order_rate``     -- strong > medium > (off-topic, wrong), i.e. the
  scorer can resolve four levels instead of only "good vs terrible".
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from evals.core.datasets import dataset_path, load_jsonl
from evals.core.metrics import max_spread, mean, median, valid_rate
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

SUITE = "interview_scoring"
DEFAULT_REPEATS = 3


def run_live(
    dataset: list[dict[str, Any]], repeats: int
) -> tuple[list[dict[str, Any]], str, str, dict[str, Any]]:
    from backend.app.ai.interview_engine import OpenAIInterviewEngine
    from backend.app.ai.model_providers import list_provider_info
    from backend.app.core.config import get_settings

    engine = OpenAIInterviewEngine.from_settings()
    settings = get_settings()
    provider = settings.active_chat_provider
    model = settings.model_name
    for info in list_provider_info():
        if info.active:
            model = info.model
            break

    def evaluate(question: str, answer: str, role: str, job_description: str) -> int:
        evaluation = engine.evaluate_answer(
            role=role,
            job_description=job_description,
            question=question,
            answer=answer,
            history=[],
            knowledge_context="",
        )
        return evaluation.score

    records: list[dict[str, Any]] = []
    for sample in dataset:
        role = sample.get("role", "Software Engineer")
        job_description = sample.get("job_description", "")
        try:
            good_scores = [
                evaluate(sample["question"], sample["good_answer"], role, job_description)
                for _ in range(repeats)
            ]
            record: dict[str, Any] = {
                "id": sample["id"],
                "good_scores": good_scores,
                "error": None,
            }
            for tier, key in (
                ("medium", "medium_answer"),
                ("off_topic", "off_topic_answer"),
                ("bad", "bad_answer"),
            ):
                answer = sample.get(key)
                if answer:
                    record[f"{tier}_score"] = evaluate(
                        sample["question"], answer, role, job_description
                    )
            records.append(record)
        except Exception as error:  # noqa: BLE001 - keep scoring the other samples
            records.append(
                {"id": sample["id"], "error": f"{type(error).__name__}: {error}"}
            )
    return records, provider, model, engine.stats.as_dict()


def score(dataset: list[dict[str, Any]], predictions: list[dict[str, Any]]) -> dict[str, Any]:
    by_id = {record["id"]: record for record in predictions}
    discriminations: list[float] = []
    gaps: list[float] = []
    spreads: list[float] = []
    good_medians: list[float] = []
    medium_medians: list[float] = []
    off_topic_scores: list[float] = []
    bad_scores: list[float] = []
    tier_orders: list[float] = []
    good_medium_gaps: list[float] = []
    for sample in dataset:
        prediction = by_id.get(sample["id"])
        if prediction is None or prediction.get("error"):
            continue
        good_scores = [float(value) for value in prediction.get("good_scores", [])]
        if not good_scores:
            continue
        good_median = median(good_scores)
        good_medians.append(good_median)
        spreads.append(max_spread(good_scores))

        medium = prediction.get("medium_score")
        off_topic = prediction.get("off_topic_score")
        bad = prediction.get("bad_score")
        if medium is not None:
            medium_medians.append(float(medium))
            good_medium_gaps.append(good_median - float(medium))
        if off_topic is not None:
            off_topic_scores.append(float(off_topic))
        if bad is not None:
            bad_scores.append(float(bad))
            discriminations.append(1.0 if good_median > float(bad) else 0.0)
            gaps.append(good_median - float(bad))
        if medium is not None and off_topic is not None and bad is not None:
            tier_orders.append(
                1.0
                if good_median > float(medium) > max(float(off_topic), float(bad))
                else 0.0
            )

    metrics: dict[str, Any] = {
        "samples": len(dataset),
        "schema_valid_rate": valid_rate(predictions),
        "discrimination_rate": mean(discriminations),
        "mean_gap": mean(gaps),
        "median_good_score": median(good_medians),
        "score_spread_median": median(spreads),
    }
    if medium_medians:
        metrics["median_medium_score"] = median(medium_medians)
        metrics["mean_good_medium_gap"] = mean(good_medium_gaps)
    if off_topic_scores:
        metrics["median_off_topic_score"] = median(off_topic_scores)
    if bad_scores:
        metrics["median_bad_score"] = median(bad_scores)
    if tier_orders:
        metrics["tier_order_rate"] = mean(tier_orders)
    return metrics


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", default=str(dataset_path("interview_scoring.jsonl")))
    parser.add_argument("--repeats", type=int, default=DEFAULT_REPEATS)
    parser.add_argument("--live", action="store_true", help="call the configured provider")
    parser.add_argument("--snapshot", help="prediction snapshot to score (offline mode)")
    parser.add_argument("--notes", help="free-text note stored in the report")
    parser.add_argument("--compare", action="store_true", help="diff against the baseline")
    args = parser.parse_args()

    dataset = load_jsonl(args.dataset)
    provider_stats: dict[str, Any] | None = None
    if args.live:
        try:
            predictions, provider, model, provider_stats = run_live(
                dataset, args.repeats
            )
        except RuntimeError as error:
            print(f"Chat provider is not configured: {error}")
            return 1
        snapshot = write_predictions(SUITE, model, predictions)
        mode = "live"
    else:
        snapshot_path = Path(args.snapshot) if args.snapshot else resolve_snapshot(SUITE)
        if snapshot_path is None or not snapshot_path.exists():
            print(
                "No snapshot found. Run with --live once to create "
                "evals/snapshots/interview_scoring.<model>.latest.jsonl"
            )
            return 0
        predictions = read_predictions(snapshot_path)
        provider, model, snapshot = "snapshot", snapshot_model(snapshot_path), snapshot_path
        mode = "offline"

    metrics = score(dataset, predictions)
    metadata = RunMetadata.build(
        SUITE,
        mode,
        provider,
        model,
        args.dataset,
        len(dataset),
        snapshot=str(snapshot),
        notes=args.notes,
        provider_stats=provider_stats,
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
