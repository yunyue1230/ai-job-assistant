"""Compare the latest run of a suite against its promoted baseline.

    python -m evals.compare --suite job_match
    python -m evals.compare --suite job_match --promote
    python -m evals.compare --suite rag_retrieval --check

``--promote`` refuses to promote anything that is not a complete live run,
because the baseline is what CI diffs against. Pass ``--force`` when the
promotion really is deliberate.

Regression detection is driven by ``evals.core.metrics.METRIC_POLICY``: each
metric declares whether higher or lower is better, and how far it may move
before the run is failed (``gate``), merely reported (``warn``), or only
recorded (``info``). ``--min-delta`` stays as the fallback for metrics that
have no policy yet.
"""

from __future__ import annotations

import argparse
import shutil
from collections.abc import Sequence

from evals.core.metrics import (
    MetricVerdict,
    evaluate_report,
    gating_regressions,
    warnings,
)
from evals.core.reporting import load_report, promotion_blockers, report_path

SUITES = ("job_match", "rag_retrieval", "interview_scoring")


def _render(value: float | int | None) -> str:
    if value is None:
        return "-"
    return f"{value:.4f}" if isinstance(value, float) else str(value)


def print_verdicts(suite: str, verdicts: Sequence[MetricVerdict]) -> None:
    print(f"\n=== {suite}: candidate vs baseline ===")
    header = (
        f"  {'metric':<28} {'baseline':>10} {'current':>10} {'delta':>10} "
        f"{'threshold':>10}  {'decision':<10} policy"
    )
    print(header)
    for verdict in verdicts:
        print(
            f"  {verdict.name:<28} {_render(verdict.baseline):>10} "
            f"{_render(verdict.candidate):>10} {_render(verdict.delta):>10} "
            f"{_render(verdict.threshold):>10}  {verdict.decision:<10} "
            f"{verdict.policy_label}"
        )


def report_suite(suite: str, min_delta: float = -0.02) -> list[MetricVerdict] | None:
    """Print one suite's verdicts, or say why there is nothing to print.

    Every caller -- this CLI, ``run_all --compare`` and each suite runner --
    goes through here, so a run is always judged with the same policy and
    printed in the same shape.
    """
    baseline_path = report_path(suite, "baseline")
    if not baseline_path.exists():
        print(
            f"\n=== {suite}: no baseline yet ===\n"
            f"  run 'python -m evals.compare --suite {suite} --promote' after a good run"
        )
        return None
    verdicts = evaluate_report(
        load_report(baseline_path), load_report(report_path(suite, "latest")), min_delta
    )
    print_verdicts(suite, verdicts)
    soft = warnings(verdicts)
    if soft:
        print(f"WARNING: {', '.join(soft)} (structural change, not a failure)")
    return verdicts


def promote_suite(suite: str, *, force: bool = False) -> int:
    """Copy the latest report over the baseline, unless it should not be one."""
    latest_path = report_path(suite, "latest")
    baseline_path = report_path(suite, "baseline")
    if not latest_path.exists():
        print(f"\n=== {suite}: nothing to promote ===")
        print(f"  {latest_path} does not exist; run the suite with --live first")
        return 2

    blockers = promotion_blockers(load_report(latest_path))
    if blockers and not force:
        print(f"\n=== {suite}: promotion refused ===")
        for blocker in blockers:
            print(f"  {blocker}")
        print(
            f"  baseline is what CI diffs against; "
            f"add --force to promote {latest_path} anyway"
        )
        return 2

    shutil.copyfile(latest_path, baseline_path)
    print(f"promoted {latest_path} -> {baseline_path}")
    for blocker in blockers:
        print(f"  WARNING: promoted anyway ({blocker})")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", required=True, choices=SUITES)
    parser.add_argument(
        "--promote",
        action="store_true",
        help="make latest the baseline (refused unless it is a complete live run)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="promote even when --promote would refuse the latest report",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit non-zero when a gated metric regressed beyond its policy",
    )
    parser.add_argument(
        "--min-delta",
        type=float,
        default=-0.02,
        help="fallback tolerance for metrics with no policy (default -0.02)",
    )
    args = parser.parse_args()

    if args.promote:
        return promote_suite(args.suite, force=args.force)
    verdicts = report_suite(args.suite, args.min_delta)
    if verdicts is None:
        return 0

    if not args.check:
        return 0
    regressions = gating_regressions(verdicts)
    if regressions:
        print(f"\nREGRESSION: {', '.join(regressions)}")
        return 1
    print("\nno regression beyond the configured policy")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
