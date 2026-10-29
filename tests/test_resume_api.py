import unittest
from collections.abc import AsyncIterator
from uuid import UUID

from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.app.api.deps import get_session
from backend.app.api.routes.resumes import get_storage
from backend.app.db.base import Base
from backend.app.infrastructure.queue import get_task_queue
from backend.app.main import app
from backend.app.models.analysis import AnalysisJob


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
        self.job_ids: list[str] = []

    async def enqueue_analysis(self, job_id: str) -> None:
        self.job_ids.append(job_id)

    async def close(self) -> None:
        return None


class ResumeApiTests(unittest.IsolatedAsyncioTestCase):
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
        self.client = AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        )
        registration = await self.client.post(
            "/api/v1/auth/register",
            json={
                "email": "resume-owner@example.com",
                "password": "a-long-test-password",
            },
        )
        token_response = await self.client.post(
            "/api/v1/auth/token",
            data={
                "username": "resume-owner@example.com",
                "password": "a-long-test-password",
            },
        )
        self.auth_headers = {
            "Authorization": f"Bearer {token_response.json()['access_token']}"
        }
        self.assertEqual(registration.status_code, 201)

    async def asyncTearDown(self) -> None:
        await self.client.aclose()
        app.dependency_overrides.pop(get_session, None)
        app.dependency_overrides.pop(get_storage, None)
        app.dependency_overrides.pop(get_task_queue, None)
        await self.engine.dispose()

    async def test_upload_creates_history_and_queues_analysis(self) -> None:
        response = await self.client.post(
            "/api/v1/resumes",
            headers=self.auth_headers,
            data={"job_description": "Looking for a Python engineer."},
            files={"file": ("resume.txt", b"Python developer", "text/plain")},
        )
        self.assertEqual(response.status_code, 202)
        job = response.json()
        self.assertEqual(job["status"], "queued")
        self.assertEqual(job["resume_filename"], "resume.txt")
        self.assertEqual(self.queue.job_ids, [job["id"]])
        self.assertEqual(list(self.storage.objects.values()), [b"Python developer"])

        details = await self.client.get(
            f"/api/v1/analyses/{job['id']}",
            headers=self.auth_headers,
        )
        self.assertEqual(details.status_code, 200)
        self.assertEqual(details.json()["id"], job["id"])

        history = await self.client.get(
            "/api/v1/analyses",
            headers=self.auth_headers,
        )
        self.assertEqual(history.status_code, 200)
        self.assertEqual(len(history.json()), 1)

        async with self.session_factory() as session:
            stored_job = await session.scalar(
                select(AnalysisJob).where(AnalysisJob.id == UUID(job["id"]))
            )
            stored_job.status = "completed"
            stored_job.result = {
                "match_score": 88,
                "matched_skills": ["Python"],
                "missing_skills": [],
                "strengths": ["Built an AI tool"],
                "gaps": [],
                "resume_improvement_tips": [],
                "interview_questions": [],
            }
            await session.commit()

        report = await self.client.get(
            f"/api/v1/analyses/{job['id']}/report",
            headers=self.auth_headers,
        )
        self.assertEqual(report.status_code, 200)
        self.assertTrue(report.content.startswith(b"%PDF-"))

        await self.client.post(
            "/api/v1/auth/register",
            json={
                "email": "other-owner@example.com",
                "password": "another-long-test-password",
            },
        )
        other_token = await self.client.post(
            "/api/v1/auth/token",
            data={
                "username": "other-owner@example.com",
                "password": "another-long-test-password",
            },
        )
        other_headers = {
            "Authorization": f"Bearer {other_token.json()['access_token']}"
        }
        private_details = await self.client.get(
            f"/api/v1/analyses/{job['id']}",
            headers=other_headers,
        )
        self.assertEqual(private_details.status_code, 404)

    async def test_upload_rejects_unsupported_type(self) -> None:
        response = await self.client.post(
            "/api/v1/resumes",
            headers=self.auth_headers,
            data={"job_description": "Looking for a Python engineer."},
            files={"file": ("resume.exe", b"not a resume", "application/octet-stream")},
        )
        self.assertEqual(response.status_code, 415)
        self.assertEqual(self.queue.job_ids, [])


if __name__ == "__main__":
    unittest.main()
