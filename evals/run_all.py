"""Run every suite in one command.

Offline by default (free, CI friendly). Use ``--live`` to refresh snapshots
against the configured providers, which costs tokens.
"""

from __future__ import annotations

import argparse
import sys

from evals.compare import SUITES, report_suite
from evals.run_interview_scoring import main as interview_main
from evals.run_job_match import main as job_match_main
from evals.run_rag_retrieval import main as rag_main


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="call the configured providers")
    parser.add_argument("--compare", action="store_true", help="diff against baselines")
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()

    argv = ["--live"] if args.live else []
    sys.argv = ["run_job_match", *argv]
    job_match_main()
    sys.argv = ["run_rag_retrieval", *argv]
    rag_main()
    sys.argv = ["run_interview_scoring", *argv, f"--repeats={args.repeats}"]
    interview_main()

    # Diffing happens here rather than inside each runner so every suite is
    # judged by the same policy and printed in the same shape.
    if args.compare:
        for suite in SUITES:
            report_suite(suite)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
