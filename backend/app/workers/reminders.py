import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.app.db.session import SessionFactory, engine
from backend.app.models.schedule import InterviewEvent, UserNotification

logger = logging.getLogger(__name__)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


async def deliver_due_reminders(
    *,
    session_factory: async_sessionmaker[AsyncSession] = SessionFactory,
    now: datetime | None = None,
) -> int:
    current_time = now or datetime.now(timezone.utc)
    horizon = current_time + timedelta(days=7)
    async with session_factory() as session:
        events = (
            await session.scalars(
                select(InterviewEvent)
                .where(
                    InterviewEvent.status == "scheduled",
                    InterviewEvent.reminder_sent.is_(False),
                    InterviewEvent.starts_at > current_time,
                    InterviewEvent.starts_at <= horizon,
                )
                .order_by(InterviewEvent.starts_at)
                .limit(500)
            )
        ).all()

        due = [
            event
            for event in events
            if _as_utc(event.starts_at)
            <= current_time + timedelta(minutes=event.reminder_minutes)
        ]
        for event in due:
            session.add(
                UserNotification(
                    user_id=event.user_id,
                    event_id=event.id,
                    kind="interview_reminder",
                    title=f"Upcoming interview: {event.title}",
                    body=(
                        f"{event.title} is scheduled at "
                        f"{_as_utc(event.starts_at).isoformat()} "
                        f"({event.timezone_name})."
                    ),
                    payload={
                        "company": event.company,
                        "role": event.role,
                        "meeting_url": event.meeting_url,
                        "starts_at": _as_utc(event.starts_at).isoformat(),
                    },
                )
            )
            event.reminder_sent = True
        await session.commit()
        return len(due)


async def run_reminder_worker(poll_seconds: int = 30) -> None:
    logger.info("Interview reminder worker started")
    try:
        while True:
            try:
                delivered = await deliver_due_reminders()
                if delivered:
                    logger.info("Created %s interview reminders", delivered)
            except Exception:
                logger.exception("Could not create due interview reminders")
            await asyncio.sleep(poll_seconds)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_reminder_worker())
