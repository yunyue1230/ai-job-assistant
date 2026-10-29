import hashlib
import logging
from datetime import datetime
from pathlib import PurePosixPath
from typing import Annotated, Any
from uuid import UUID, uuid4

from botocore.exceptions import BotoCoreError, ClientError
from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    UploadFile,
    status,
)
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel
from fastapi.responses import Response
from redis.exceptions import RedisError
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.deps import CurrentUser, SessionDep
from backend.app.core.config import get_settings
from backend.app.infrastructure.queue import RedisStreamQueue, get_task_queue
from backend.app.infrastructure.pdf_report import build_analysis_report
from backend.app.models.analysis import AnalysisJob
from backend.app.models.resume import Resume
from services.storage import Storage, create_storage

logger = logging.getLogger(__name__)
router = APIRouter(tags=["resumes and analyses"])

ALLOWED_EXTENSIONS = {".txt", ".md", ".pdf"}
ALLOWED_CONTENT_TYPES = {
    ".txt": {"text/plain", "application/octet-stream"},
    ".md": {"text/markdown", "text/plain", "application/octet-stream"},
    ".pdf": {"application/pdf", "application/octet-stream"},
}


class ResumeRead(BaseModel):
    id: str
    original_filename: str
    content_type: str
    size_bytes: int
    created_at: datetime


class AnalysisJobRead(BaseModel):
    id: str
    resume_id: str
    resume_filename: str
    status: str
    result: dict[str, Any] | None
    error_message: str | None
    attempts: int
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None


def get_storage() -> Storage:
    return create_storage()


StorageDep = Annotated[Storage, Depends(get_storage)]
QueueDep = Annotated[RedisStreamQueue, Depends(get_task_queue)]


def _analysis_read(job: AnalysisJob, filename: str) -> AnalysisJobRead:
    return AnalysisJobRead(
        id=str(job.id),
        resume_id=str(job.resume_id),
        resume_filename=filename,
        status=job.status,
        result=job.result,
        error_message=job.error_message,
        attempts=job.attempts,
        created_at=job.created_at,
        updated_at=job.updated_at,
        completed_at=job.completed_at,
    )


async def _get_owned_job(
    session: AsyncSession, user_id: UUID, job_id: UUID
) -> tuple[AnalysisJob, str]:
    row = (
        await session.execute(
            select(AnalysisJob, Resume.original_filename)
            .join(Resume, AnalysisJob.resume_id == Resume.id)
            .where(AnalysisJob.id == job_id, AnalysisJob.user_id == user_id)
        )
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Analysis job not found.")
    return row[0], row[1]


async def _enqueue_or_mark_failed(
    job: AnalysisJob, session: AsyncSession, queue: RedisStreamQueue
) -> None:
    try:
        await queue.enqueue_analysis(str(job.id))
    except RedisError as error:
        logger.exception("Could not enqueue analysis job %s", job.id)
        job.status = "failed"
        job.error_message = "Analysis queue is unavailable. Please retry the job."
        await session.commit()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Analysis queue is unavailable. The upload was saved; retry the analysis later.",
        ) from error


