"""Queue consumers for resume analysis and knowledge ingestion.

Three things keep the workers honest when a task is delivered twice, a provider
has a bad moment, or a worker dies mid-task:

- **claiming**: a task only leaves ``queued``/``failed`` through a single
  ``UPDATE ... WHERE status IN (...)``. A duplicate delivery or a second worker
  matches zero rows and stops, so the model or embedding call happens once.
- **bounded retry**: a transient failure retries inside the same task with an
  exponential backoff, up to the row's ``max_attempts``. Parsing the uploaded
  bytes is *not* retried: the same bytes fail the same way, so retrying only
  burns attempts.
- **lease recovery**: a task still ``processing`` long after its last write is
  treated as abandoned -- its worker probably died -- and is put back on the
  queue. ``attempts`` is not reset, so a crash cannot turn into an unbounded
  retry loop.
"""

import asyncio
import logging
import socket
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.app.ai.document_processing import chunk_document, extract_document_pieces
from backend.app.ai.embeddings import EmbeddingService
from backend.app.ai.model_providers import active_provider_credentials
from backend.app.core.config import get_settings
from backend.app.db.session import SessionFactory, engine
from backend.app.infrastructure.queue import RedisStreamQueue
from backend.app.models.analysis import AnalysisJob
from backend.app.models.knowledge_base import DocumentChunk, KnowledgeDocument
from backend.app.models.resume import Resume
from services.resume_parser import parse_resume_file
from services.storage import create_storage

logger = logging.getLogger(__name__)

# Only these statuses may be picked up. 'processing' and the terminal states are
# owned by whoever moved the row there, which is what makes claiming safe.
CLAIMABLE_STATUSES = ("queued", "failed")


def backoff_delay(
    attempt: int, *, base: float | None = None, maximum: float | None = None
) -> float:
    """Delay before attempt ``attempt`` (1-based): base, 2*base, 4*base, ..."""
    settings = get_settings()
    floor = base if base is not None else settings.worker_retry_base_delay_seconds
    ceiling = (
        maximum if maximum is not None else settings.worker_retry_max_delay_seconds
    )
    return min(ceiling, floor * (2 ** max(0, attempt - 1)))


def _as_utc(value: datetime) -> datetime:
    # SQLite hands back naive datetimes even for timezone-aware columns.
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


async def _claim_task(
    session_factory: async_sessionmaker[AsyncSession],
    model: type[AnalysisJob] | type[KnowledgeDocument],
    object_id: UUID,
) -> bool:
    """Take ownership of one task, or report that someone else already did.

    The status filter is the whole concurrency guard: two workers issuing this
    same statement cannot both match the row, so the loser stops instead of
    running the task a second time.
    """
    async with session_factory() as session:
        result = await session.execute(
            update(model)
            .where(model.id == object_id, model.status.in_(CLAIMABLE_STATUSES))
            .values(
                status="processing",
                attempts=model.attempts + 1,
                error_message=None,
            )
        )
        await session.commit()
        return result.rowcount == 1


async def _mark_failed(
    session_factory: async_sessionmaker[AsyncSession],
    model: type[AnalysisJob] | type[KnowledgeDocument],
    object_id: UUID,
    message: str,
) -> None:
    async with session_factory() as session:
        row = await session.get(model, object_id)
        if row is None:
            return
        row.status = "failed"
        row.error_message = message
        if hasattr(row, "completed_at"):
            row.completed_at = datetime.now(timezone.utc)
        await session.commit()


async def _register_retry_or_failure(
    session_factory: async_sessionmaker[AsyncSession],
    model: type[AnalysisJob] | type[KnowledgeDocument],
    object_id: UUID,
    error: BaseException,
    *,
    label: str,
) -> float | None:
    """Count a failed attempt and return the backoff, or None when exhausted.

    The row's ``attempts`` already includes the attempt that just failed, so a
    retry increments it for the attempt about to start. Exhausted tasks end up
    ``failed`` with the exception type in the message, which is what makes a
    permanent failure diagnosable from the API.
    """
    async with session_factory() as session:
        row = await session.get(model, object_id)
        if row is None:
            return None
        if row.attempts < row.max_attempts:
            row.attempts += 1
            await session.commit()
            logger.warning(
                "%s %s failed attempt %s/%s (%s); retrying",
                label,
                object_id,
                row.attempts - 1,
                row.max_attempts,
                type(error).__name__,
            )
            return backoff_delay(row.attempts - 1)
        row.status = "failed"
        row.error_message = (
            f"{label} failed after {row.attempts} attempts "
            f"(last error: {type(error).__name__})."
        )
        if hasattr(row, "completed_at"):
            row.completed_at = datetime.now(timezone.utc)
        await session.commit()
        return None


