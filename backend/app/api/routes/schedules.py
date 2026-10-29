from datetime import datetime, timezone
from typing import Annotated
from urllib.parse import quote
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import Response
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import desc, select

from backend.app.api.deps import CurrentUser, SessionDep
from backend.app.models.schedule import InterviewEvent, UserNotification

router = APIRouter(tags=["schedule and notifications"])


class EventCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    company: str | None = Field(default=None, max_length=200)
    role: str | None = Field(default=None, max_length=200)
    starts_at: datetime
    ends_at: datetime | None = None
    timezone_name: str = Field(default="UTC", min_length=1, max_length=64)
    meeting_url: str | None = Field(default=None, max_length=2048)
    notes: str | None = Field(default=None, max_length=10_000)
    reminder_minutes: int = Field(default=30, ge=0, le=10080)

    @field_validator("title", "company", "role", "timezone_name")
    @classmethod
    def strip_text(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None

    @field_validator("timezone_name")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as error:
            raise ValueError("Timezone must be a valid IANA timezone name.") from error
        return value

    @field_validator("starts_at", "ends_at")
    @classmethod
    def require_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("Date/time values must include a timezone.")
        return value

    @model_validator(mode="after")
    def validate_event(self) -> "EventCreate":
        if not self.title:
            raise ValueError("Title cannot be blank.")
        if self.ends_at is not None and self.ends_at <= self.starts_at:
            raise ValueError("Event end time must be after its start time.")
        return self


class EventUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    company: str | None = Field(default=None, max_length=200)
    role: str | None = Field(default=None, max_length=200)
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    timezone_name: str | None = Field(default=None, min_length=1, max_length=64)
    meeting_url: str | None = Field(default=None, max_length=2048)
    notes: str | None = Field(default=None, max_length=10_000)
    reminder_minutes: int | None = Field(default=None, ge=0, le=10080)

    @field_validator("starts_at", "ends_at")
    @classmethod
    def require_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("Date/time values must include a timezone.")
        return value

    @field_validator("timezone_name")
    @classmethod
    def validate_timezone(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as error:
            raise ValueError("Timezone must be a valid IANA timezone name.") from error
        return value


class EventRead(BaseModel):
    id: str
    title: str
    company: str | None
    role: str | None
    starts_at: datetime
    ends_at: datetime | None
    timezone_name: str
    meeting_url: str | None
    notes: str | None
    reminder_minutes: int
    reminder_sent: bool
    status: str
    created_at: datetime


class NotificationRead(BaseModel):
    id: str
    event_id: str | None
    kind: str
    title: str
    body: str
    payload: dict
    read_at: datetime | None
    created_at: datetime


async def _owned_event(
    event_id: UUID, user_id: UUID, session: SessionDep
) -> InterviewEvent:
    event = await session.scalar(
        select(InterviewEvent).where(
            InterviewEvent.id == event_id,
            InterviewEvent.user_id == user_id,
        )
    )
    if event is None:
        raise HTTPException(status_code=404, detail="Interview event not found.")
    return event


def _event_read(event: InterviewEvent) -> EventRead:
    return EventRead(
        id=str(event.id),
        title=event.title,
        company=event.company,
        role=event.role,
        starts_at=event.starts_at,
        ends_at=event.ends_at,
        timezone_name=event.timezone_name,
        meeting_url=event.meeting_url,
        notes=event.notes,
        reminder_minutes=event.reminder_minutes,
        reminder_sent=event.reminder_sent,
        status=event.status,
        created_at=event.created_at,
    )


@router.post(
    "/schedule/events",
    response_model=EventRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_event(
    payload: EventCreate,
    user: CurrentUser,
    session: SessionDep,
) -> EventRead:
    event = InterviewEvent(user_id=user.id, **payload.model_dump())
    session.add(event)
    await session.commit()
    await session.refresh(event)
    return _event_read(event)


@router.get("/schedule/events", response_model=list[EventRead])
async def list_events(
    user: CurrentUser,
    session: SessionDep,
    from_at: Annotated[datetime | None, Query()] = None,
    to_at: Annotated[datetime | None, Query()] = None,
) -> list[EventRead]:
    if from_at is not None and from_at.tzinfo is None:
        raise HTTPException(status_code=422, detail="from_at must include a timezone.")
    if to_at is not None and to_at.tzinfo is None:
        raise HTTPException(status_code=422, detail="to_at must include a timezone.")
    query = select(InterviewEvent).where(
        InterviewEvent.user_id == user.id,
        InterviewEvent.status == "scheduled",
    )
    if from_at is not None:
        query = query.where(InterviewEvent.starts_at >= from_at)
    if to_at is not None:
        query = query.where(InterviewEvent.starts_at < to_at)
    events = (await session.scalars(query.order_by(InterviewEvent.starts_at))).all()
    return [_event_read(event) for event in events]


@router.patch("/schedule/events/{event_id}", response_model=EventRead)
async def update_event(
    event_id: UUID,
    payload: EventUpdate,
    user: CurrentUser,
    session: SessionDep,
) -> EventRead:
    event = await _owned_event(event_id, user.id, session)
    values = payload.model_dump(exclude_unset=True)
    if "title" in values:
        values["title"] = values["title"].strip()
        if not values["title"]:
            raise HTTPException(status_code=422, detail="Title cannot be blank.")
    for field in ("company", "role", "timezone_name"):
        if values.get(field) is not None:
            values[field] = values[field].strip()
    start = values.get("starts_at", event.starts_at)
    end = values.get("ends_at", event.ends_at)
    if start is not None and end is not None and end <= start:
        raise HTTPException(status_code=422, detail="End time must be after start time.")
    for field, value in values.items():
        setattr(event, field, value)
    if {"starts_at", "reminder_minutes"} & values.keys():
        event.reminder_sent = False
    await session.commit()
    await session.refresh(event)
    return _event_read(event)


@router.delete("/schedule/events/{event_id}", status_code=status.HTTP_204_NO_CONTENT)
async def cancel_event(
    event_id: UUID,
    user: CurrentUser,
    session: SessionDep,
) -> Response:
    event = await _owned_event(event_id, user.id, session)
    event.status = "cancelled"
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/schedule/events.ics")
async def export_calendar(
    user: CurrentUser,
    session: SessionDep,
) -> Response:
    events = (
        await session.scalars(
            select(InterviewEvent)
            .where(
                InterviewEvent.user_id == user.id,
                InterviewEvent.status == "scheduled",
            )
            .order_by(InterviewEvent.starts_at)
        )
    ).all()
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//AI Interview Guide//Interview Schedule//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
    ]
    for event in events:
        lines.extend(
            [
                "BEGIN:VEVENT",
                f"UID:{event.id}@ai-interview-guide",
                f"DTSTAMP:{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}",
                f"DTSTART:{event.starts_at.astimezone(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}",
                f"SUMMARY:{_ics_escape(event.title)}",
            ]
        )
        if event.ends_at:
            lines.append(
                f"DTEND:{event.ends_at.astimezone(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
            )
        if event.company:
            lines.append(f"ORGANIZER:{_ics_escape(event.company)}")
        if event.meeting_url:
            lines.append(f"URL:{_ics_escape(event.meeting_url)}")
        if event.notes:
            lines.append(f"DESCRIPTION:{_ics_escape(event.notes)}")
        lines.extend(["END:VEVENT"])
    lines.append("END:VCALENDAR")
    calendar = "\r\n".join(lines) + "\r\n"
    return Response(
        content=calendar,
        media_type="text/calendar; charset=utf-8",
        headers={
            "Content-Disposition": (
                f"attachment; filename*=UTF-8''{quote('interview-schedule.ics')}"
            )
        },
    )


def _ics_escape(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\r\n", "\\n")
        .replace("\r", "\\n")
        .replace("\n", "\\n")
    )


@router.get("/notifications", response_model=list[NotificationRead])
async def list_notifications(
    user: CurrentUser,
    session: SessionDep,
    unread_only: bool = False,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> list[NotificationRead]:
    query = select(UserNotification).where(UserNotification.user_id == user.id)
    if unread_only:
        query = query.where(UserNotification.read_at.is_(None))
    notifications = (
        await session.scalars(
            query.order_by(desc(UserNotification.created_at)).limit(limit)
        )
    ).all()
    return [
        NotificationRead(
            id=str(item.id),
            event_id=str(item.event_id) if item.event_id else None,
            kind=item.kind,
            title=item.title,
            body=item.body,
            payload=item.payload,
            read_at=item.read_at,
            created_at=item.created_at,
        )
        for item in notifications
    ]


@router.post("/notifications/{notification_id}/read", response_model=NotificationRead)
async def mark_notification_read(
    notification_id: UUID,
    user: CurrentUser,
    session: SessionDep,
) -> NotificationRead:
    notification = await session.scalar(
        select(UserNotification).where(
            UserNotification.id == notification_id,
            UserNotification.user_id == user.id,
        )
    )
    if notification is None:
        raise HTTPException(status_code=404, detail="Notification not found.")
    notification.read_at = notification.read_at or datetime.now(timezone.utc)
    await session.commit()
    await session.refresh(notification)
    return NotificationRead(
        id=str(notification.id),
        event_id=str(notification.event_id) if notification.event_id else None,
        kind=notification.kind,
        title=notification.title,
        body=notification.body,
        payload=notification.payload,
        read_at=notification.read_at,
        created_at=notification.created_at,
    )
