import logging
from datetime import datetime, timezone
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response
from openai import OpenAIError
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select

from backend.app.ai.embeddings import (
    EmbeddingService,
    embedding_provider_is_configured,
)
from backend.app.ai.interview_engine import (
    AnswerEvaluation,
    InterviewEngine,
    InterviewReport,
    OpenAIInterviewEngine,
)
from backend.app.api.deps import CurrentUser, SessionDep
from backend.app.infrastructure.pdf_report import build_analysis_report
from backend.app.models.interview import InterviewSession, InterviewTurn
from backend.app.models.knowledge_base import (
    DocumentChunk,
    KnowledgeBase,
    KnowledgeDocument,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/interviews", tags=["mock interviews"])


class InterviewCreate(BaseModel):
    role: str = Field(min_length=1, max_length=200)
    job_description: str = Field(min_length=1, max_length=30_000)
    resume_text: str = Field(min_length=1, max_length=30_000)
    difficulty: str = "intermediate"
    max_questions: int = Field(default=5, ge=1, le=20)
    knowledge_base_id: UUID | None = None

    @field_validator("role", "job_description", "resume_text")
    @classmethod
    def fields_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("This field cannot be blank.")
        return value

    @field_validator("difficulty")
    @classmethod
    def validate_difficulty(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in {"beginner", "intermediate", "advanced"}:
            raise ValueError("Difficulty must be beginner, intermediate, or advanced.")
        return normalized


class InterviewAnswerCreate(BaseModel):
    answer: str = Field(min_length=1, max_length=20_000)

    @field_validator("answer")
    @classmethod
    def answer_must_not_be_blank(cls, answer: str) -> str:
        answer = answer.strip()
        if not answer:
            raise ValueError("Answer cannot be blank.")
        return answer


class InterviewTurnRead(BaseModel):
    sequence: int
    question: str
    answer: str
    evaluation: dict[str, Any]
    created_at: datetime


class InterviewRead(BaseModel):
    id: str
    role: str
    difficulty: str
    max_questions: int
    status: str
    current_question: str | None
    question_number: int
    turns: list[InterviewTurnRead]
    report: dict[str, Any] | None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None


def get_optional_embeddings() -> EmbeddingService | None:
    if not embedding_provider_is_configured():
        return None
    return EmbeddingService.from_settings()


EngineDep = Annotated[InterviewEngine, Depends(OpenAIInterviewEngine.from_settings)]
EmbeddingDep = Annotated[EmbeddingService | None, Depends(get_optional_embeddings)]


async def _owned_session(
    session: SessionDep, user_id: UUID, interview_id: UUID
) -> InterviewSession:
    interview = await session.scalar(
        select(InterviewSession).where(
            InterviewSession.id == interview_id,
            InterviewSession.user_id == user_id,
        )
    )
    if interview is None:
        raise HTTPException(status_code=404, detail="Interview session not found.")
    return interview


async def _history(session: SessionDep, interview_id: UUID) -> list[InterviewTurn]:
    return (
        await session.scalars(
            select(InterviewTurn)
            .where(InterviewTurn.interview_id == interview_id)
            .order_by(InterviewTurn.sequence)
        )
    ).all()


def _read(interview: InterviewSession, turns: list[InterviewTurn]) -> InterviewRead:
    return InterviewRead(
        id=str(interview.id),
        role=interview.role,
        difficulty=interview.difficulty,
        max_questions=interview.max_questions,
        status=interview.status,
        current_question=interview.current_question,
        question_number=len(turns) + (1 if interview.status == "active" else 0),
        turns=[
            InterviewTurnRead(
                sequence=turn.sequence,
                question=turn.question,
                answer=turn.answer,
                evaluation=turn.evaluation,
                created_at=turn.created_at,
            )
            for turn in turns
        ],
        report=interview.report,
        created_at=interview.created_at,
        updated_at=interview.updated_at,
        completed_at=interview.completed_at,
    )


async def _knowledge_context(
    session: SessionDep,
    user_id: UUID,
    knowledge_base_id: UUID | None,
    query: str,
    embeddings: EmbeddingService | None,
) -> str:
    if knowledge_base_id is None:
        return ""
    knowledge_base = await session.scalar(
        select(KnowledgeBase).where(
            KnowledgeBase.id == knowledge_base_id,
            KnowledgeBase.user_id == user_id,
        )
    )
    if knowledge_base is None:
        raise HTTPException(status_code=404, detail="Knowledge base not found.")
    if embeddings is None:
        raise HTTPException(
            status_code=503,
            detail="An embedding provider is required for knowledge-grounded interviews.",
        )
    vector = (await run_in_threadpool(embeddings.embed, [query]))[0]
    distance = DocumentChunk.embedding.cosine_distance(vector)
    rows = (
        await session.execute(
            select(DocumentChunk.content, KnowledgeDocument.original_filename)
            .join(
                KnowledgeDocument,
                DocumentChunk.document_id == KnowledgeDocument.id,
            )
            .where(
                DocumentChunk.knowledge_base_id == knowledge_base.id,
                KnowledgeDocument.status == "ready",
            )
            .order_by(distance)
            .limit(3)
        )
    ).all()
    return "\n\n".join(
        f"[{filename}] {content[:4000]}" for content, filename in rows
    )


def _transcript(turns: list[InterviewTurn]) -> list[dict[str, Any]]:
    return [
        {
            "sequence": turn.sequence,
            "question": turn.question,
            "answer": turn.answer,
            "evaluation": turn.evaluation,
        }
        for turn in turns
    ]


async def _evaluate(
    engine: InterviewEngine,
    interview: InterviewSession,
    turns: list[InterviewTurn],
) -> InterviewReport:
    return await run_in_threadpool(engine.create_report, _transcript(turns))


def _report_dict(report: InterviewReport) -> dict[str, Any]:
    return report.model_dump()


@router.post("", response_model=InterviewRead, status_code=status.HTTP_201_CREATED)
async def create_interview(
    payload: InterviewCreate,
    user: CurrentUser,
    session: SessionDep,
    engine: EngineDep,
    embeddings: EmbeddingDep,
) -> InterviewRead:
    knowledge_context = await _knowledge_context(
        session,
        user.id,
        payload.knowledge_base_id,
        f"{payload.role}\n{payload.job_description}",
        embeddings,
    )
    try:
        question = await run_in_threadpool(
            engine.generate_question,
            role=payload.role,
            job_description=payload.job_description,
            resume_text=payload.resume_text,
            difficulty=payload.difficulty,
            history=[],
            knowledge_context=knowledge_context,
        )
    except (OpenAIError, RuntimeError, ValueError) as error:
        logger.warning(
            "Could not start interview for user %s (%s)",
            user.id,
            type(error).__name__,
        )
        raise HTTPException(
            status_code=502, detail="Could not generate the first interview question."
        ) from error

    interview = InterviewSession(
        user_id=user.id,
        knowledge_base_id=payload.knowledge_base_id,
        role=payload.role,
        job_description=payload.job_description,
        resume_text=payload.resume_text,
        difficulty=payload.difficulty,
        max_questions=payload.max_questions,
        current_question=question,
        status="active",
    )
    session.add(interview)
    await session.commit()
    await session.refresh(interview)
    return _read(interview, [])


@router.get("", response_model=list[InterviewRead])
async def list_interviews(
    user: CurrentUser,
    session: SessionDep,
) -> list[InterviewRead]:
    interviews = (
        await session.scalars(
            select(InterviewSession)
            .where(InterviewSession.user_id == user.id)
            .order_by(InterviewSession.created_at.desc())
        )
    ).all()
    output: list[InterviewRead] = []
    for interview in interviews:
        output.append(_read(interview, await _history(session, interview.id)))
    return output


@router.get("/{interview_id}", response_model=InterviewRead)
async def get_interview(
    interview_id: UUID,
    user: CurrentUser,
    session: SessionDep,
) -> InterviewRead:
    interview = await _owned_session(session, user.id, interview_id)
    return _read(interview, await _history(session, interview.id))


@router.post("/{interview_id}/answers", response_model=InterviewRead)
async def answer_interview_question(
    interview_id: UUID,
    payload: InterviewAnswerCreate,
    user: CurrentUser,
    session: SessionDep,
    engine: EngineDep,
    embeddings: EmbeddingDep,
) -> InterviewRead:
    interview = await _owned_session(session, user.id, interview_id)
    if interview.status != "active" or not interview.current_question:
        raise HTTPException(status_code=409, detail="Interview session is not active.")

    turns = await _history(session, interview.id)
    history = [
        {"question": turn.question, "answer": turn.answer} for turn in turns
    ]
    current_question = interview.current_question
    knowledge_context = await _knowledge_context(
        session,
        user.id,
        interview.knowledge_base_id,
        current_question,
        embeddings,
    )
    try:
        evaluation = await run_in_threadpool(
            engine.evaluate_answer,
            role=interview.role,
            job_description=interview.job_description,
            question=current_question,
            answer=payload.answer,
            history=history,
            knowledge_context=knowledge_context,
        )
        turn = InterviewTurn(
            interview_id=interview.id,
            sequence=len(turns) + 1,
            question=current_question,
            answer=payload.answer,
            evaluation=evaluation.model_dump(),
        )
        session.add(turn)
        turns.append(turn)
        if len(turns) >= interview.max_questions:
            report = await _evaluate(engine, interview, turns)
            interview.report = _report_dict(report)
            interview.status = "completed"
            interview.current_question = None
            interview.completed_at = datetime.now(timezone.utc)
        else:
            follow_up = evaluation.follow_up_question
            if follow_up and follow_up.strip():
                interview.current_question = follow_up.strip()
            else:
                transcript = [
                    {"question": item.question, "answer": item.answer}
                    for item in turns
                ]
                interview.current_question = await run_in_threadpool(
                    engine.generate_question,
                    role=interview.role,
                    job_description=interview.job_description,
                    resume_text=interview.resume_text,
                    difficulty=interview.difficulty,
                    history=transcript,
                    knowledge_context=knowledge_context,
                )
        await session.commit()
        await session.refresh(interview)
        persisted_turns = await _history(session, interview.id)
        return _read(interview, persisted_turns)
    except HTTPException:
        await session.rollback()
        raise
    except (OpenAIError, RuntimeError, ValueError) as error:
        await session.rollback()
        logger.warning(
            "Could not process interview answer %s (%s)",
            interview.id,
            type(error).__name__,
        )
        raise HTTPException(
            status_code=502,
            detail="Could not evaluate the answer. Your turn was not saved; please retry.",
        ) from error


@router.post("/{interview_id}/finish", response_model=InterviewRead)
async def finish_interview(
    interview_id: UUID,
    user: CurrentUser,
    session: SessionDep,
    engine: EngineDep,
) -> InterviewRead:
    interview = await _owned_session(session, user.id, interview_id)
    if interview.status == "completed":
        return _read(interview, await _history(session, interview.id))
    turns = await _history(session, interview.id)
    if not turns:
        raise HTTPException(
            status_code=409,
            detail="Answer at least one question before finishing the interview.",
        )
    try:
        report = await _evaluate(engine, interview, turns)
    except (OpenAIError, RuntimeError, ValueError) as error:
        logger.warning(
            "Could not finish interview %s (%s)",
            interview.id,
            type(error).__name__,
        )
        raise HTTPException(
            status_code=502, detail="Could not generate the interview report."
        ) from error
    interview.report = _report_dict(report)
    interview.status = "completed"
    interview.current_question = None
    interview.completed_at = datetime.now(timezone.utc)
    await session.commit()
    await session.refresh(interview)
    return _read(interview, turns)


@router.get("/{interview_id}/report")
async def download_interview_report(
    interview_id: UUID,
    user: CurrentUser,
    session: SessionDep,
) -> Response:
    interview = await _owned_session(session, user.id, interview_id)
    if interview.status != "completed" or not interview.report:
        raise HTTPException(
            status_code=409,
            detail="The interview report is available after the interview is completed.",
        )
    turns = await _history(session, interview.id)
    report = {
        "match_score": interview.report.get("overall_score", 0),
        "strengths": interview.report.get("strengths", []),
        "gaps": interview.report.get("improvement_areas", []),
        "resume_improvement_tips": interview.report.get("recommendations", []),
        "interview_questions": [
            (
                f"Q{turn.sequence}: {turn.question}\n"
                f"Answer: {turn.answer}\n"
                f"Score: {turn.evaluation.get('score', 0)}/100"
            )
            for turn in turns
        ],
    }
    pdf = await run_in_threadpool(
        build_analysis_report, f"{interview.role} mock interview", report
    )
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="interview-{interview.id}.pdf"'
        },
    )
