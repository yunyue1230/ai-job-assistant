"""Resume/JD matching suite.

Metrics: schema validity, score MAE against human-labelled scores, tolerance
hit rate, and skill recall for both the matched and missing skill lists.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from evals.core.datasets import dataset_path, load_jsonl
from evals.core.metrics import (
    list_recall,
    mean,
    score_mae,
    valid_rate,
)
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

SUITE = "job_match"
DEFAULT_TOLERANCE = 12


def run_live(dataset: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], str, str]:
    from backend.app.ai.model_providers import list_provider_info
    from backend.app.core.config import get_settings
    from services.job_match_service import analyze_job_match

    settings = get_settings()
    provider = settings.active_chat_provider
    model = settings.model_name
    for info in list_provider_info():
        if info.active:
            model = info.model
            break

    records: list[dict[str, Any]] = []
    for sample in dataset:
        try:
            result = analyze_job_match(sample["job_description"], sample["resume"])
            records.append(
                {
                    "id": sample["id"],
                    "match_score": result["match_score"],
                    "matched_skills": result["matched_skills"],
                    "missing_skills": result["missing_skills"],
                    "error": None,
                }
            )
        except Exception as error:  # noqa: BLE001 - a failed sample must not abort the run
            records.append(
                {"id": sample["id"], "error": f"{type(error).__name__}: {error}"}
            )
    return records, provider, model


def score(
    dataset: list[dict[str, Any]],
    predictions: list[dict[str, Any]],
    tolerance: int,
) -> dict[str, Any]:
    by_id = {record["id"]: record for record in predictions}
    pairs: list[tuple[int, int]] = []
    tolerance_pairs: list[tuple[int, int, int]] = []
    matched_recalls: list[float] = []
    missing_recalls: list[float] = []
    for sample in dataset:
        prediction = by_id.get(sample["id"])
        if prediction is None or prediction.get("error"):
            continue
        expected = sample.get("expected", {})
        if expected.get("score_center") is not None:
            predicted = int(prediction["match_score"])
            centre = int(expected["score_center"])
            pairs.append((predicted, centre))
            # A vague JD cannot be scored to the same precision as a detailed
            # one, so a sample may widen its own tolerance.
            tolerance_pairs.append(
                (predicted, centre, int(expected.get("score_tolerance", tolerance)))
            )
        matched_recalls.append(
            list_recall(
                prediction.get("matched_skills", []), expected.get("must_match_skills", [])
            )
        )
        missing_recalls.append(
            list_recall(
                prediction.get("missing_skills", []),
                expected.get("must_missing_skills", []),
            )
        )
    return {
        "samples": len(dataset),
        "schema_valid_rate": valid_rate(predictions),
        "score_mae": score_mae(pairs),
        "score_within_tolerance": mean(
            1.0 if abs(predicted - centre) <= tol else 0.0
            for predicted, centre, tol in tolerance_pairs
        ),
        "matched_skill_recall": mean(matched_recalls),
        "missing_skill_recall": mean(missing_recalls),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", default=str(dataset_path("job_match.jsonl")))
    parser.add_argument("--live", action="store_true", help="call the configured provider")
    parser.add_argument("--snapshot", help="prediction snapshot to score (offline mode)")
    parser.add_argument("--tolerance", type=int, default=DEFAULT_TOLERANCE)
    parser.add_argument("--notes", help="free-text note stored in the report")
    parser.add_argument("--compare", action="store_true", help="diff against the baseline")
    args = parser.parse_args()

    dataset = load_jsonl(args.dataset)
    if args.live:
        try:
            predictions, provider, model = run_live(dataset)
        except RuntimeError as error:
            print(f"Chat provider is not configured: {error}")
            return 1
        snapshot = write_predictions(SUITE, model, predictions)
        mode = "live"
    else:
        snapshot_path = (
            Path(args.snapshot) if args.snapshot else resolve_snapshot(SUITE)
        )
        if snapshot_path is None or not snapshot_path.exists():
            print(
                "No snapshot found. Run with --live once to create "
                "evals/snapshots/job_match.<model>.latest.jsonl"
            )
            return 0
        predictions = read_predictions(snapshot_path)
        provider, model, snapshot = "snapshot", snapshot_model(snapshot_path), snapshot_path
        mode = "offline"

    metrics = score(dataset, predictions, args.tolerance)
    metadata = RunMetadata.build(
        SUITE,
        mode,
        provider,
        model,
        args.dataset,
        len(dataset),
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
