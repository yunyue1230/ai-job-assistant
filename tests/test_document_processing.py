import unittest

from backend.app.ai.document_processing import (
    DocumentPiece,
    chunk_document,
    extract_document_pieces,
    split_text,
)


class DocumentProcessingTests(unittest.TestCase):
    def test_splits_text_with_overlap_and_respects_size(self) -> None:
        text = " ".join(f"token{i}" for i in range(30))

        chunks = split_text(text, chunk_size=40, overlap=10)

        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(chunk) <= 40 for chunk in chunks))
        self.assertTrue(
            any(set(left.split()) & set(right.split()) for left, right in zip(chunks, chunks[1:]))
        )

    def test_preserves_page_metadata_when_chunking_pages(self) -> None:
        chunks = chunk_document(
            [
                DocumentPiece("First-page content", 1),
                DocumentPiece("Second-page content", 2),
            ],
            chunk_size=100,
            overlap=10,
        )

        self.assertEqual(chunks, [("First-page content", 1), ("Second-page content", 2)])

    def test_extracts_utf8_text_documents(self) -> None:
        pieces = extract_document_pieces("notes.md", "RAG 知识资料".encode("utf-8"))

        self.assertEqual(len(pieces), 1)
        self.assertEqual(pieces[0].text, "RAG 知识资料")
        self.assertIsNone(pieces[0].page_number)

    def test_rejects_empty_or_unsupported_documents(self) -> None:
        with self.assertRaisesRegex(ValueError, "readable text"):
            extract_document_pieces("empty.txt", b"  ")
        with self.assertRaisesRegex(ValueError, "Unsupported"):
            extract_document_pieces("notes.csv", b"a,b")


if __name__ == "__main__":
    unittest.main()
