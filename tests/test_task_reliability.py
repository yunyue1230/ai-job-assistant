"""Reliability of queued tasks: no double work, bounded retries, recovery.

These cover the failure modes that a green unit suite says nothing about: the
same task delivered twice, several workers racing for it, a provider that
fails once, a provider that never recovers, and a worker that dies mid-task.
"""

import asyncio
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.app.db.base import Base
from backend.app.models.analysis import AnalysisJob
from backend.app.models.knowledge_base import (
    DocumentChunk,
    KnowledgeBase,
    KnowledgeDocument,
)
from backend.app.models.resume import Resume
from backend.app.models.user import User
from backend.app.workers.consumer import (
    _claim_task,
    backoff_delay,
    process_analysis_job,
    process_knowledge_document,
    recover_stuck_analysis_jobs,
    recover_stuck_documents,
)


class MemoryStorage:
    def __init__(self, content: bytes) -> None:
        self.content = content

    def read(self, _: str) -> bytes:
        return self.content


class FakeQueue:
    def __init__(self) -> None:
        self.analysis: list[str] = []
        self.documents: list[str] = []

    async def enqueue_analysis(self, job_id: str) -> None:
        self.analysis.append(job_id)

    async def enqueue_document(self, document_id: str) -> None:
        self.documents.append(document_id)

    async def close(self) -> None:
        return None


class TaskTestBase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        database = Path(directory.name) / "tasks.db"
        # A file database so concurrent workers use separate connections, the
        # way they would against PostgreSQL.
        self.engine = create_async_engine(
            f"sqlite+aiosqlite:///{database}", connect_args={"timeout": 15}
        )
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def _user(self, email: str) -> UUID:
        async with self.session_factory() as session:
            existing = await session.scalar(
                select(User).where(User.email == email)
            )
            if existing is not None:
                return existing.id
            user = User(email=email, hashed_password="not-used")
            session.add(user)
            await session.commit()
            return user.id

    async def _create_job(self, max_attempts: int = 3) -> UUID:
        user_id = await self._user("worker@example.com")
        async with self.session_factory() as session:
            resume = Resume(
                user_id=user_id,
                original_filename="resume.txt",
                object_key=f"resumes/user/{uuid4().hex}.txt",
                content_type="text/plain",
                size_bytes=15,
                sha256=uuid4().hex + uuid4().hex,
            )
            session.add(resume)
            await session.flush()
            job = AnalysisJob(
                user_id=user_id,
                resume_id=resume.id,
                job_description="Python role",
                status="queued",
                max_attempts=max_attempts,
            )
            session.add(job)
            await session.commit()
            return job.id

    async def _create_document(self, max_attempts: int = 3) -> UUID:
        user_id = await self._user("knowledge@example.com")
        async with self.session_factory() as session:
            knowledge_base = KnowledgeBase(user_id=user_id, name="Interview notes")
            session.add(knowledge_base)
            await session.flush()
            document = KnowledgeDocument(
                knowledge_base_id=knowledge_base.id,
                original_filename="notes.txt",
                object_key=f"knowledge/{uuid4().hex}.txt",
                content_type="text/plain",
                size_bytes=29,
                sha256=uuid4().hex + uuid4().hex,
                status="queued",
                max_attempts=max_attempts,
            )
            session.add(document)
            await session.commit()
            return document.id

    async def _mark_processing(
        self, model: type, object_id: UUID, *, age: int, attempts: int = 1
    ) -> None:
        """Pretend a worker claimed the task ``age`` seconds ago and died."""
        async with self.session_factory() as session:
            await session.execute(
                update(model)
                .where(model.id == object_id)
                .values(
                    status="processing",
                    attempts=attempts,
                    updated_at=datetime.now(timezone.utc) - timedelta(seconds=age),
                )
            )
            await session.commit()

    async def _age(self, model: type, object_id: UUID, seconds: int) -> None:
        async with self.session_factory() as session:
            await session.execute(
                update(model)
                .where(model.id == object_id)
                .values(
                    updated_at=datetime.now(timezone.utc)
                    - timedelta(seconds=seconds)
                )
            )
            await session.commit()

    def _recorder(self) -> tuple[list[float], object]:
        delays: list[float] = []

        async def sleeper(delay: float) -> None:
            delays.append(delay)

        return delays, sleeper


