"""Dataset loading helpers for the evaluation suites."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

DATASETS_DIR = Path(__file__).resolve().parents[1] / "datasets"


def load_jsonl(path: str | Path) -> list[dict[str, Any]]:
    """Load a JSONL dataset, skipping blank lines and ``#`` comments."""
    file_path = Path(path)
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(
        file_path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        try:
            record = json.loads(stripped)
        except json.JSONDecodeError as error:
            raise ValueError(
                f"{file_path}:{line_number} is not valid JSON: {error}"
            ) from error
        if not isinstance(record, dict):
            raise ValueError(f"{file_path}:{line_number} must be a JSON object.")
        records.append(record)
    if not records:
        raise ValueError(f"{file_path} does not contain any evaluation sample.")
    return records


def normalize_for_match(text: str) -> str:
    return " ".join(text.split()).lower()


def resolve_relevant_chunks(
    query: dict[str, Any], corpus: list[dict[str, Any]]
) -> list[str]:
    """Relevant chunks for a query, resolved from content anchors when present.

    Chunk ids depend on how the corpus was chunked, so a label expressed as an
    id silently points at different text the moment chunk size or overlap
    changes. Anchors are short quotes from the source text and stay valid across
    chunkings, which is what makes the chunk-size experiment meaningful.
    """
    anchors = [normalize_for_match(item) for item in query.get("relevant_anchors") or []]
    if not anchors:
        return list(query.get("relevant_chunk_ids", []))
    matched = [
        chunk["chunk_id"]
        for chunk in corpus
        if any(anchor in normalize_for_match(chunk["text"]) for anchor in anchors)
    ]
    if not matched:
        raise ValueError(
            f"query {query['id']}: no corpus chunk contains anchors "
            f"{query['relevant_anchors']}. The corpus was probably rebuilt with a "
            "different chunk size; pick shorter or different anchors."
        )
    return matched


def dataset_path(name: str) -> Path:
    return DATASETS_DIR / name