async def process_analysis_job(
    job_id: UUID,
    *,
    session_factory: async_sessionmaker[AsyncSession] = SessionFactory,
    storage_factory: Callable = create_storage,
    analyzer: Callable[[str, str], dict[str, Any]] | None = None,
    sleeper: Callable[[float], Any] = asyncio.sleep,
) -> None:
    if not await _claim_task(session_factory, AnalysisJob, job_id):
        logger.info(
            "Analysis job %s is already owned or finished; skipping duplicate", job_id
        )
        return

    async with session_factory() as session:
        job = await session.scalar(
            select(AnalysisJob).where(AnalysisJob.id == job_id)
        )
        resume = None if job is None else await session.get(Resume, job.resume_id)
        if job is None or resume is None:
            await _mark_failed(
                session_factory,
                AnalysisJob,
                job_id,
                "The uploaded resume record could not be found.",
            )
            return
        object_key = resume.object_key
        original_filename = resume.original_filename
        job_description = job.job_description

    # Parsing is deterministic: retrying the same bytes only spends attempts.
    try:
        storage = storage_factory()
        file_bytes = await asyncio.to_thread(storage.read, object_key)
        resume_text = await asyncio.to_thread(
            parse_resume_file, original_filename, file_bytes
        )
    except Exception as error:
        logger.exception("Analysis job %s could not read its resume", job_id)
        await _mark_failed(
            session_factory,
            AnalysisJob,
            job_id,
            f"Resume analysis failed before the model call "
            f"({type(error).__name__}).",
        )
        return

    while True:
        try:
            if analyzer is None:
                from services.job_match_service import analyze_job_match

                credentials = active_provider_credentials()
                result = await asyncio.to_thread(
                    analyze_job_match,
                    job_description,
                    resume_text,
                    api_key=credentials[0],
                    base_url=credentials[1],
                    model_name=credentials[2],
                )
            else:
                result = await asyncio.to_thread(analyzer, job_description, resume_text)
        except Exception as error:
            logger.warning(
                "Analysis job %s failed (%s)", job_id, type(error).__name__
            )
            delay = await _register_retry_or_failure(
                session_factory,
                AnalysisJob,
                job_id,
                error,
                label="Resume analysis",
            )
            if delay is None:
                return
            await sleeper(delay)
            continue

        async with session_factory() as session:
            job = await session.scalar(
                select(AnalysisJob).where(AnalysisJob.id == job_id)
            )
            if job is not None:
                job.status = "completed"
                job.result = result
                job.error_message = None
                job.completed_at = datetime.now(timezone.utc)
                await session.commit()
        return


async def _clear_document_chunks(
    session_factory: async_sessionmaker[AsyncSession], document_id: UUID
) -> None:
    """Drop chunks so a failed document never leaves half-indexed content."""
    async with session_factory() as session:
        await session.execute(
            DocumentChunk.__table__.delete().where(
                DocumentChunk.document_id == document_id
            )
        )
        document = await session.get(KnowledgeDocument, document_id)
        if document is not None:
            document.chunk_count = 0
        await session.commit()


async def process_knowledge_document(
    document_id: UUID,
    *,
    session_factory: async_sessionmaker[AsyncSession] = SessionFactory,
    storage_factory: Callable = create_storage,
    embedding_factory: Callable = EmbeddingService.from_settings,
    sleeper: Callable[[float], Any] = asyncio.sleep,
) -> None:
    if not await _claim_task(session_factory, KnowledgeDocument, document_id):
        logger.info(
            "Knowledge document %s is already owned or finished; skipping duplicate",
            document_id,
        )
        return

    async with session_factory() as session:
        document = await session.scalar(
            select(KnowledgeDocument).where(KnowledgeDocument.id == document_id)
        )
        if document is None:
            return
        object_key = document.object_key
        filename = document.original_filename

    try:
        storage = storage_factory()
        file_bytes = await asyncio.to_thread(storage.read, object_key)
        pieces = await asyncio.to_thread(
            extract_document_pieces, filename, file_bytes
        )
        chunks = chunk_document(pieces)
        if not chunks:
            raise ValueError("Document did not produce any searchable text chunks.")
    except Exception as error:
        logger.exception("Knowledge document %s could not be parsed", document_id)
        await _clear_document_chunks(session_factory, document_id)
        await _mark_failed(
            session_factory,
            KnowledgeDocument,
            document_id,
            f"Document ingestion failed before embedding "
            f"({type(error).__name__}).",
        )
        return

    embedding_service = embedding_factory()
    while True:
        try:
            vectors = await asyncio.to_thread(
                embedding_service.embed, [content for content, _ in chunks]
            )
        except Exception as error:
            logger.warning(
                "Knowledge document %s embedding failed (%s)",
                document_id,
                type(error).__name__,
            )
            delay = await _register_retry_or_failure(
                session_factory,
                KnowledgeDocument,
                document_id,
                error,
                label="Document ingestion",
            )
            if delay is None:
                await _clear_document_chunks(session_factory, document_id)
                return
            await sleeper(delay)
            continue

        async with session_factory() as session:
            document = await session.scalar(
                select(KnowledgeDocument).where(
                    KnowledgeDocument.id == document_id
                )
            )
            if document is None:
                return
            await session.execute(
                DocumentChunk.__table__.delete().where(
                    DocumentChunk.document_id == document_id
                )
            )
            session.add_all(
                [
                    DocumentChunk(
                        knowledge_base_id=document.knowledge_base_id,
                        document_id=document.id,
                        chunk_index=index,
                        page_number=page_number,
                        content=content,
                        embedding=vector,
                        metadata_json={"source": filename},
                    )
                    for index, ((content, page_number), vector) in enumerate(
                        zip(chunks, vectors, strict=True)
                    )
                ]
            )
            document.chunk_count = len(chunks)
            document.status = "ready"
            document.error_message = None
            await session.commit()
        return


