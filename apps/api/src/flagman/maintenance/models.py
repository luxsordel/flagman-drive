from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column

from flagman.core.db import Base
from flagman.maintenance.domain import (
    COMMENT_MAX_LENGTH,
    DESCRIPTION_MAX_LENGTH,
    TaskStatus,
    TaskType,
)

STATUS_LENGTH = 16
OBJECT_KEY_LENGTH = 128


def _listed(values: type[StrEnum] | frozenset[TaskStatus]) -> str:
    return ", ".join(f"'{value}'" for value in sorted(values))


# A literal, not built from OPEN_STATUSES: Semgrep refuses text() over a computed string.
# test_models.py keeps the two in step.
_OPEN = "status IN ('assigned', 'in_progress', 'new')"


class MaintenanceTask(Base):
    __tablename__ = "maintenance_tasks"
    __table_args__ = (
        CheckConstraint(f"status IN ({_listed(TaskStatus)})", name="status_known"),
        CheckConstraint(f"type IN ({_listed(TaskType)})", name="type_known"),
        CheckConstraint(
            "status IN ('new', 'cancelled') OR assignee_id IS NOT NULL",
            name="assignee_set_once_assigned",
        ),
        CheckConstraint(
            "(status IN ('done', 'cancelled')) = (finished_at IS NOT NULL)",
            name="finished_at_matches_status",
        ),
        # The worker and an operator creating the same task at once: the second insert fails here.
        Index(
            "uq_maintenance_tasks_open_car_type",
            "car_id",
            "type",
            unique=True,
            postgresql_where=text(_OPEN),
        ),
        Index("ix_maintenance_tasks_open_status", "status", postgresql_where=text(_OPEN)),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    car_id: Mapped[UUID] = mapped_column(ForeignKey("cars.id"), index=True)
    type: Mapped[str] = mapped_column(String(STATUS_LENGTH))
    status: Mapped[str] = mapped_column(String(STATUS_LENGTH))
    description: Mapped[str | None] = mapped_column(String(DESCRIPTION_MAX_LENGTH))
    assignee_id: Mapped[UUID | None] = mapped_column(ForeignKey("staff_members.id"), index=True)
    # NULL for the tasks the worker creates.
    created_by: Mapped[UUID | None] = mapped_column(ForeignKey("staff_members.id"))
    comment: Mapped[str | None] = mapped_column(String(COMMENT_MAX_LENGTH))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class MaintenanceTaskPhoto(Base):
    __tablename__ = "maintenance_task_photos"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    # No ON DELETE CASCADE: the photos are in the storage, which the database cannot remove.
    task_id: Mapped[UUID] = mapped_column(ForeignKey("maintenance_tasks.id"), index=True)
    object_key: Mapped[str] = mapped_column(String(OBJECT_KEY_LENGTH), unique=True)
    size_bytes: Mapped[int] = mapped_column(Integer)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
