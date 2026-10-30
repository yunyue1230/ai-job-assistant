"""Build a retrieval evaluation corpus from source documents.

The corpus is produced with the same ``split_text`` the ingestion worker uses,
so changing --chunk-size/--overlap measures the real chunking effect instead of
a hand-written approximation.

Sources default to every .md/.txt/.rst file in ``evals/raw_docs`` (see
``raw_docs/SOURCES.md`` for provenance); pass --source to use other files.

    python -m evals.build_corpus --chunk-size 1000 --overlap 150
    python -m evals.build_corpus --chunk-size 600 --overlap 80 --out ...600.jsonl
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from backend.app.ai.document_processing import split_text

ROOT = Path(__file__).resolve().parents[1]
RAW_DOCS_DIR = ROOT / "evals" / "raw_docs"
DEFAULT_OUT = ROOT / "evals" / "datasets" / "rag_corpus.jsonl"


def default_sources() -> list[Path]:
    """Every source document in ``evals/raw_docs``, sorted for stable ids.

    The repository's own markdown is deliberately not used: a corpus made of
    this project's README is self-referential, does not look like the documents
    a user actually uploads, and leaks repository content to the embedding
    provider.
    """
    return sorted(
        path
        for path in RAW_DOCS_DIR.glob("*")
        if path.suffix.lower() in {".md", ".txt", ".rst"}
        and path.is_file()
        and path.stem.upper() not in {"README", "SOURCES"}
    )


def _prefix(source: Path) -> str:
    """Readable, collision-free chunk id prefix.

    ``parent[:2] + stem[:3]`` alone is not unique: several documents in one
    directory usually share the first characters, and duplicate chunk ids make
    label resolution score the wrong text. A short hash of the file name keeps
    ids stable across runs and independent of ``--source`` ordering.
    """
    parent = source.parent.name or "root"
    stem = "".join(char for char in f"{parent[:2]}{source.stem[:4]}" if char.isalnum())
    digest = hashlib.sha1(source.name.encode("utf-8")).hexdigest()[:3]
    return f"{stem.upper()}-{digest.upper()}"


def build(sources: list[Path], chunk_size: int, overlap: int) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    seen: set[str] = set()
    for source in sources:
        if not source.exists():
            print(f"skipping missing source: {source}")
            continue
        prefix = _prefix(source)
        for index, chunk in enumerate(
            split_text(source.read_text(encoding="utf-8"), chunk_size, overlap), start=1
        ):
            chunk_id = f"{prefix}-{index:03d}"
            if chunk_id in seen:
                raise ValueError(
                    f"duplicate chunk id {chunk_id} from {source}; rename the file"
                )
            seen.add(chunk_id)
            records.append(
                {
                    "chunk_id": chunk_id,
                    "doc": source.name,
                    "text": chunk,
                }
            )
    return records


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chunk-size", type=int, default=1000)
    parser.add_argument("--overlap", type=int, default=150)
    parser.add_argument(
        "--source", action="append", default=None, help="repeatable; overrides defaults"
    )
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    args = parser.parse_args()

    sources = [Path(item) for item in args.source] if args.source else default_sources()
    if not sources:
        print(f"no source documents found in {RAW_DOCS_DIR}; add .md/.txt files first")
        return 1
    records = build(sources, args.chunk_size, args.overlap)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    print(
        f"wrote {len(records)} chunks to {out} "
        f"(chunk_size={args.chunk_size} overlap={args.overlap})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
