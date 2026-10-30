"""Report storage and baseline comparison.

Every run writes two files under ``evals/reports/``:

- ``<suite>.latest.json``  -- always the newest run, used for comparisons.
- ``<suite>-<timestamp>.json`` -- immutable record for the history.

Promoting a run with ``python -m evals.compare --suite <suite> --promote``
copies it to ``<suite>.baseline.json``, which is what CI and ``--check`` diff
against.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
REPORTS_DIR = Path(__file__).resolve().parents[1] / "reports"


def git_sha() -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


@dataclass(frozen=True)
class RunMetadata:
    suite: str
    mode: str
    provider: str
    model: str
    dataset: str
    sample_count: int
    created_at: str
    git_sha: str | None = None
    snapshot: str | None = None
    notes: str | None = None
    # Call accounting for live runs (calls, retries, failures). Kept out of
    # metrics so a provider hiccup never shows up as a metric regression.
    provider_stats: dict[str, Any] | None = None

    @classmethod
    def build(
        cls,
        suite: str,
        mode: str,
        provider: str,
        model: str,
        dataset: str,
        sample_count: int,
        snapshot: str | None = None,
        notes: str | None = None,
        provider_stats: dict[str, Any] | None = None,
    ) -> "RunMetadata":
        return cls(
            suite=suite,
            mode=mode,
            provider=provider,
            model=model,
            dataset=dataset,
            sample_count=sample_count,
            created_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            git_sha=git_sha(),
            snapshot=snapshot,
            notes=notes,
            provider_stats=provider_stats,
        )


def report_path(suite: str, kind: str = "latest") -> Path:
    return REPORTS_DIR / f"{suite}.{kind}.json"


# Only a live run carries provider metadata and fresh predictions, so it is the
# only mode that may become a baseline without an explicit override.
PROMOTABLE_MODE = "live"


def promotion_blockers(report: dict[str, Any]) -> list[str]:
    """Reasons why ``report`` should not quietly become the baseline.

    Two mistakes are worth stopping, both of which produce a baseline that
    looks fine but is not:

    - promoting an offline re-score. ``run_all --compare`` overwrites
      ``<suite>.latest.json``, so the newest report is often a free re-score of
      an old snapshot: its metadata records the re-scoring commit, not the run
      that produced the predictions.
    - promoting a run that did not score every sample. Missing predictions do
      not raise; they just shrink the sample the metrics describe.
    """
    metadata = report.get("metadata") or {}
    metrics = report.get("metrics") or {}
    blockers: list[str] = []

    mode = metadata.get("mode")
    if mode != PROMOTABLE_MODE:
        if mode == "offline":
            detail = "offline re-scores overwrite <suite>.latest.json"
        else:
            detail = "only a live run carries provider metadata"
        blockers.append(
            f"mode={mode!r} ({detail}); rerun the suite with --live before promoting"
        )

    samples = metrics.get("samples")
    scored = metrics.get("scored_samples")
    if isinstance(samples, int) and isinstance(scored, int) and scored < samples:
        blockers.append(f"only {scored}/{samples} samples were scored")

    valid_rate = metrics.get("schema_valid_rate")
    if isinstance(valid_rate, (int, float)) and not isinstance(valid_rate, bool):
        if valid_rate < 1:
            blockers.append(f"schema_valid_rate={valid_rate:.4f} (below 1.0)")

    return blockers


def write_report(suite: str, metrics: dict[str, Any], metadata: RunMetadata) -> Path:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    payload = {"suite": suite, "metadata": asdict(metadata), "metrics": metrics}
    latest = report_path(suite, "latest")
    latest.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    history = REPORTS_DIR / f"{suite}-{stamp}.json"
    history.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return latest


def load_report(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def compare_reports(
    baseline: dict[str, Any], candidate: dict[str, Any]
) -> dict[str, dict[str, float | None]]:
    """Return per-metric baseline/candidate/delta rows for numeric metrics."""
    rows: dict[str, dict[str, float | None]] = {}
    base_metrics = baseline.get("metrics", {})
    candidate_metrics = candidate.get("metrics", {})
    for name in sorted(set(base_metrics) | set(candidate_metrics)):
        before = base_metrics.get(name)
        after = candidate_metrics.get(name)
        delta: float | None = None
        if isinstance(before, (int, float)) and isinstance(after, (int, float)):
            delta = round(float(after) - float(before), 4)
        rows[name] = {"baseline": before, "candidate": after, "delta": delta}
    return rows


def print_report(path: Path, metrics: dict[str, Any], metadata: RunMetadata) -> None:
    print(f"\n=== {metadata.suite} ({metadata.mode}) ===")
    print(
        f"provider={metadata.provider} model={metadata.model} "
        f"samples={metadata.sample_count} at {metadata.created_at}"
    )
    for name, value in metrics.items():
        rendered = f"{value:.4f}" if isinstance(value, float) else str(value)
        print(f"  {name:<32} {rendered}")
    if metadata.provider_stats:
        print("  -- provider calls --")
        for name, value in metadata.provider_stats.items():
            print(f"  {name:<32} {value}")
    print(f"  report: {path}")


def print_comparison(
    suite: str, rows: dict[str, dict[str, float | None]]
) -> None:
    print(f"\n=== {suite}: candidate vs baseline ===")
    print(f"  {'metric':<32} {'baseline':>10} {'candidate':>10} {'delta':>10}")
    for name, row in rows.items():
        before = row["baseline"]
        after = row["candidate"]
        delta = row["delta"]
        rendered = lambda value: "-" if value is None else f"{value:.4f}"  # noqa: E731
        print(
            f"  {name:<32} {rendered(before):>10} {rendered(after):>10} "
            f"{rendered(delta):>10}"
        )
