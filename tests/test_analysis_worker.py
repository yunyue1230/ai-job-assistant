import unittest
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.app.db.base import Base
from backend.app.models.analysis import AnalysisJob
from backend.app.models.resume import Resume
from backend.app.models.user import User
from backend.app.workers.consumer import process_analysis_job


class MemoryStorage:
    def __init__(self, content: bytes) -> None:
        self.content = content

    def read(self, _: str) -> bytes:
        return self.content


class AnalysisWorkerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def _create_job(self) -> UUID:
        async with self.session_factory() as session:
            user = User(email="worker@example.com", hashed_password="not-used")
            session.add(user)
            await session.flush()
            resume = Resume(
                user_id=user.id,
                original_filename="resume.txt",
                object_key="resumes/user/resume.txt",
                content_type="text/plain",
                size_bytes=15,
                sha256="a" * 64,
            )
            session.add(resume)
            await session.flush()
            job = AnalysisJob(
                user_id=user.id,
                resume_id=resume.id,
                job_description="Python role",
                status="queued",
            )
            session.add(job)
            await session.commit()
            return job.id

    async def test_worker_persists_analysis_result(self) -> None:
        job_id = await self._create_job()

        def analyze(job_description: str, resume_text: str) -> dict:
            self.assertEqual(job_description, "Python role")
            self.assertEqual(resume_text, "Python developer")
            return {"match_score": 88}

        await process_analysis_job(
            job_id,
            session_factory=self.session_factory,
            storage_factory=lambda: MemoryStorage(b"Python developer"),
            analyzer=analyze,
        )

        async with self.session_factory() as session:
            job = await session.scalar(
                select(AnalysisJob).where(AnalysisJob.id == job_id)
            )
            self.assertEqual(job.status, "completed")
            self.assertEqual(job.result, {"match_score": 88})
            self.assertEqual(job.attempts, 1)

    async def test_worker_records_failure_for_retry(self) -> None:
        job_id = await self._create_job()

        def fail_analysis(_: str, __: str) -> dict:
            raise RuntimeError("provider unavailable")

        await process_analysis_job(
            job_id,
            session_factory=self.session_factory,
            storage_factory=lambda: MemoryStorage(b"Python developer"),
            analyzer=fail_analysis,
        )

        async with self.session_factory() as session:
            job = await session.scalar(
                select(AnalysisJob).where(AnalysisJob.id == job_id)
            )
            self.assertEqual(job.status, "failed")
            self.assertEqual(job.attempts, 1)
            self.assertIsNotNone(job.error_message)


if __name__ == "__main__":
    unittest.main()
