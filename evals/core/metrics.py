"""Pure metric functions. No model calls, no I/O, no cost."""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence

from backend.app.ai.evaluation import recall_at_k, reciprocal_rank

_TOKEN_RE = re.compile(r"[^a-z0-9]+")


def normalize(text: str) -> str:
    """Lowercase and collapse punctuation so labels compare reliably."""
    return _TOKEN_RE.sub(" ", text.lower()).strip()


def mean(values: Iterable[float]) -> float:
    values = list(values)
    return sum(values) / len(values) if values else 0.0


def median(values: Sequence[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[middle])
    return (ordered[middle - 1] + ordered[middle]) / 2


def max_spread(values: Sequence[float]) -> float:
    """Largest minus smallest value. Used as a stability / variance measure."""
    if len(values) < 2:
        return 0.0
    return float(max(values) - min(values))


def recall_at_k_average(
    rankings: Sequence[Sequence[str]],
    relevant_sets: Sequence[Iterable[str]],
    k: int,
) -> float:
    scores = [
        recall_at_k(ranking, set(relevant), k)
        for ranking, relevant in zip(rankings, relevant_sets)
    ]
    return mean(scores)


def mean_reciprocal_rank(
    rankings: Sequence[Sequence[str]],
    relevant_sets: Sequence[Iterable[str]],
) -> float:
    scores = [
        reciprocal_rank(ranking, set(relevant))
        for ranking, relevant in zip(rankings, relevant_sets)
    ]
    return mean(scores)


def _contains(normalized_target: str, haystack: Sequence[str]) -> bool:
    return any(
        normalized_target in candidate or candidate in normalized_target
        for candidate in haystack
    )


def list_recall(predicted: Sequence[str], expected: Sequence[str]) -> float:
    """Fraction of expected labels that appear in the predicted list."""
    if not expected:
        return 1.0
    haystack = [normalize(item) for item in predicted]
    hits = sum(1 for item in expected if _contains(normalize(item), haystack))
    return hits / len(expected)


def list_precision(predicted: Sequence[str], expected: Sequence[str]) -> float:
    """Fraction of predicted labels that are grounded in the expected list."""
    if not predicted:
        return 0.0
    targets = [normalize(item) for item in expected]
    hits = sum(1 for item in predicted if _contains(normalize(item), targets))
    return hits / len(predicted)


def list_f1(predicted: Sequence[str], expected: Sequence[str]) -> float:
    recall = list_recall(predicted, expected)
    precision = list_precision(predicted, expected)
    if recall + precision == 0:
        return 0.0
    return 2 * recall * precision / (recall + precision)


def score_mae(pairs: Sequence[tuple[int, int]]) -> float:
    """Mean absolute error between predicted and human-centred scores."""
    if not pairs:
        return 0.0
    return mean(abs(predicted - expected) for predicted, expected in pairs)


def within_tolerance_rate(pairs: Sequence[tuple[int, int]], tolerance: int) -> float:
    """Fraction of predictions within ``tolerance`` points of the human score."""
    if not pairs:
        return 0.0
    return mean(
        1.0 if abs(predicted - expected) <= tolerance else 0.0
        for predicted, expected in pairs
    )


def valid_rate(records: Sequence[dict], key: str = "error") -> float:
    """Fraction of samples that produced a schema-valid result."""
    if not records:
        return 0.0
    return mean(0.0 if record.get(key) else 1.0 for record in records)
