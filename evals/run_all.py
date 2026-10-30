"""Run every suite in one command.

Offline by default (free, CI friendly). Use ``--live`` to refresh snapshots
against the configured providers, which costs tokens.
"""

from __future__ import annotations

import argparse

from evals.run_interview_scoring import main as interview_main
from evals.run_job_match import main as job_match_main
from evals.run_rag_retrieval import main as rag_main


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="call the configured providers")
    parser.add_argument("--compare", action="store_true", help="diff against baselines")
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()

    import sys

    argv = ["--compare"] if args.compare else []
    if args.live:
        argv.append("--live")

    sys.argv = ["run_job_match", *argv]
    job_match_main()
    sys.argv = ["run_rag_retrieval", *argv]
    rag_main()
    sys.argv = ["run_interview_scoring", *argv, f"--repeats={args.repeats}"]
    interview_main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
