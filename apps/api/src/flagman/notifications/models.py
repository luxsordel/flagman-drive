from collections.abc import Iterable
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, SmallInteger, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from flagman.core.db import Base
from flagman.notifications.domain import (
    PUSH_TICKET_ID_MAX_LENGTH,
    PUSH_TOKEN_MAX_LENGTH,
    NotificationCategory,
    NotificationEvent,
    OutboxStatus,
    PushPlatform,
)

ENUM_LENGTH = 32
DEDUP_KEY_LENGTH = 128


def _known(column: str, values: Iterable[str]) -> str:
    return f"{column} IN (" + ", ".join(f"'{value}'" for value in values) + ")"


class PushDevice(Base):
    """The app on one phone, as signed in: one per session, and a token belongs to one session."""

    __tablename__ = "push_devices"
    __table_args__ = (CheckConstraint(_known("platform", PushPlatform), name="platform_known"),)

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    # Deleted with the session by the auth cleanup; a revoked session's device is skipped and
    # deleted by the sender before that.
    session_id: Mapped[UUID] = mapped_column(
        ForeignKey("auth_sessions.id", ondelete="CASCADE"), unique=True
    )
    token: Mapped[str] = mapped_column(String(PUSH_TOKEN_MAX_LENGTH), unique=True)
    platform: Mapped[str] = mapped_column(String(ENUM_LENGTH))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PushTicket(Base):
    """A message the provider took, until its receipt tells whether the phone got it."""

    __tablename__ = "push_tickets"

    # The provider's ticket id; the token is not kept here, the device holds it.
    id: Mapped[str] = mapped_column(String(PUSH_TICKET_ID_MAX_LENGTH), primary_key=True)
    # A deleted device has no receipt worth asking for.
    device_id: Mapped[UUID] = mapped_column(
        ForeignKey("push_devices.id", ondelete="CASCADE"), index=True
    )
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class NotificationOptOut(Base):
    """A category the client switched off; no row means it is on."""

    __tablename__ = "notification_opt_outs"
    __table_args__ = (
        CheckConstraint(_known("category", NotificationCategory), name="category_known"),
    )

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    category: Mapped[str] = mapped_column(String(ENUM_LENGTH), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class OutboxNotification(Base):
    """A notification waiting to be sent, written in the transaction of the event it tells of."""

    __tablename__ = "notification_outbox"
    __table_args__ = (
        CheckConstraint(_known("event", NotificationEvent), name="event_known"),
        CheckConstraint(_known("status", OutboxStatus), name="status_known"),
        CheckConstraint("attempts >= 0", name="attempts_non_negative"),
        # The sender only ever looks for pending rows that are due.
        Index(
            "ix_notification_outbox_due",
            "next_attempt_at",
            postgresql_where=text("status = 'pending'"),
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    event: Mapped[str] = mapped_column(String(ENUM_LENGTH))
    rental_id: Mapped[UUID]
    # The template's numbers only (flagman.notifications.domain.Notification).
    params: Mapped[dict[str, Any]] = mapped_column(JSONB)
    # The same event for the same rental is stored once, however many times it is reported.
    dedup_key: Mapped[str] = mapped_column(String(DEDUP_KEY_LENGTH), unique=True)
    status: Mapped[str] = mapped_column(String(ENUM_LENGTH))
    attempts: Mapped[int] = mapped_column(SmallInteger)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # Not sent after this (Notification.lifetime): still pending then means undelivered.
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # Kept NOTIFICATIONS_RETENTION_DAYS from here, then deleted by the outbox cleanup.
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
