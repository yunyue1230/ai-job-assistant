"""Prediction snapshots: run once against a provider, re-score for free."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SNAPSHOT_DIR = Path(__file__).resolve().parents[1] / "snapshots"


def _slug(value: str) -> str:
    return "".join(character if character.isalnum() else "-" for character in value)


def write_predictions(
    suite: str, model: str, records: list[dict[str, Any]]
) -> Path:
    """Persist raw predictions and refresh the ``latest`` pointer."""
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = SNAPSHOT_DIR / f"{suite}.{_slug(model)}-{stamp}.jsonl"
    payload = "".join(
        json.dumps(record, ensure_ascii=False) + "\n" for record in records
    )
    path.write_text(payload, encoding="utf-8")
    latest = SNAPSHOT_DIR / f"{suite}.{_slug(model)}.latest.jsonl"
    latest.write_text(payload, encoding="utf-8")
    return latest


def read_predictions(path: str | Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in Path(path).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def resolve_snapshot(suite: str, model: str | None = None) -> Path | None:
    """Find the newest snapshot for a suite, optionally filtered by model."""
    if not SNAPSHOT_DIR.exists():
        return None
    if model:
        exact = SNAPSHOT_DIR / f"{suite}.{_slug(model)}.latest.jsonl"
        return exact if exact.exists() else None
    candidates = sorted(
        SNAPSHOT_DIR.glob(f"{suite}.*.latest.jsonl"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    return candidates[0] if candidates else None


def snapshot_model(path: Path) -> str:
    """Recover the model name embedded in a snapshot filename."""
    parts = path.name.split(".")
    return parts[1] if len(parts) > 2 else "unknown"
