import unittest
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.app.db.base import Base
from backend.app.models.knowledge_base import (
    DocumentChunk,
    KnowledgeBase,
    KnowledgeDocument,
)
from backend.app.models.user import User
from backend.app.workers.consumer import process_knowledge_document


class MemoryStorage:
    def __init__(self, content: bytes) -> None:
        self.content = content

    def read(self, _: str) -> bytes:
        return self.content


class FakeEmbeddings:
    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.1] * 1536 for _ in texts]


class KnowledgeWorkerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def _create_document(self) -> UUID:
        async with self.session_factory() as session:
            user = User(email="knowledge@example.com", hashed_password="not-used")
            session.add(user)
            await session.flush()
            knowledge_base = KnowledgeBase(user_id=user.id, name="Interview notes")
            session.add(knowledge_base)
            await session.flush()
            document = KnowledgeDocument(
                knowledge_base_id=knowledge_base.id,
                original_filename="notes.txt",
                object_key="knowledge/notes.txt",
                content_type="text/plain",
                size_bytes=29,
                sha256="b" * 64,
                status="queued",
            )
            session.add(document)
            await session.commit()
            return document.id

    async def test_worker_splits_embeds_and_persists_document_chunks(self) -> None:
        document_id = await self._create_document()

        await process_knowledge_document(
            document_id,
            session_factory=self.session_factory,
            storage_factory=lambda: MemoryStorage(
                b"Embeddings represent semantic meaning."
            ),
            embedding_factory=FakeEmbeddings,
        )

        async with self.session_factory() as session:
            document = await session.get(KnowledgeDocument, document_id)
            chunks = (
                await session.scalars(
                    select(DocumentChunk).where(
                        DocumentChunk.document_id == document_id
                    )
                )
            ).all()
            self.assertEqual(document.status, "ready")
            self.assertEqual(document.chunk_count, 1)
            self.assertEqual(chunks[0].content, "Embeddings represent semantic meaning.")
            self.assertEqual(len(chunks[0].embedding), 1536)

    async def test_worker_records_ingestion_failure(self) -> None:
        document_id = await self._create_document()

        await process_knowledge_document(
            document_id,
            session_factory=self.session_factory,
            storage_factory=lambda: MemoryStorage(b"\xff\xfe"),
            embedding_factory=FakeEmbeddings,
        )

        async with self.session_factory() as session:
            document = await session.get(KnowledgeDocument, document_id)
            self.assertEqual(document.status, "failed")
            self.assertIsNotNone(document.error_message)
            self.assertEqual(document.chunk_count, 0)


if __name__ == "__main__":
    unittest.main()