async def _recover_stuck(
    model: type[AnalysisJob] | type[KnowledgeDocument],
    *,
    session_factory: async_sessionmaker[AsyncSession],
    enqueue: Callable[[RedisStreamQueue, UUID], Any],
    queue: RedisStreamQueue | None = None,
    now: datetime | None = None,
) -> list[UUID]:
    """Requeue tasks whose worker stopped writing without finishing them.

    A task is abandoned when it is still ``processing`` longer than the lease
    after its last write. Its attempt count survives, so a worker that keeps
    crashing cannot retry forever.
    """
    settings = get_settings()
    current = now or datetime.now(timezone.utc)
    cutoff = current - timedelta(seconds=settings.worker_lease_timeout_seconds)

    async with session_factory() as session:
        rows = (
            await session.scalars(select(model).where(model.status == "processing"))
        ).all()
        stuck = [row for row in rows if _as_utc(row.updated_at) < cutoff]
        for row in stuck:
            row.status = "queued"
            row.error_message = None
        await session.commit()
        stuck_ids = [row.id for row in stuck]

    if not stuck_ids:
        return []
    logger.warning(
        "Requeuing %s %s task(s) whose lease expired", len(stuck_ids), model.__name__
    )
    own_queue = queue is None
    active = queue if queue is not None else RedisStreamQueue.from_settings()
    try:
        for object_id in stuck_ids:
            await enqueue(active, object_id)
    finally:
        if own_queue:
            await active.close()
    return stuck_ids


async def recover_stuck_analysis_jobs(
    *,
    session_factory: async_sessionmaker[AsyncSession] = SessionFactory,
    queue: RedisStreamQueue | None = None,
    now: datetime | None = None,
) -> list[UUID]:
    return await _recover_stuck(
        AnalysisJob,
        session_factory=session_factory,
        enqueue=lambda active, job_id: active.enqueue_analysis(str(job_id)),
        queue=queue,
        now=now,
    )


async def recover_stuck_documents(
    *,
    session_factory: async_sessionmaker[AsyncSession] = SessionFactory,
    queue: RedisStreamQueue | None = None,
    now: datetime | None = None,
) -> list[UUID]:
    return await _recover_stuck(
        KnowledgeDocument,
        session_factory=session_factory,
        enqueue=lambda active, document_id: active.enqueue_document(str(document_id)),
        queue=queue,
        now=now,
    )


async def run_worker() -> None:
    settings = get_settings()
    queue = RedisStreamQueue.from_settings()
    consumer_name = f"{socket.gethostname()}-{uuid4().hex[:8]}"
    try:
        await queue.ensure_consumer_group()
        await queue.ensure_knowledge_consumer_group()
        logger.info("Analysis worker %s started", consumer_name)
        # Sweep once at start-up so tasks abandoned by a previous worker are
        # picked up without waiting a full interval.
        last_sweep = 0.0
        while True:
            if time.monotonic() - last_sweep >= settings.worker_recovery_sweep_seconds:
                last_sweep = time.monotonic()
                await recover_stuck_analysis_jobs(queue=queue)
                await recover_stuck_documents(queue=queue)

            messages = await queue.reclaim_pending(consumer_name)
            if not messages:
                messages = await queue.read_new(consumer_name)
            for message_id, fields in messages:
                try:
                    job_id = UUID(fields["job_id"])
                    await process_analysis_job(job_id)
                except (KeyError, ValueError):
                    logger.exception("Discarding malformed analysis task %s", message_id)
                except Exception:
                    logger.exception("Analysis task %s could not be processed", message_id)
                    continue
                await queue.acknowledge(message_id)

            document_messages = await queue.reclaim_pending_documents(consumer_name)
            if not document_messages:
                document_messages = await queue.read_new_documents(
                    consumer_name, block_ms=1000
                )
            for message_id, fields in document_messages:
                try:
                    document_id = UUID(fields["document_id"])
                    await process_knowledge_document(document_id)
                except (KeyError, ValueError):
                    logger.exception(
                        "Discarding malformed knowledge task %s", message_id
                    )
                except Exception:
                    logger.exception(
                        "Knowledge task %s could not be processed", message_id
                    )
                    continue
                await queue.acknowledge_document(message_id)
    finally:
        await queue.close()
        await engine.dispose()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_worker())
