from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Identity,
    Index,
    Integer,
    String,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from flagman.core.db import Base
from flagman.support.domain import (
    INCIDENT_COMMENT_MAX_LENGTH,
    INCIDENT_DESCRIPTION_MAX_LENGTH,
    MESSAGE_TEXT_MAX_LENGTH,
    ConversationStatus,
    IncidentEventAction,
    IncidentStatus,
    IncidentType,
    MessageAuthor,
    MessageKind,
)

ENUM_LENGTH = 16
OBJECT_KEY_LENGTH = 128
ACTION_LENGTH = 24


def _listed(values: type[StrEnum]) -> str:
    return ", ".join(f"'{value}'" for value in sorted(values))


class Conversation(Base):
    __tablename__ = "support_conversations"
    __table_args__ = (
        CheckConstraint(f"status IN ({_listed(ConversationStatus)})", name="status_known"),
        # Parallel first messages cannot open two conversations for one client.
        Index(
            "uq_support_conversations_open_user",
            "user_id",
            unique=True,
            postgresql_where=text("status = 'open'"),
        ),
        # The queue: open conversations, the oldest unanswered first.
        Index(
            "ix_support_conversations_queue",
            "last_message_at",
            postgresql_where=text("status = 'open'"),
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), index=True)
    rental_id: Mapped[UUID | None] = mapped_column(ForeignKey("rentals.id"))
    status: Mapped[str] = mapped_column(String(ENUM_LENGTH))
    assigned_staff_id: Mapped[UUID | None] = mapped_column(ForeignKey("staff_members.id"))
    # Counted on write so the menu badge and the client's badge are one cheap query.
    staff_unread: Mapped[int] = mapped_column(Integer, default=0)
    client_unread: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_message_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Message(Base):
    __tablename__ = "support_messages"
    __table_args__ = (
        CheckConstraint(f"author IN ({_listed(MessageAuthor)})", name="author_known"),
        CheckConstraint(f"kind IN ({_listed(MessageKind)})", name="kind_known"),
        CheckConstraint(
            "(kind = 'text') = (body IS NOT NULL) AND (kind = 'photo') = (object_key IS NOT NULL)",
            name="content_matches_kind",
        ),
        CheckConstraint(
            "(author = 'staff') = (staff_id IS NOT NULL)", name="staff_id_matches_author"
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    # The order clients and the admin panel poll by: a UUID has none.
    seq: Mapped[int] = mapped_column(BigInteger, Identity(), unique=True)
    conversation_id: Mapped[UUID] = mapped_column(
        ForeignKey("support_conversations.id"), index=True
    )
    author: Mapped[str] = mapped_column(String(ENUM_LENGTH))
    staff_id: Mapped[UUID | None] = mapped_column(ForeignKey("staff_members.id"))
    kind: Mapped[str] = mapped_column(String(ENUM_LENGTH))
    body: Mapped[str | None] = mapped_column(String(MESSAGE_TEXT_MAX_LENGTH))
    object_key: Mapped[str | None] = mapped_column(String(OBJECT_KEY_LENGTH), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Incident(Base):
    __tablename__ = "support_incidents"
    __table_args__ = (
        CheckConstraint(f"type IN ({_listed(IncidentType)})", name="type_known"),
        CheckConstraint(f"status IN ({_listed(IncidentStatus)})", name="status_known"),
        CheckConstraint("(lat IS NULL) = (lon IS NULL)", name="location_complete"),
        # The queue: open incidents, accidents first, then the oldest.
        Index(
            "ix_support_incidents_queue",
            "priority",
            "created_at",
            postgresql_where=text("status IN ('new', 'in_progress')"),
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), index=True)
    rental_id: Mapped[UUID | None] = mapped_column(ForeignKey("rentals.id"))
    car_id: Mapped[UUID | None] = mapped_column(ForeignKey("cars.id"))
    type: Mapped[str] = mapped_column(String(ENUM_LENGTH))
    status: Mapped[str] = mapped_column(String(ENUM_LENGTH))
    # 0 for an accident in a rental in progress, 1 otherwise: sorts the queue.
    priority: Mapped[int] = mapped_column(Integer)
    description: Mapped[str] = mapped_column(String(INCIDENT_DESCRIPTION_MAX_LENGTH))
    lat: Mapped[float | None] = mapped_column(Float)
    lon: Mapped[float | None] = mapped_column(Float)
    assigned_staff_id: Mapped[UUID | None] = mapped_column(ForeignKey("staff_members.id"))
    sent_to_service: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class IncidentPhoto(Base):
    __tablename__ = "support_incident_photos"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    # No ON DELETE CASCADE: the photos are in the storage, which the database cannot remove.
    incident_id: Mapped[UUID] = mapped_column(ForeignKey("support_incidents.id"), index=True)
    object_key: Mapped[str] = mapped_column(String(OBJECT_KEY_LENGTH), unique=True)
    size_bytes: Mapped[int] = mapped_column(Integer)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class IncidentEvent(Base):
    """The chronology the admin panel shows; the audit log keeps the same actions for security."""

    __tablename__ = "support_incident_events"
    __table_args__ = (
        CheckConstraint(f"action IN ({_listed(IncidentEventAction)})", name="action_known"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    incident_id: Mapped[UUID] = mapped_column(ForeignKey("support_incidents.id"), index=True)
    staff_id: Mapped[UUID | None] = mapped_column(ForeignKey("staff_members.id"))
    action: Mapped[str] = mapped_column(String(ACTION_LENGTH))
    comment: Mapped[str | None] = mapped_column(String(INCIDENT_COMMENT_MAX_LENGTH))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
