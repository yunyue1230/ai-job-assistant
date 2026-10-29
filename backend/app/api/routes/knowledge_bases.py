import hashlib
import logging
from datetime import datetime
from pathlib import PurePosixPath
from typing import Annotated
from urllib.parse import quote
from uuid import UUID, uuid4

from botocore.exceptions import BotoCoreError, ClientError
from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Query,
    UploadFile,
    status,
)
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response
from openai import OpenAIError
from pydantic import BaseModel, Field, field_validator
from redis.exceptions import RedisError
from sqlalchemy import desc, select

from backend.app.ai.embeddings import EmbeddingService, generate_rag_answer
from backend.app.api.deps import CurrentUser, SessionDep
from backend.app.api.routes.resumes import get_storage
from backend.app.core.config import get_settings
from backend.app.infrastructure.queue import RedisStreamQueue, get_task_queue
from backend.app.models.knowledge_base import (
    DocumentChunk,
    KnowledgeBase,
    KnowledgeDocument,
)
from services.storage import Storage

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/knowledge-bases", tags=["knowledge bases"])

ALLOWED_EXTENSIONS = {".txt", ".md", ".pdf", ".docx"}
ALLOWED_CONTENT_TYPES = {
    ".txt": {"text/plain", "application/octet-stream"},
    ".md": {"text/markdown", "text/plain", "application/octet-stream"},
    ".pdf": {"application/pdf", "application/octet-stream"},
    ".docx": {
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/octet-stream",
    },
}


class KnowledgeBaseCreate(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=1000)


class KnowledgeBaseRead(BaseModel):
    id: str
    name: str
    description: str | None
    created_at: datetime


class KnowledgeDocumentRead(BaseModel):
    id: str
    knowledge_base_id: str
    original_filename: str
    content_type: str
    size_bytes: int
    status: str
    error_message: str | None
    chunk_count: int
    created_at: datetime
    updated_at: datetime


class KnowledgeSource(BaseModel):
    label: str
    document_id: str
    filename: str
    page_number: int | None
    excerpt: str


class KnowledgeAnswer(BaseModel):
    answer: str
    sources: list[KnowledgeSource]


class KnowledgeQuestion(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    top_k: int | None = Field(default=None, ge=1, le=20)

    @field_validator("question")
    @classmethod
    def question_must_not_be_blank(cls, question: str) -> str:
        question = question.strip()
        if not question:
            raise ValueError("Question cannot be blank.")
        return question


EmbeddingDep = Annotated[EmbeddingService, Depends(EmbeddingService.from_settings)]
StorageDep = Annotated[Storage, Depends(get_storage)]
QueueDep = Annotated[RedisStreamQueue, Depends(get_task_queue)]


def _knowledge_base_read(knowledge_base: KnowledgeBase) -> KnowledgeBaseRead:
    return KnowledgeBaseRead(
        id=str(knowledge_base.id),
        name=knowledge_base.name,
        description=knowledge_base.description,
        created_at=knowledge_base.created_at,
    )


def _document_read(document: KnowledgeDocument) -> KnowledgeDocumentRead:
    return KnowledgeDocumentRead(
        id=str(document.id),
        knowledge_base_id=str(document.knowledge_base_id),
        original_filename=document.original_filename,
        content_type=document.content_type,
        size_bytes=document.size_bytes,
        status=document.status,
        error_message=document.error_message,
        chunk_count=document.chunk_count,
        created_at=document.created_at,
        updated_at=document.updated_at,
    )


async def _get_owned_knowledge_base(
    session: SessionDep, user_id: UUID, knowledge_base_id: UUID
) -> KnowledgeBase:
    knowledge_base = await session.scalar(
        select(KnowledgeBase).where(
            KnowledgeBase.id == knowledge_base_id,
            KnowledgeBase.user_id == user_id,
        )
    )
    if knowledge_base is None:
        raise HTTPException(status_code=404, detail="Knowledge base not found.")
    return knowledge_base


async def _get_owned_document(
    session: SessionDep, user_id: UUID, document_id: UUID
) -> KnowledgeDocument:
    document = await session.scalar(
        select(KnowledgeDocument)
        .join(
            KnowledgeBase,
            KnowledgeDocument.knowledge_base_id == KnowledgeBase.id,
        )
        .where(
            KnowledgeDocument.id == document_id,
            KnowledgeBase.user_id == user_id,
        )
    )
    if document is None:
        raise HTTPException(status_code=404, detail="Knowledge document not found.")
    return document


async def _enqueue_document(
    document: KnowledgeDocument, session: SessionDep, queue: RedisStreamQueue
) -> None:
    try:
        await queue.enqueue_document(str(document.id))
    except RedisError as error:
        logger.exception("Could not enqueue knowledge document %s", document.id)
        document.status = "failed"
        document.error_message = "Knowledge ingestion queue is unavailable. Retry later."
        await session.commit()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Knowledge ingestion queue is unavailable. The upload was saved; retry later.",
        ) from error


