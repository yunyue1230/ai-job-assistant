"""Compare the latest run of a suite against its promoted baseline.

    python -m evals.compare --suite job_match
    python -m evals.compare --suite job_match --promote
    python -m evals.compare --suite rag_retrieval --check --min-delta -0.02
"""

from __future__ import annotations

import argparse
import shutil

from evals.core.reporting import (
    compare_reports,
    load_report,
    print_comparison,
    report_path,
)

SUITES = ("job_match", "rag_retrieval", "interview_scoring")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", required=True, choices=SUITES)
    parser.add_argument("--promote", action="store_true", help="make latest the baseline")
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit non-zero when a metric regressed beyond --min-delta",
    )
    parser.add_argument(
        "--min-delta",
        type=float,
        default=-0.02,
        help="smallest acceptable change per metric (default -0.02)",
    )
    args = parser.parse_args()

    baseline_path = report_path(args.suite, "baseline")
    latest_path = report_path(args.suite, "latest")
    if args.promote:
        shutil.copyfile(latest_path, baseline_path)
        print(f"promoted {latest_path} -> {baseline_path}")
        return 0
    if not baseline_path.exists():
        print(f"no baseline for {args.suite}; run with --promote after a good run")
        return 0

    rows = compare_reports(load_report(baseline_path), load_report(latest_path))
    print_comparison(args.suite, rows)

    if not args.check:
        return 0
    regressions = [
        name
        for name, row in rows.items()
        if row["delta"] is not None and row["delta"] < args.min_delta
    ]
    if regressions:
        print(f"\nREGRESSION: {', '.join(regressions)}")
        return 1
    print("\nno regression beyond the configured threshold")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
