import asyncio
import logging
import socket
from datetime import datetime, timezone
from typing import Any, Callable
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.app.db.session import SessionFactory, engine
from backend.app.infrastructure.queue import RedisStreamQueue
from backend.app.models.analysis import AnalysisJob
from backend.app.models.knowledge_base import DocumentChunk, KnowledgeDocument
from backend.app.models.resume import Resume
from backend.app.ai.document_processing import chunk_document, extract_document_pieces
from backend.app.ai.embeddings import EmbeddingService
from backend.app.ai.model_providers import active_provider_credentials
from services.resume_parser import parse_resume_file
from services.storage import create_storage

logger = logging.getLogger(__name__)


async def process_analysis_job(
    job_id: UUID,
    *,
    session_factory: async_sessionmaker[AsyncSession] = SessionFactory,
    storage_factory: Callable = create_storage,
    analyzer: Callable[[str, str], dict[str, Any]] | None = None,
) -> None:
    async with session_factory() as session:
        job = await session.scalar(
            select(AnalysisJob).where(AnalysisJob.id == job_id)
        )
        if job is None or job.status == "completed":
            return
        resume = await session.get(Resume, job.resume_id)
        if resume is None:
            job.status = "failed"
            job.error_message = "The uploaded resume record could not be found."
            job.completed_at = datetime.now(timezone.utc)
            await session.commit()
            return

        job.status = "processing"
        job.error_message = None
        job.attempts += 1
        await session.commit()
        object_key = resume.object_key
        original_filename = resume.original_filename
        job_description = job.job_description

    try:
        storage = storage_factory()
        file_bytes = await asyncio.to_thread(storage.read, object_key)
        resume_text = await asyncio.to_thread(
            parse_resume_file, original_filename, file_bytes
        )
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
        logger.error("Analysis job %s failed (%s)", job_id, type(error).__name__)
        async with session_factory() as session:
            job = await session.scalar(
                select(AnalysisJob).where(AnalysisJob.id == job_id)
            )
            if job is not None:
                job.status = "failed"
                job.error_message = (
                    "Resume analysis failed. Check worker logs and retry the job."
                )
                job.completed_at = datetime.now(timezone.utc)
                await session.commit()
        return

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


async def process_knowledge_document(
    document_id: UUID,
    *,
    session_factory: async_sessionmaker[AsyncSession] = SessionFactory,
    storage_factory: Callable = create_storage,
    embedding_factory: Callable = EmbeddingService.from_settings,
) -> None:
    async with session_factory() as session:
        document = await session.scalar(
            select(KnowledgeDocument).where(KnowledgeDocument.id == document_id)
        )
        if document is None or document.status == "ready":
            return
        document.status = "processing"
        document.error_message = None
        await session.commit()
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

        embedding_service = embedding_factory()
        vectors = await asyncio.to_thread(
            embedding_service.embed, [content for content, _ in chunks]
        )
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
    except Exception:
        logger.exception("Knowledge document %s ingestion failed", document_id)
        async with session_factory() as session:
            document = await session.scalar(
                select(KnowledgeDocument).where(
                    KnowledgeDocument.id == document_id
                )
            )
            if document is not None:
                document.status = "failed"
                document.error_message = (
                    "Document ingestion failed. Check worker logs and retry the document."
                )
                await session.commit()


async def run_worker() -> None:
    queue = RedisStreamQueue.from_settings()
    consumer_name = f"{socket.gethostname()}-{uuid4().hex[:8]}"
    try:
        await queue.ensure_consumer_group()
        await queue.ensure_knowledge_consumer_group()
        logger.info("Analysis worker %s started", consumer_name)
        while True:
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
