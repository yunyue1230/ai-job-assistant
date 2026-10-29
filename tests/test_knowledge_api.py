import unittest
from collections.abc import AsyncIterator

from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.app.ai.embeddings import EmbeddingService
from backend.app.api.deps import get_session
from backend.app.api.routes.knowledge_bases import get_storage
from backend.app.db.base import Base
from backend.app.infrastructure.queue import get_task_queue
from backend.app.main import app


class MemoryStorage:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def save(self, key: str, content: bytes) -> str:
        self.objects[key] = content
        return key

    def read(self, key: str) -> bytes:
        return self.objects[key]

    def delete(self, key: str) -> None:
        self.objects.pop(key, None)


class MemoryQueue:
    def __init__(self) -> None:
        self.document_ids: list[str] = []

    async def enqueue_document(self, document_id: str) -> None:
        self.document_ids.append(document_id)

    async def close(self) -> None:
        return None


class FakeEmbeddings:
    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.0] * 1536 for _ in texts]


class KnowledgeApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)
        self.storage = MemoryStorage()
        self.queue = MemoryQueue()

        async def override_get_session() -> AsyncIterator:
            async with self.session_factory() as session:
                yield session

        async def override_get_queue() -> AsyncIterator[MemoryQueue]:
            yield self.queue

        app.dependency_overrides[get_session] = override_get_session
        app.dependency_overrides[get_storage] = lambda: self.storage
        app.dependency_overrides[get_task_queue] = override_get_queue
        app.dependency_overrides[EmbeddingService.from_settings] = FakeEmbeddings
        self.client = AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        )
        await self.client.post(
            "/api/v1/auth/register",
            json={
                "email": "kb-owner@example.com",
                "password": "a-long-test-password",
            },
        )
        token = await self.client.post(
            "/api/v1/auth/token",
            data={
                "username": "kb-owner@example.com",
                "password": "a-long-test-password",
            },
        )
        self.headers = {"Authorization": f"Bearer {token.json()['access_token']}"}

    async def asyncTearDown(self) -> None:
        await self.client.aclose()
        app.dependency_overrides.pop(get_session, None)
        app.dependency_overrides.pop(get_storage, None)
        app.dependency_overrides.pop(get_task_queue, None)
        app.dependency_overrides.pop(EmbeddingService.from_settings, None)
        await self.engine.dispose()

    async def test_create_knowledge_base_upload_document_and_list(self) -> None:
        created = await self.client.post(
            "/api/v1/knowledge-bases",
            headers=self.headers,
            json={"name": "Python interview notes", "description": "Study material"},
        )
        self.assertEqual(created.status_code, 201)
        knowledge_base_id = created.json()["id"]

        upload = await self.client.post(
            f"/api/v1/knowledge-bases/{knowledge_base_id}/documents",
            headers=self.headers,
            files={"file": ("notes.md", b"Python interview concepts", "text/markdown")},
        )
        self.assertEqual(upload.status_code, 202)
        document = upload.json()
        self.assertEqual(document["status"], "queued")
        self.assertEqual(document["original_filename"], "notes.md")
        self.assertEqual(self.queue.document_ids, [document["id"]])
        self.assertEqual(list(self.storage.objects.values()), [b"Python interview concepts"])

        documents = await self.client.get(
            f"/api/v1/knowledge-bases/{knowledge_base_id}/documents",
            headers=self.headers,
        )
        self.assertEqual(documents.status_code, 200)
        self.assertEqual(len(documents.json()), 1)

        chat = await self.client.post(
            f"/api/v1/knowledge-bases/{knowledge_base_id}/chat",
            headers=self.headers,
            json={"question": "What are Python decorators?"},
        )
        self.assertEqual(chat.status_code, 409)
        self.assertIn("no processed documents", chat.json()["detail"])

    async def test_user_cannot_access_another_users_knowledge_base(self) -> None:
        created = await self.client.post(
            "/api/v1/knowledge-bases",
            headers=self.headers,
            json={"name": "Private notes"},
        )
        other_registration = await self.client.post(
            "/api/v1/auth/register",
            json={
                "email": "other-kb-user@example.com",
                "password": "another-long-test-password",
            },
        )
        self.assertEqual(other_registration.status_code, 201)
        other_token = await self.client.post(
            "/api/v1/auth/token",
            data={
                "username": "other-kb-user@example.com",
                "password": "another-long-test-password",
            },
        )
        response = await self.client.get(
            f"/api/v1/knowledge-bases/{created.json()['id']}/documents",
            headers={
                "Authorization": f"Bearer {other_token.json()['access_token']}"
            },
        )
        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