class AnalysisReliabilityTests(TaskTestBase):
    async def _job(self, job_id: UUID) -> AnalysisJob:
        async with self.session_factory() as session:
            job = await session.scalar(
                select(AnalysisJob).where(AnalysisJob.id == job_id)
            )
            self.assertIsNotNone(job)
            return job

    async def test_duplicate_delivery_runs_the_model_once(self) -> None:
        job_id = await self._create_job()
        calls: list[str] = []

        def analyze(job_description: str, resume_text: str) -> dict:
            calls.append(resume_text)
            return {"match_score": 88}

        for _ in range(2):
            await process_analysis_job(
                job_id,
                session_factory=self.session_factory,
                storage_factory=lambda: MemoryStorage(b"Python developer"),
                analyzer=analyze,
            )

        job = await self._job(job_id)
        self.assertEqual(calls, ["Python developer"])
        self.assertEqual(job.status, "completed")
        self.assertEqual(job.attempts, 1)

    async def test_concurrent_workers_run_the_model_once(self) -> None:
        job_id = await self._create_job()
        calls: list[str] = []

        def analyze(_: str, resume_text: str) -> dict:
            calls.append(resume_text)
            return {"match_score": 88}

        await asyncio.gather(
            *[
                process_analysis_job(
                    job_id,
                    session_factory=self.session_factory,
                    storage_factory=lambda: MemoryStorage(b"Python developer"),
                    analyzer=analyze,
                )
                for _ in range(5)
            ]
        )

        job = await self._job(job_id)
        self.assertEqual(len(calls), 1)
        self.assertEqual(job.status, "completed")
        self.assertEqual(job.attempts, 1)

    async def test_completed_jobs_are_never_reprocessed(self) -> None:
        job_id = await self._create_job()
        calls: list[str] = []

        def analyze(_: str, __: str) -> dict:
            calls.append("called")
            return {"match_score": 88}

        await process_analysis_job(
            job_id,
            session_factory=self.session_factory,
            storage_factory=lambda: MemoryStorage(b"Python developer"),
            analyzer=analyze,
        )
        await process_analysis_job(
            job_id,
            session_factory=self.session_factory,
            storage_factory=lambda: MemoryStorage(b"Python developer"),
            analyzer=analyze,
        )
        self.assertEqual(len(calls), 1)

    async def test_transient_failure_is_retried_then_succeeds(self) -> None:
        job_id = await self._create_job()
        calls: list[int] = []
        delays, sleeper = self._recorder()

        def flaky(_: str, __: str) -> dict:
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("provider unavailable")
            return {"match_score": 88}

        await process_analysis_job(
            job_id,
            session_factory=self.session_factory,
            storage_factory=lambda: MemoryStorage(b"Python developer"),
            analyzer=flaky,
            sleeper=sleeper,
        )

        job = await self._job(job_id)
        self.assertEqual(len(calls), 2)
        self.assertEqual(job.status, "completed")
        self.assertEqual(job.result, {"match_score": 88})
        self.assertEqual(job.attempts, 2)
        self.assertEqual(delays, [backoff_delay(1)])

    async def test_exhausted_attempts_fail_with_a_diagnosable_reason(self) -> None:
        job_id = await self._create_job(max_attempts=3)
        calls: list[int] = []
        delays, sleeper = self._recorder()

        def broken(_: str, __: str) -> dict:
            calls.append(1)
            raise RuntimeError("provider unavailable")

        await process_analysis_job(
            job_id,
            session_factory=self.session_factory,
            storage_factory=lambda: MemoryStorage(b"Python developer"),
            analyzer=broken,
            sleeper=sleeper,
        )

        job = await self._job(job_id)
        self.assertEqual(len(calls), 3)
        self.assertEqual(job.status, "failed")
        self.assertEqual(job.attempts, 3)
        self.assertIn("RuntimeError", job.error_message or "")
        self.assertIn("3 attempts", job.error_message or "")
        self.assertEqual(delays, [backoff_delay(1), backoff_delay(2)])

    async def test_single_attempt_job_fails_immediately(self) -> None:
        job_id = await self._create_job(max_attempts=1)
        calls: list[int] = []
        delays, sleeper = self._recorder()

        def broken(_: str, __: str) -> dict:
            calls.append(1)
            raise RuntimeError("provider unavailable")

        await process_analysis_job(
            job_id,
            session_factory=self.session_factory,
            storage_factory=lambda: MemoryStorage(b"Python developer"),
            analyzer=broken,
            sleeper=sleeper,
        )

        job = await self._job(job_id)
        self.assertEqual(len(calls), 1)
        self.assertEqual(job.status, "failed")
        self.assertEqual(delays, [])

    async def test_unreadable_resume_is_not_retried(self) -> None:
        job_id = await self._create_job()
        calls: list[int] = []
        delays, sleeper = self._recorder()

        def never_called(_: str, __: str) -> dict:
            calls.append(1)
            return {}

        await process_analysis_job(
            job_id,
            session_factory=self.session_factory,
            storage_factory=lambda: MemoryStorage(b"\xff\xfe"),
            analyzer=never_called,
            sleeper=sleeper,
        )

        job = await self._job(job_id)
        # Parsing the same bytes cannot succeed later, so no attempt is spent.
        self.assertEqual(calls, [])
        self.assertEqual(delays, [])
        self.assertEqual(job.status, "failed")
        self.assertEqual(job.attempts, 1)

    async def test_abandoned_job_is_requeued(self) -> None:
        job_id = await self._create_job()
        await self._mark_processing(AnalysisJob, job_id, age=600)
        queue = FakeQueue()

        recovered = await recover_stuck_analysis_jobs(
            session_factory=self.session_factory, queue=queue
        )

        self.assertEqual(recovered, [job_id])
        self.assertEqual(queue.analysis, [str(job_id)])
        job = await self._job(job_id)
        self.assertEqual(job.status, "queued")
        # The attempt already spent is not refunded, so a crashing worker
        # cannot retry forever.
        self.assertEqual(job.attempts, 1)

    async def test_recovery_leaves_live_and_finished_jobs_alone(self) -> None:
        fresh = await self._create_job()
        await self._mark_processing(AnalysisJob, fresh, age=5)
        done = await self._create_job()
        async with self.session_factory() as session:
            await session.execute(
                update(AnalysisJob)
                .where(AnalysisJob.id == done)
                .values(status="completed")
            )
            await session.commit()
        queue = FakeQueue()

        recovered = await recover_stuck_analysis_jobs(
            session_factory=self.session_factory, queue=queue
        )

        self.assertEqual(recovered, [])
        self.assertEqual(queue.analysis, [])
        self.assertEqual((await self._job(fresh)).status, "processing")

    async def test_claiming_refreshes_the_lease(self) -> None:
        # An old row is only safe to process because claiming rewrites
        # updated_at; without that, a long task would look abandoned.
        job_id = await self._create_job()
        await self._age(AnalysisJob, job_id, seconds=600)

        self.assertTrue(await _claim_task(self.session_factory, AnalysisJob, job_id))
        self.assertEqual(
            await recover_stuck_analysis_jobs(
                session_factory=self.session_factory, queue=FakeQueue()
            ),
            [],
        )

    def test_backoff_grows_then_is_capped(self) -> None:
        self.assertEqual(backoff_delay(1, base=2.0, maximum=30.0), 2.0)
        self.assertEqual(backoff_delay(2, base=2.0, maximum=30.0), 4.0)
        self.assertEqual(backoff_delay(3, base=2.0, maximum=30.0), 8.0)
        self.assertEqual(backoff_delay(10, base=2.0, maximum=30.0), 30.0)


