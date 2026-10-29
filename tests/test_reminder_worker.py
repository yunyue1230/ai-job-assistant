import unittest
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.app.db.base import Base
from backend.app.models.schedule import InterviewEvent, UserNotification
from backend.app.models.user import User
from backend.app.workers.reminders import deliver_due_reminders


class ReminderWorkerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)
        self.now = datetime(2026, 10, 21, 12, tzinfo=timezone.utc)
        async with self.session_factory() as session:
            user = User(email="reminder@example.com", hashed_password="not-used")
            session.add(user)
            await session.flush()
            session.add_all(
                [
                    InterviewEvent(
                        user_id=user.id,
                        title="Due interview",
                        starts_at=self.now + timedelta(minutes=10),
                        reminder_minutes=30,
                        timezone_name="UTC",
                        reminder_sent=False,
                        status="scheduled",
                    ),
                    InterviewEvent(
                        user_id=user.id,
                        title="Not due yet",
                        starts_at=self.now + timedelta(hours=2),
                        reminder_minutes=30,
                        timezone_name="UTC",
                        reminder_sent=False,
                        status="scheduled",
                    ),
                    InterviewEvent(
                        user_id=user.id,
                        title="Cancelled",
                        starts_at=self.now + timedelta(minutes=5),
                        reminder_minutes=30,
                        timezone_name="UTC",
                        reminder_sent=False,
                        status="cancelled",
                    ),
                ]
            )
            await session.commit()

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def test_creates_one_in_app_notification_per_due_event(self) -> None:
        created = await deliver_due_reminders(
            session_factory=self.session_factory,
            now=self.now,
        )
        self.assertEqual(created, 1)

        async with self.session_factory() as session:
            notifications = (await session.scalars(select(UserNotification))).all()
            self.assertEqual(len(notifications), 1)
            self.assertEqual(notifications[0].title, "Upcoming interview: Due interview")

        created_again = await deliver_due_reminders(
            session_factory=self.session_factory,
            now=self.now,
        )
        self.assertEqual(created_again, 0)


if __name__ == "__main__":
    unittest.main()
