"""Show provider behaviour across every recorded eval run.

Metrics say whether the model was right; these numbers say whether the provider
was reliable. Retries, parsing failures and latency live in each report's
``metadata.provider_stats``, and a rising retry rate is a provider problem that
never shows up in a metric.

    python -m evals.provider_stats            # every report on disk
    python -m evals.provider_stats --suite interview_scoring
    python -m evals.provider_stats --live-only

Nothing here gates CI: provider flakiness is observed, not judged.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Iterable
from pathlib import Path

from evals.core.reporting import REPORTS_DIR

SUITES = ("job_match", "rag_retrieval", "interview_scoring")

# Counters kept per run, and the short label used for each column.
FIELDS = {
    "calls": "calls",
    "provider_failures": "provider",
    "json_parsing_failures": "json",
    "schema_validation_failures": "schema",
    "retries": "retries",
    "retry_successes": "rescued",
    "final_failures": "failed",
    "retry_latency_seconds": "lat_s",
}


def _load(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def collect(reports_dir: Path = REPORTS_DIR) -> list[dict]:
    """One row per run that carries provider stats, oldest run first.

    A single run is written three times -- ``latest``, a timestamped copy and,
    once promoted, the baseline -- so runs are keyed by their stats instead of
    by file, otherwise the same 97 calls are counted three times.
    """
    seen: set[tuple[str, str, str]] = set()
    rows: list[dict] = []
    for path in sorted(reports_dir.rglob("*.json")):
        report = _load(path)
        metadata = report.get("metadata", {})
        stats = metadata.get("provider_stats")
        if not stats:
            continue
        key = (
            metadata.get("suite", "?"),
            metadata.get("created_at", "?"),
            json.dumps(stats, sort_keys=True),
        )
        if key in seen:
            continue
        seen.add(key)
        rows.append(
            {
                "suite": metadata.get("suite", "?"),
                "created_at": metadata.get("created_at", "?"),
                "mode": metadata.get("mode", "?"),
                "source": path.name,
                "stats": stats,
            }
        )
    rows.sort(key=lambda row: (row["suite"], row["created_at"], row["source"]))
    return rows


def _rate(numerator: int, denominator: int) -> str:
    if not denominator:
        return "-"
    return f"{numerator / denominator:.2%}"


def print_table(rows: Iterable[dict]) -> None:
    header = (
        f"{'suite':<18}{'created_at':<22}{'mode':<9}"
        + "".join(f"{label:>9}" for label in FIELDS.values())
        + f"{'retry%':>9}{'fail%':>9}"
    )
    print(header)
    for row in rows:
        stats = row["stats"]
        cells = "".join(
            f"{stats.get(field, 0):>9}" if field != "retry_latency_seconds"
            else f"{stats.get(field, 0):>9.2f}"
            for field in FIELDS
        )
        print(
            f"{row['suite']:<18}{row['created_at']:<22}{row['mode']:<9}{cells}"
            f"{_rate(stats.get('retries', 0), stats.get('calls', 0)):>9}"
            f"{_rate(stats.get('final_failures', 0), stats.get('calls', 0)):>9}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", choices=SUITES, help="restrict to one suite")
    parser.add_argument(
        "--live-only", action="store_true", help="skip offline re-scoring runs"
    )
    args = parser.parse_args()

    rows = collect()
    if args.suite:
        rows = [row for row in rows if row["suite"] == args.suite]
    if args.live_only:
        rows = [row for row in rows if row["mode"] == "live"]

    if not rows:
        print("No provider stats recorded yet; run a suite with --live.")
        return 0

    print(f"{len(rows)} run(s) with provider stats\n")
    print_table(rows)

    totals = {
        field: sum(row["stats"].get(field, 0) for row in rows)
        for field in FIELDS
        if field != "retry_latency_seconds"
    }
    latency = sum(row["stats"].get("retry_latency_seconds", 0) for row in rows)
    print(
        f"\ntotal calls={totals['calls']} retries={totals['retries']} "
        f"rescued={totals['retry_successes']} "
        f"final_failures={totals['final_failures']} "
        f"retry_latency={latency:.2f}s "
        f"retry_rate={_rate(totals['retries'], totals['calls'])}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
