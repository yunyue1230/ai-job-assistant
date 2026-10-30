"""Pure metric functions. No model calls, no I/O, no cost.

The second half of the module is the regression policy: how far each metric may
move before CI should fail a run. Keeping it next to the metrics means the
thresholds are reviewed whenever a metric changes.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from backend.app.ai.evaluation import recall_at_k, reciprocal_rank

_TOKEN_RE = re.compile(r"[^a-z0-9]+")

DIRECTIONS = ("higher", "lower", "none")
KINDS = ("ratio", "score", "count")
ACTIONS = ("gate", "warn", "info")


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


# --------------------------------------------------------------------------
# Regression policy
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class MetricPolicy:
    """How far one metric may move before a run is called a regression.

    ``direction``
        ``higher`` / ``lower`` -- which way is better -- or ``none`` for metrics
        with no quality direction (sample counts, descriptors).
    ``kind``
        ``ratio`` (0-1), ``score`` (0-100) or ``count``. It only labels the
        scale so a reader knows which tolerance is meaningful.
    ``action``
        ``gate`` fails the build, ``warn`` prints and exits 0, ``info`` is
        recorded only.
    ``abs_tolerance`` / ``rel_tolerance``
        Allowed movement in absolute units, or as a fraction of the baseline.
        The wider of the two wins, so a metric never trips on its own noise.
    """

    direction: str
    kind: str
    action: str
    abs_tolerance: float | None = None
    rel_tolerance: float | None = None

    def __post_init__(self) -> None:
        if self.direction not in DIRECTIONS:
            raise ValueError(f"direction must be one of {DIRECTIONS}")
        if self.kind not in KINDS:
            raise ValueError(f"kind must be one of {KINDS}")
        if self.action not in ACTIONS:
            raise ValueError(f"action must be one of {ACTIONS}")


def _higher_ratio(
    abs_tolerance: float = 0.02, rel_tolerance: float | None = 0.05
) -> MetricPolicy:
    return MetricPolicy("higher", "ratio", "gate", abs_tolerance, rel_tolerance)


def _higher_score(
    abs_tolerance: float = 3.0, rel_tolerance: float | None = 0.05
) -> MetricPolicy:
    return MetricPolicy("higher", "score", "gate", abs_tolerance, rel_tolerance)


def _lower_score(
    abs_tolerance: float = 3.0, rel_tolerance: float | None = 0.05
) -> MetricPolicy:
    return MetricPolicy("lower", "score", "gate", abs_tolerance, rel_tolerance)


def _count_warn() -> MetricPolicy:
    """Sample counts: a change makes every other number incomparable."""
    return MetricPolicy("none", "count", "warn", 0.0, None)


def _info_score(direction: str) -> MetricPolicy:
    return MetricPolicy(direction, "score", "info", 3.0, None)


METRIC_POLICY: dict[str, MetricPolicy] = {
    # -- structural -------------------------------------------------------
    "samples": _count_warn(),
    "scored_samples": _count_warn(),
    # -- job_match --------------------------------------------------------
    "schema_valid_rate": _higher_ratio(),
    "score_mae": _lower_score(rel_tolerance=0.10),
    # One of 25 samples is worth 0.04, and two live runs of the same code
    # moved this by 0.08 with nothing else changing, so the band has to clear
    # two flipped samples or CI fails on sampling noise alone.
    "score_within_tolerance": _higher_ratio(abs_tolerance=0.10, rel_tolerance=0.10),
    "matched_skill_recall": _higher_ratio(),
    "missing_skill_recall": _higher_ratio(),
    # -- rag_retrieval ----------------------------------------------------
    # recall@1 carries the most run-to-run movement (one gold chunk per query
    # over 26 queries), so it gets the widest band.
    "recall@1": _higher_ratio(abs_tolerance=0.03, rel_tolerance=0.10),
    "recall@3": _higher_ratio(),
    "recall@5": _higher_ratio(),
    "mrr": _higher_ratio(),
    # -- interview_scoring ------------------------------------------------
    "discrimination_rate": _higher_ratio(),
    "tier_order_rate": _higher_ratio(),
    "mean_gap": _higher_score(),
    "mean_good_medium_gap": _higher_score(),
    "score_spread_median": _lower_score(abs_tolerance=2.0, rel_tolerance=None),
    # Tier medians describe the score distribution; they are reported, never
    # gated, because "good answers score 88" is not a target by itself.
    "median_good_score": _info_score("higher"),
    "median_medium_score": _info_score("none"),
    "median_off_topic_score": _info_score("none"),
    "median_bad_score": _info_score("lower"),
}


def policy_for(name: str) -> MetricPolicy | None:
    return METRIC_POLICY.get(name)


def regression_threshold(policy: MetricPolicy, baseline: float | None) -> float | None:
    """Wider of the absolute and relative bands, in the metric's own units.

    A zero or unknown baseline has nothing to be relative to, so the relative
    band is dropped rather than dividing by zero; the absolute band decides.
    """
    candidates: list[float] = []
    if policy.abs_tolerance is not None:
        candidates.append(policy.abs_tolerance)
    if policy.rel_tolerance is not None and baseline:
        candidates.append(policy.rel_tolerance * abs(baseline))
    return max(candidates) if candidates else None


@dataclass(frozen=True)
class MetricVerdict:
    name: str
    baseline: float | None
    candidate: float | None
    delta: float | None
    threshold: float | None
    direction: str
    kind: str
    action: str
    decision: str  # ok | improved | regression | changed | unscored
    gating: bool

    @property
    def policy_label(self) -> str:
        return f"{self.action}/{self.kind}/{self.direction}"


def evaluate_metric(
    name: str,
    baseline: float | None,
    candidate: float | None,
    min_delta: float = -0.02,
) -> MetricVerdict:
    """Compare one metric against its policy.

    Metrics without a policy fall back to the pre-policy rule: any drop past
    ``--min-delta`` fails, which reads as "lower is worse" and is why every
    metric above is declared explicitly.
    """
    numeric = isinstance(baseline, (int, float)) and isinstance(candidate, (int, float))
    delta = round(float(candidate) - float(baseline), 4) if numeric else None
    policy = policy_for(name)

    if policy is None:
        regression = delta is not None and delta < min_delta
        return MetricVerdict(
            name=name,
            baseline=baseline,
            candidate=candidate,
            delta=delta,
            threshold=min_delta,
            direction="higher",
            kind="unknown",
            action="gate",
            decision="regression" if regression else "ok",
            gating=regression,
        )

    threshold = regression_threshold(policy, baseline if numeric else None)
    if delta is None:
        decision = "unscored"
    elif policy.direction == "none":
        decision = "changed" if delta != 0 else "ok"
    else:
        band = threshold if threshold is not None else 0.0
        if policy.direction == "higher":
            decision = (
                "regression" if delta < -band else "improved" if delta > band else "ok"
            )
        else:
            decision = (
                "regression" if delta > band else "improved" if delta < -band else "ok"
            )

    return MetricVerdict(
        name=name,
        baseline=baseline,
        candidate=candidate,
        delta=delta,
        threshold=threshold,
        direction=policy.direction,
        kind=policy.kind,
        action=policy.action,
        decision=decision,
        gating=policy.action == "gate" and decision == "regression",
    )


def evaluate_report(
    baseline: dict, candidate: dict, min_delta: float = -0.02
) -> list[MetricVerdict]:
    names = sorted(
        set(baseline.get("metrics", {})) | set(candidate.get("metrics", {}))
    )
    return [
        evaluate_metric(
            name,
            baseline.get("metrics", {}).get(name),
            candidate.get("metrics", {}).get(name),
            min_delta,
        )
        for name in names
    ]


def gating_regressions(verdicts: Iterable[MetricVerdict]) -> list[str]:
    return [verdict.name for verdict in verdicts if verdict.gating]


def warnings(verdicts: Iterable[MetricVerdict]) -> list[str]:
    """Structural or soft problems: printed, never fatal."""
    return [
        verdict.name
        for verdict in verdicts
        if verdict.action == "warn" and verdict.decision in {"changed", "regression"}
    ]
