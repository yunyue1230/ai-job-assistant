import unittest
from collections.abc import AsyncIterator
from uuid import UUID

from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.app.ai.interview_engine import (
    AnswerEvaluation,
    InterviewReport,
    OpenAIInterviewEngine,
)
from backend.app.api.deps import get_session
from backend.app.api.routes.interviews import get_optional_embeddings
from backend.app.db.base import Base
from backend.app.main import app
from backend.app.models.interview import InterviewSession, InterviewTurn


class FakeInterviewEngine:
    def generate_question(
        self,
        *,
        role: str,
        job_description: str,
        resume_text: str,
        difficulty: str,
        history: list[dict[str, str]],
        knowledge_context: str,
    ) -> str:
        return f"Question {len(history) + 1} for {role}?"

    def evaluate_answer(
        self,
        *,
        role: str,
        job_description: str,
        question: str,
        answer: str,
        history: list[dict[str, str]],
        knowledge_context: str,
    ) -> AnswerEvaluation:
        return AnswerEvaluation(
            score=80,
            strengths=["Clear explanation"],
            improvements=["Add measurable impact"],
            follow_up_question=(
                "Can you explain the trade-off?" if not history else None
            ),
        )

    def create_report(self, history: list[dict]) -> InterviewReport:
        return InterviewReport(
            overall_score=82,
            summary="Good technical interview.",
            strengths=["Clear communication"],
            improvement_areas=["Provide more evidence"],
            recommendations=["Use the STAR method"],
        )


class InterviewApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)
        self.interview_engine = FakeInterviewEngine()

        async def override_get_session() -> AsyncIterator:
            async with self.session_factory() as session:
                yield session

        app.dependency_overrides[get_session] = override_get_session
        app.dependency_overrides[
            OpenAIInterviewEngine.from_settings
        ] = lambda: self.interview_engine
        app.dependency_overrides[get_optional_embeddings] = lambda: None
        self.client = AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        )
        registration = await self.client.post(
            "/api/v1/auth/register",
            json={
                "email": "interview-owner@example.com",
                "password": "a-long-test-password",
            },
        )
        self.assertEqual(registration.status_code, 201)
        token = await self.client.post(
            "/api/v1/auth/token",
            data={
                "username": "interview-owner@example.com",
                "password": "a-long-test-password",
            },
        )
        self.headers = {"Authorization": f"Bearer {token.json()['access_token']}"}

    async def asyncTearDown(self) -> None:
        await self.client.aclose()
        app.dependency_overrides.pop(get_session, None)
        app.dependency_overrides.pop(OpenAIInterviewEngine.from_settings, None)
        app.dependency_overrides.pop(get_optional_embeddings, None)
        await self.engine.dispose()

    async def _create_interview(self, max_questions: int = 2) -> dict:
        response = await self.client.post(
            "/api/v1/interviews",
            headers=self.headers,
            json={
                "role": "AI Engineer",
                "job_description": "Build reliable AI products.",
                "resume_text": "Built an LLM application.",
                "difficulty": "intermediate",
                "max_questions": max_questions,
            },
        )
        self.assertEqual(response.status_code, 201)
        return response.json()

    async def test_follow_up_answer_completion_and_report(self) -> None:
        interview = await self._create_interview()
        self.assertEqual(interview["status"], "active")
        self.assertEqual(interview["current_question"], "Question 1 for AI Engineer?")

        first_answer = await self.client.post(
            f"/api/v1/interviews/{interview['id']}/answers",
            headers=self.headers,
            json={"answer": "I built an LLM application."},
        )
        self.assertEqual(first_answer.status_code, 200)
        self.assertEqual(first_answer.json()["turns"][0]["evaluation"]["score"], 80)
        self.assertEqual(
            first_answer.json()["current_question"],
            "Can you explain the trade-off?",
        )

        second_answer = await self.client.post(
            f"/api/v1/interviews/{interview['id']}/answers",
            headers=self.headers,
            json={"answer": "I compared latency and response quality."},
        )
        self.assertEqual(second_answer.status_code, 200)
        completed = second_answer.json()
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(completed["current_question"], None)
        self.assertEqual(completed["report"]["overall_score"], 82)
        self.assertEqual(len(completed["turns"]), 2)

        report = await self.client.get(
            f"/api/v1/interviews/{interview['id']}/report",
            headers=self.headers,
        )
        self.assertEqual(report.status_code, 200)
        self.assertTrue(report.content.startswith(b"%PDF-"))

        history = await self.client.get(
            "/api/v1/interviews",
            headers=self.headers,
        )
        self.assertEqual(history.status_code, 200)
        self.assertEqual(len(history.json()), 1)

    async def test_finish_early_and_reject_additional_answers(self) -> None:
        interview = await self._create_interview(max_questions=5)
        answer = await self.client.post(
            f"/api/v1/interviews/{interview['id']}/answers",
            headers=self.headers,
            json={"answer": "I improved a model."},
        )
        self.assertEqual(answer.status_code, 200)

        finished = await self.client.post(
            f"/api/v1/interviews/{interview['id']}/finish",
            headers=self.headers,
        )
        self.assertEqual(finished.status_code, 200)
        self.assertEqual(finished.json()["status"], "completed")

        extra_answer = await self.client.post(
            f"/api/v1/interviews/{interview['id']}/answers",
            headers=self.headers,
            json={"answer": "Additional response"},
        )
        self.assertEqual(extra_answer.status_code, 409)

    async def test_interview_is_private_to_its_owner(self) -> None:
        interview = await self._create_interview()
        await self.client.post(
            "/api/v1/auth/register",
            json={
                "email": "other-interview-owner@example.com",
                "password": "another-long-test-password",
            },
        )
        token = await self.client.post(
            "/api/v1/auth/token",
            data={
                "username": "other-interview-owner@example.com",
                "password": "another-long-test-password",
            },
        )
        other_headers = {"Authorization": f"Bearer {token.json()['access_token']}"}
        response = await self.client.get(
            f"/api/v1/interviews/{interview['id']}",
            headers=other_headers,
        )
        self.assertEqual(response.status_code, 404)

    async def test_empty_interview_cannot_be_finished(self) -> None:
        interview = await self._create_interview()
        response = await self.client.post(
            f"/api/v1/interviews/{interview['id']}/finish",
            headers=self.headers,
        )
        self.assertEqual(response.status_code, 409)


if __name__ == "__main__":
    unittest.main()
