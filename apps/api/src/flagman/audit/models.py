from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, Index, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from flagman.core.db import Base

HASH_LENGTH = 64  # hex HMAC-SHA256


class AuditEvent(Base):
    """Append-only: a database trigger rejects UPDATE, DELETE and TRUNCATE (docs/adr/0010)."""

    __tablename__ = "audit_events"
    __table_args__ = (
        CheckConstraint(
            "(actor_type = 'system') = (actor_id IS NULL)", name="actor_id_matches_type"
        ),
        CheckConstraint("(target_type IS NULL) = (target_id IS NULL)", name="target_complete"),
        # Pages go by (occurred_at, id) from the newest; the other two serve the list filters.
        Index("ix_audit_events_occurred_at_id", "occurred_at", "id"),
        Index("ix_audit_events_actor_id_occurred_at", "actor_id", "occurred_at"),
        Index("ix_audit_events_action_occurred_at", "action", "occurred_at"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    # Set by the database, not by the caller. clock_timestamp(), not now(): now() is the start of
    # the transaction, so events recorded in one transaction would share it.
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.clock_timestamp()
    )
    actor_type: Mapped[str] = mapped_column(String(16))
    actor_id: Mapped[UUID | None]
    action: Mapped[str] = mapped_column(String(64))
    target_type: Mapped[str | None] = mapped_column(String(64))
    target_id: Mapped[UUID | None]
    ip_hash: Mapped[str | None] = mapped_column(String(HASH_LENGTH))
    request_id: Mapped[UUID | None]
    details: Mapped[dict[str, Any]] = mapped_column(JSONB)