class KnowledgeReliabilityTests(TaskTestBase):
    async def _document(self, document_id: UUID) -> KnowledgeDocument:
        async with self.session_factory() as session:
            document = await session.get(KnowledgeDocument, document_id)
            self.assertIsNotNone(document)
            return document

    async def _chunks(self, document_id: UUID) -> list[DocumentChunk]:
        async with self.session_factory() as session:
            return list(
                (
                    await session.scalars(
                        select(DocumentChunk).where(
                            DocumentChunk.document_id == document_id
                        )
                    )
                ).all()
            )

    async def test_duplicate_ingestion_does_not_duplicate_chunks(self) -> None:
        document_id = await self._create_document()
        calls: list[int] = []

        class FakeEmbeddings:
            def embed(self, texts: list[str]) -> list[list[float]]:
                calls.append(len(texts))
                return [[0.1] * 1536 for _ in texts]

        for _ in range(2):
            await process_knowledge_document(
                document_id,
                session_factory=self.session_factory,
                storage_factory=lambda: MemoryStorage(
                    b"Embeddings represent semantic meaning."
                ),
                embedding_factory=FakeEmbeddings,
            )

        document = await self._document(document_id)
        self.assertEqual(calls, [1])
        self.assertEqual(document.status, "ready")
        self.assertEqual(document.chunk_count, 1)
        self.assertEqual(len(await self._chunks(document_id)), 1)

    async def test_transient_embedding_failure_is_retried(self) -> None:
        document_id = await self._create_document()
        calls: list[int] = []
        delays, sleeper = self._recorder()

        class FlakyEmbeddings:
            def embed(self, texts: list[str]) -> list[list[float]]:
                calls.append(len(texts))
                if len(calls) == 1:
                    raise RuntimeError("embedding quota exceeded")
                return [[0.1] * 1536 for _ in texts]

        await process_knowledge_document(
            document_id,
            session_factory=self.session_factory,
            storage_factory=lambda: MemoryStorage(
                b"Embeddings represent semantic meaning."
            ),
            embedding_factory=FlakyEmbeddings,
            sleeper=sleeper,
        )

        document = await self._document(document_id)
        self.assertEqual(len(calls), 2)
        self.assertEqual(document.status, "ready")
        self.assertEqual(document.attempts, 2)
        self.assertEqual(delays, [backoff_delay(1)])

    async def test_failed_ingestion_leaves_no_chunks(self) -> None:
        document_id = await self._create_document(max_attempts=2)
        delays, sleeper = self._recorder()

        class BrokenEmbeddings:
            def embed(self, texts: list[str]) -> list[list[float]]:
                raise RuntimeError("embedding quota exceeded")

        await process_knowledge_document(
            document_id,
            session_factory=self.session_factory,
            storage_factory=lambda: MemoryStorage(
                b"Embeddings represent semantic meaning."
            ),
            embedding_factory=BrokenEmbeddings,
            sleeper=sleeper,
        )

        document = await self._document(document_id)
        self.assertEqual(document.status, "failed")
        self.assertEqual(document.attempts, 2)
        self.assertEqual(document.chunk_count, 0)
        self.assertEqual(await self._chunks(document_id), [])
        self.assertIn("RuntimeError", document.error_message or "")

    async def test_unparseable_document_is_not_retried(self) -> None:
        document_id = await self._create_document()
        delays, sleeper = self._recorder()

        class NeverUsed:
            def embed(self, texts: list[str]) -> list[list[float]]:
                raise AssertionError("embedding should not be called")

        await process_knowledge_document(
            document_id,
            session_factory=self.session_factory,
            storage_factory=lambda: MemoryStorage(b"\xff\xfe"),
            embedding_factory=NeverUsed,
            sleeper=sleeper,
        )

        document = await self._document(document_id)
        self.assertEqual(delays, [])
        self.assertEqual(document.status, "failed")
        self.assertEqual(document.attempts, 1)

    async def test_abandoned_document_is_requeued(self) -> None:
        document_id = await self._create_document()
        await self._mark_processing(KnowledgeDocument, document_id, age=600)
        queue = FakeQueue()

        recovered = await recover_stuck_documents(
            session_factory=self.session_factory, queue=queue
        )

        self.assertEqual(recovered, [document_id])
        self.assertEqual(queue.documents, [str(document_id)])
        self.assertEqual((await self._document(document_id)).status, "queued")


if __name__ == "__main__":
    unittest.main()
