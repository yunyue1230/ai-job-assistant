import unittest
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone

from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.app.api.deps import get_session
from backend.app.db.base import Base
from backend.app.main import app


class ScheduleApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)

        async def override_get_session() -> AsyncIterator:
            async with self.session_factory() as session:
                yield session

        app.dependency_overrides[get_session] = override_get_session
        self.client = AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        )
        await self.client.post(
            "/api/v1/auth/register",
            json={
                "email": "schedule-owner@example.com",
                "password": "a-long-test-password",
            },
        )
        token = await self.client.post(
            "/api/v1/auth/token",
            data={
                "username": "schedule-owner@example.com",
                "password": "a-long-test-password",
            },
        )
        self.headers = {"Authorization": f"Bearer {token.json()['access_token']}"}

    async def asyncTearDown(self) -> None:
        await self.client.aclose()
        app.dependency_overrides.pop(get_session, None)
        await self.engine.dispose()

    async def test_schedule_update_export_and_cancel(self) -> None:
        starts_at = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        response = await self.client.post(
            "/api/v1/schedule/events",
            headers=self.headers,
            json={
                "title": "Technical interview",
                "company": "Example Co",
                "role": "ML Engineer",
                "starts_at": starts_at,
                "timezone_name": "Asia/Shanghai",
                "meeting_url": "https://example.test/meeting",
                "reminder_minutes": 60,
            },
        )
        self.assertEqual(response.status_code, 201)
        event_id = response.json()["id"]

        exported = await self.client.get(
            "/api/v1/schedule/events.ics",
            headers=self.headers,
        )
        self.assertEqual(exported.status_code, 200)
        self.assertIn("BEGIN:VCALENDAR", exported.text)
        self.assertIn("Technical interview", exported.text)

        updated = await self.client.patch(
            f"/api/v1/schedule/events/{event_id}",
            headers=self.headers,
            json={"reminder_minutes": 15},
        )
        self.assertEqual(updated.status_code, 200)
        self.assertFalse(updated.json()["reminder_sent"])

        cancelled = await self.client.delete(
            f"/api/v1/schedule/events/{event_id}",
            headers=self.headers,
        )
        self.assertEqual(cancelled.status_code, 204)
        events = await self.client.get(
            "/api/v1/schedule/events",
            headers=self.headers,
        )
        self.assertEqual(events.json(), [])

    async def test_schedule_rejects_naive_datetime(self) -> None:
        response = await self.client.post(
            "/api/v1/schedule/events",
            headers=self.headers,
            json={
                "title": "Interview",
                "starts_at": "2026-11-01T10:00:00",
            },
        )
        self.assertEqual(response.status_code, 422)

    async def test_schedule_rejects_invalid_timezone(self) -> None:
        response = await self.client.post(
            "/api/v1/schedule/events",
            headers=self.headers,
            json={
                "title": "Interview",
                "starts_at": "2026-11-01T10:00:00+08:00",
                "timezone_name": "Not/A_Timezone",
            },
        )
        self.assertEqual(response.status_code, 422)


if __name__ == "__main__":
    unittest.main()