@router.post("", response_model=KnowledgeBaseRead, status_code=status.HTTP_201_CREATED)
async def create_knowledge_base(
    payload: KnowledgeBaseCreate,
    user: CurrentUser,
    session: SessionDep,
) -> KnowledgeBaseRead:
    knowledge_base = KnowledgeBase(
        user_id=user.id,
        name=payload.name.strip(),
        description=payload.description.strip() if payload.description else None,
    )
    if not knowledge_base.name:
        raise HTTPException(status_code=422, detail="Knowledge base name is required.")
    session.add(knowledge_base)
    await session.commit()
    await session.refresh(knowledge_base)
    return _knowledge_base_read(knowledge_base)


@router.get("", response_model=list[KnowledgeBaseRead])
async def list_knowledge_bases(user: CurrentUser, session: SessionDep) -> list[KnowledgeBaseRead]:
    knowledge_bases = (
        await session.scalars(
            select(KnowledgeBase)
            .where(KnowledgeBase.user_id == user.id)
            .order_by(desc(KnowledgeBase.created_at))
        )
    ).all()
    return [_knowledge_base_read(item) for item in knowledge_bases]


@router.post(
    "/{knowledge_base_id}/documents",
    response_model=KnowledgeDocumentRead,
    status_code=status.HTTP_202_ACCEPTED,
)
async def upload_knowledge_document(
    knowledge_base_id: UUID,
    user: CurrentUser,
    session: SessionDep,
    storage: StorageDep,
    queue: QueueDep,
    file: Annotated[UploadFile, File()],
) -> KnowledgeDocumentRead:
    knowledge_base = await _get_owned_knowledge_base(session, user.id, knowledge_base_id)
    filename = PurePosixPath((file.filename or "").replace("\\", "/")).name
    extension = PurePosixPath(filename).suffix.lower()
    if not filename or extension not in ALLOWED_EXTENSIONS:
        allowed = ", ".join(sorted(ALLOWED_EXTENSIONS))
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported knowledge document. Upload one of: {allowed}.",
        )
    content_type = file.content_type or "application/octet-stream"
    if content_type not in ALLOWED_CONTENT_TYPES[extension]:
        raise HTTPException(status_code=415, detail="File content type is not supported.")

    settings = get_settings()
    content = await file.read(settings.upload_max_bytes + 1)
    if len(content) > settings.upload_max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"Document exceeds the {settings.upload_max_bytes}-byte upload limit.",
        )
    if not content:
        raise HTTPException(status_code=422, detail="Uploaded document is empty.")

    object_key = f"knowledge/{knowledge_base.id}/{uuid4().hex}{extension}"
    try:
        await run_in_threadpool(storage.save, object_key, content)
    except (OSError, BotoCoreError, ClientError) as error:
        logger.exception("Could not store knowledge document for user %s", user.id)
        raise HTTPException(
            status_code=503, detail="Knowledge document storage is unavailable."
        ) from error

    document = KnowledgeDocument(
        knowledge_base_id=knowledge_base.id,
        original_filename=filename,
        object_key=object_key,
        content_type=content_type,
        size_bytes=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
        status="queued",
    )
    session.add(document)
    try:
        await session.commit()
        await session.refresh(document)
    except Exception:
        await session.rollback()
        try:
            await run_in_threadpool(storage.delete, object_key)
        except (OSError, BotoCoreError, ClientError):
            logger.exception("Could not clean up orphaned knowledge object %s", object_key)
        raise

    await _enqueue_document(document, session, queue)
    return _document_read(document)


