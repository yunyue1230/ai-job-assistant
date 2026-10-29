import unittest
from collections.abc import AsyncIterator

from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.app.api.deps import get_session
from backend.app.db.base import Base
from backend.app.main import app


class AuthApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

        session_factory = async_sessionmaker(self.engine, expire_on_commit=False)

        async def override_get_session() -> AsyncIterator:
            async with session_factory() as session:
                yield session

        app.dependency_overrides[get_session] = override_get_session
        self.client = AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        )

    async def asyncTearDown(self) -> None:
        await self.client.aclose()
        app.dependency_overrides.pop(get_session, None)
        await self.engine.dispose()

    async def test_register_login_and_read_current_user(self) -> None:
        registration = await self.client.post(
            "/api/v1/auth/register",
            json={"email": "candidate@example.com", "password": "a-long-test-password"},
        )
        self.assertEqual(registration.status_code, 201)
        user = registration.json()
        self.assertEqual(user["email"], "candidate@example.com")
        self.assertNotIn("hashed_password", user)

        login = await self.client.post(
            "/api/v1/auth/token",
            data={"username": "candidate@example.com", "password": "a-long-test-password"},
        )
        self.assertEqual(login.status_code, 200)
        token = login.json()["access_token"]

        current_user = await self.client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {token}"},
        )
        self.assertEqual(current_user.status_code, 200)
        self.assertEqual(current_user.json()["id"], user["id"])

    async def test_rejects_duplicate_email_and_invalid_password(self) -> None:
        payload = {"email": "candidate@example.com", "password": "a-long-test-password"}
        self.assertEqual(
            (await self.client.post("/api/v1/auth/register", json=payload)).status_code,
            201,
        )
        self.assertEqual(
            (await self.client.post("/api/v1/auth/register", json=payload)).status_code,
            409,
        )
        login = await self.client.post(
            "/api/v1/auth/token",
            data={"username": payload["email"], "password": "wrong-password"},
        )
        self.assertEqual(login.status_code, 401)


if __name__ == "__main__":
    unittest.main()
