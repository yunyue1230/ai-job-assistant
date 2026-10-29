from dataclasses import dataclass
from io import BytesIO
from pathlib import PurePosixPath

from docx import Document
from pypdf import PdfReader

from backend.app.core.config import get_settings


@dataclass(frozen=True)
class DocumentPiece:
    text: str
    page_number: int | None


def extract_document_pieces(filename: str, content: bytes) -> list[DocumentPiece]:
    extension = PurePosixPath(filename).suffix.lower()
    if extension in {".txt", ".md"}:
        text = content.decode("utf-8-sig").strip()
        pieces = [DocumentPiece(text, None)] if text else []
    elif extension == ".pdf":
        reader = PdfReader(BytesIO(content))
        pieces = [
            DocumentPiece(text.strip(), page_number)
            for page_number, page in enumerate(reader.pages, start=1)
            if (text := (page.extract_text() or "").strip())
        ]
    elif extension == ".docx":
        document = Document(BytesIO(content))
        text = "\n".join(
            paragraph.text.strip()
            for paragraph in document.paragraphs
            if paragraph.text.strip()
        )
        pieces = [DocumentPiece(text, None)] if text else []
    else:
        raise ValueError("Unsupported document type.")

    if not pieces:
        raise ValueError("The document does not contain readable text.")
    return pieces


def split_text(text: str, chunk_size: int, overlap: int) -> list[str]:
    if chunk_size <= 0:
        raise ValueError("Chunk size must be greater than zero.")
    if overlap < 0 or overlap >= chunk_size:
        raise ValueError("Chunk overlap must be between zero and chunk size.")

    normalized = " ".join(text.split())
    if not normalized:
        return []

    chunks: list[str] = []
    start = 0
    while start < len(normalized):
        end = min(start + chunk_size, len(normalized))
        if end < len(normalized):
            boundary = max(
                normalized.rfind(" ", start, end),
                normalized.rfind(".", start, end),
                normalized.rfind(";", start, end),
                normalized.rfind("。", start, end),
                normalized.rfind("\n", start, end),
            )
            if boundary > start + chunk_size // 2:
                end = boundary + 1
        chunk = normalized[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(normalized):
            break
        start = max(start + 1, end - overlap)
    return chunks


def chunk_document(
    pieces: list[DocumentPiece],
    chunk_size: int | None = None,
    overlap: int | None = None,
) -> list[tuple[str, int | None]]:
    settings = get_settings()
    size = chunk_size or settings.rag_chunk_size
    overlap_size = settings.rag_chunk_overlap if overlap is None else overlap
    output: list[tuple[str, int | None]] = []
    for piece in pieces:
        output.extend(
            (text, piece.page_number)
            for text in split_text(piece.text, size, overlap_size)
        )
    return output