@router.get(
    "/{knowledge_base_id}/documents",
    response_model=list[KnowledgeDocumentRead],
)
async def list_knowledge_documents(
    knowledge_base_id: UUID,
    user: CurrentUser,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[KnowledgeDocumentRead]:
    knowledge_base = await _get_owned_knowledge_base(session, user.id, knowledge_base_id)
    documents = (
        await session.scalars(
            select(KnowledgeDocument)
            .where(KnowledgeDocument.knowledge_base_id == knowledge_base.id)
            .order_by(desc(KnowledgeDocument.created_at))
            .limit(limit)
            .offset(offset)
        )
    ).all()
    return [_document_read(document) for document in documents]


@router.post(
    "/{knowledge_base_id}/documents/{document_id}/retry",
    response_model=KnowledgeDocumentRead,
    status_code=status.HTTP_202_ACCEPTED,
)
async def retry_knowledge_document(
    knowledge_base_id: UUID,
    document_id: UUID,
    user: CurrentUser,
    session: SessionDep,
    queue: QueueDep,
) -> KnowledgeDocumentRead:
    knowledge_base = await _get_owned_knowledge_base(session, user.id, knowledge_base_id)
    document = await _get_owned_document(session, user.id, document_id)
    if document.knowledge_base_id != knowledge_base.id:
        raise HTTPException(status_code=404, detail="Knowledge document not found.")
    if document.status != "failed":
        raise HTTPException(
            status_code=409,
            detail="Only failed knowledge document jobs can be retried.",
        )
    document.status = "queued"
    document.error_message = None
    await session.commit()
    await _enqueue_document(document, session, queue)
    return _document_read(document)


@router.post(
    "/{knowledge_base_id}/chat",
    response_model=KnowledgeAnswer,
)
async def ask_knowledge_base(
    knowledge_base_id: UUID,
    payload: KnowledgeQuestion,
    user: CurrentUser,
    session: SessionDep,
    embeddings: EmbeddingDep,
) -> KnowledgeAnswer:
    knowledge_base = await _get_owned_knowledge_base(session, user.id, knowledge_base_id)
    ready_count = await session.scalar(
        select(KnowledgeDocument.id)
        .where(
            KnowledgeDocument.knowledge_base_id == knowledge_base.id,
            KnowledgeDocument.status == "ready",
        )
        .limit(1)
    )
    if ready_count is None:
        raise HTTPException(
            status_code=409,
            detail="This knowledge base has no processed documents yet.",
        )

    try:
        query_embedding = (
            await run_in_threadpool(embeddings.embed, [payload.question.strip()])
        )[0]
        distance = DocumentChunk.embedding.cosine_distance(query_embedding)
        rows = (
            await session.execute(
                select(
                    DocumentChunk,
                    KnowledgeDocument.original_filename,
                )
                .join(
                    KnowledgeDocument,
                    DocumentChunk.document_id == KnowledgeDocument.id,
                )
                .where(
                    DocumentChunk.knowledge_base_id == knowledge_base.id,
                    KnowledgeDocument.status == "ready",
                )
                .order_by(distance)
                .limit(payload.top_k or get_settings().rag_top_k)
            )
        ).all()
        if not rows:
            raise HTTPException(
                status_code=404,
                detail="No relevant knowledge chunks were found.",
            )

        sources: list[KnowledgeSource] = []
        context_parts: list[str] = []
        for index, (chunk, filename) in enumerate(rows, start=1):
            label = f"S{index}"
            sources.append(
                KnowledgeSource(
                    label=label,
                    document_id=str(chunk.document_id),
                    filename=filename,
                    page_number=chunk.page_number,
                    excerpt=chunk.content[:500],
                )
            )
            location = (
                f", page {chunk.page_number}" if chunk.page_number is not None else ""
            )
            context_parts.append(
                f"[{label}] {filename}{location}: {chunk.content[:6000]}"
            )
        answer = await run_in_threadpool(
            generate_rag_answer,
            payload.question.strip(),
            "\n\n".join(context_parts),
        )
        return KnowledgeAnswer(answer=answer, sources=sources)
    except HTTPException:
        raise
    except (OpenAIError, RuntimeError, ValueError) as error:
        logger.warning(
            "Knowledge question failed for base %s (%s)",
            knowledge_base.id,
            type(error).__name__,
        )
        raise HTTPException(
            status_code=502,
            detail="The knowledge question could not be answered. Check model configuration and retry.",
        ) from error


@router.get("/{knowledge_base_id}/documents/{document_id}/download")
async def download_knowledge_document(
    knowledge_base_id: UUID,
    document_id: UUID,
    user: CurrentUser,
    session: SessionDep,
    storage: StorageDep,
) -> Response:
    knowledge_base = await _get_owned_knowledge_base(session, user.id, knowledge_base_id)
    document = await _get_owned_document(session, user.id, document_id)
    if document.knowledge_base_id != knowledge_base.id:
        raise HTTPException(status_code=404, detail="Knowledge document not found.")
    try:
        content = await run_in_threadpool(storage.read, document.object_key)
    except (OSError, BotoCoreError, ClientError) as error:
        logger.exception("Could not read knowledge document %s", document.id)
        raise HTTPException(
            status_code=503, detail="Knowledge document storage is unavailable."
        ) from error
    return Response(
        content=content,
        media_type=document.content_type,
        headers={
            "Content-Disposition": (
                f"attachment; filename*=UTF-8''{quote(document.original_filename)}"
            )
        },
    )