@router.post(
    "/resumes",
    response_model=AnalysisJobRead,
    status_code=status.HTTP_202_ACCEPTED,
)
async def upload_resume(
    user: CurrentUser,
    session: SessionDep,
    storage: StorageDep,
    queue: QueueDep,
    file: Annotated[UploadFile, File()],
    job_description: Annotated[str, Form(min_length=1, max_length=50_000)],
) -> AnalysisJobRead:
    settings = get_settings()
    raw_filename = (file.filename or "").replace("\\", "/")
    filename = PurePosixPath(raw_filename).name
    extension = PurePosixPath(filename).suffix.lower()
    if not filename or extension not in ALLOWED_EXTENSIONS:
        allowed = ", ".join(sorted(ALLOWED_EXTENSIONS))
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported resume file. Upload one of: {allowed}.",
        )
    content_type = file.content_type or "application/octet-stream"
    if content_type not in ALLOWED_CONTENT_TYPES[extension]:
        raise HTTPException(status_code=415, detail="File content type is not supported.")

    content = await file.read(settings.upload_max_bytes + 1)
    if len(content) > settings.upload_max_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Resume exceeds the {settings.upload_max_bytes}-byte upload limit.",
        )
    if not content:
        raise HTTPException(status_code=422, detail="Uploaded resume is empty.")

    object_key = f"resumes/{user.id}/{uuid4().hex}{extension}"
    try:
        await run_in_threadpool(storage.save, object_key, content)
    except (OSError, BotoCoreError, ClientError) as error:
        logger.exception("Could not store resume for user %s", user.id)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Resume storage is unavailable.",
        ) from error

    resume = Resume(
        user_id=user.id,
        original_filename=filename,
        object_key=object_key,
        content_type=content_type,
        size_bytes=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
    )
    job = AnalysisJob(
        user_id=user.id,
        resume=resume,
        job_description=job_description.strip(),
        status="queued",
    )
    session.add(job)
    try:
        await session.commit()
        await session.refresh(job)
    except Exception:
        await session.rollback()
        try:
            await run_in_threadpool(storage.delete, object_key)
        except (OSError, BotoCoreError, ClientError):
            logger.exception("Could not clean up orphaned resume object %s", object_key)
        raise

    await _enqueue_or_mark_failed(job, session, queue)
    return _analysis_read(job, filename)


@router.get("/resumes", response_model=list[ResumeRead])
async def list_resumes(
    user: CurrentUser,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[ResumeRead]:
    resumes = (
        await session.scalars(
            select(Resume)
            .where(Resume.user_id == user.id)
            .order_by(desc(Resume.created_at))
            .limit(limit)
            .offset(offset)
        )
    ).all()
    return [
        ResumeRead(
            id=str(resume.id),
            original_filename=resume.original_filename,
            content_type=resume.content_type,
            size_bytes=resume.size_bytes,
            created_at=resume.created_at,
        )
        for resume in resumes
    ]


@router.get("/analyses", response_model=list[AnalysisJobRead])
async def list_analyses(
    user: CurrentUser,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[AnalysisJobRead]:
    rows = (
        await session.execute(
            select(AnalysisJob, Resume.original_filename)
            .join(Resume, AnalysisJob.resume_id == Resume.id)
            .where(AnalysisJob.user_id == user.id)
            .order_by(desc(AnalysisJob.created_at))
            .limit(limit)
            .offset(offset)
        )
    ).all()
    return [_analysis_read(job, filename) for job, filename in rows]


@router.get("/analyses/{job_id}", response_model=AnalysisJobRead)
async def get_analysis(
    job_id: UUID,
    user: CurrentUser,
    session: SessionDep,
) -> AnalysisJobRead:
    job, filename = await _get_owned_job(session, user.id, job_id)
    return _analysis_read(job, filename)


@router.get("/analyses/{job_id}/report")
async def download_analysis_report(
    job_id: UUID,
    user: CurrentUser,
    session: SessionDep,
) -> Response:
    job, filename = await _get_owned_job(session, user.id, job_id)
    if job.status != "completed" or job.result is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The analysis report is available after the job completes.",
        )
    pdf = await run_in_threadpool(build_analysis_report, filename, job.result)
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="analysis-{job.id}.pdf"'
        },
    )


@router.post(
    "/analyses/{job_id}/retry",
    response_model=AnalysisJobRead,
    status_code=status.HTTP_202_ACCEPTED,
)
async def retry_analysis(
    job_id: UUID,
    user: CurrentUser,
    session: SessionDep,
    queue: QueueDep,
) -> AnalysisJobRead:
    job, filename = await _get_owned_job(session, user.id, job_id)
    if job.status != "failed":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Only failed analysis jobs can be retried.",
        )
    job.status = "queued"
    job.error_message = None
    job.completed_at = None
    await session.commit()
    await _enqueue_or_mark_failed(job, session, queue)
    return _analysis_read(job, filename)
