from collections.abc import Collection
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import Select, func, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from flagman.maintenance.domain import (
    OPEN_STATUSES,
    Task,
    TaskFilter,
    TaskStatus,
    TaskType,
    Worker,
)
from flagman.maintenance.models import OPEN_CONDITION
from flagman.maintenance.models import MaintenanceTask as TaskRow
from flagman.maintenance.models import MaintenanceTaskPhoto as PhotoRow


class OpenTaskTakenError(Exception):
    """The car already has an open task of this type: a parallel request or the worker won."""


def _to_task(row: TaskRow) -> Task:
    return Task(
        id=row.id,
        car_id=row.car_id,
        type=TaskType(row.type),
        status=TaskStatus(row.status),
        description=row.description,
        assignee_id=row.assignee_id,
        created_by=row.created_by,
        comment=row.comment,
        created_at=row.created_at,
        started_at=row.started_at,
        finished_at=row.finished_at,
    )


@dataclass(frozen=True)
class NewTask:
    car_id: UUID
    type: TaskType
    description: str | None
    assignee_id: UUID | None
    created_by: UUID | None


async def add_task(db: AsyncSession, task: NewTask, now: datetime) -> Task:
    """Raises OpenTaskTakenError; the caller's transaction is unusable after it."""
    row = TaskRow(
        car_id=task.car_id,
        type=task.type,
        status=TaskStatus.ASSIGNED if task.assignee_id else TaskStatus.NEW,
        description=task.description,
        assignee_id=task.assignee_id,
        created_by=task.created_by,
        created_at=now,
        updated_at=now,
    )
    db.add(row)
    try:
        await db.flush()
    except IntegrityError as error:
        raise OpenTaskTakenError from error
    return _to_task(row)


async def add_charging_task_if_absent(db: AsyncSession, car_id: UUID, now: datetime) -> Task | None:
    """For the worker: None when the car got an open charging task meanwhile."""
    row = await db.scalar(
        insert(TaskRow)
        .values(
            car_id=car_id,
            type=TaskType.CHARGING,
            status=TaskStatus.NEW,
            created_at=now,
            updated_at=now,
        )
        .on_conflict_do_nothing(
            # The index's own literal: Postgres matches the condition as written, and a set
            # of statuses would come out in another order in some processes.
            index_elements=["car_id", "type"],
            index_where=text(OPEN_CONDITION),
        )
        .returning(TaskRow)
    )
    return _to_task(row) if row else None


async def get_task(db: AsyncSession, task_id: UUID, *, for_update: bool = False) -> Task | None:
    query = select(TaskRow).where(TaskRow.id == task_id)
    if for_update:
        query = query.with_for_update()
    row = await db.scalar(query)
    return _to_task(row) if row else None


def _visible_to(query: Select[TaskRow], worker: Worker) -> Select[TaskRow]:
    if not worker.is_technician:
        return query
    # Mirrors domain.can_see: free tasks and the technician's own.
    return query.where(
        or_(
            TaskRow.assignee_id == worker.staff_id,
            (TaskRow.status == TaskStatus.NEW) & TaskRow.assignee_id.is_(None),
        )
    )


def _filtered(query: Select[TaskRow], task_filter: TaskFilter) -> Select[TaskRow]:
    if task_filter.statuses:
        query = query.where(TaskRow.status.in_(task_filter.statuses))
    if task_filter.types:
        query = query.where(TaskRow.type.in_(task_filter.types))
    if task_filter.car_id:
        query = query.where(TaskRow.car_id == task_filter.car_id)
    if task_filter.assignee_id:
        query = query.where(TaskRow.assignee_id == task_filter.assignee_id)
    if task_filter.free_only:
        query = query.where(TaskRow.status == TaskStatus.NEW, TaskRow.assignee_id.is_(None))
    return query


async def list_tasks(
    db: AsyncSession, worker: Worker, task_filter: TaskFilter, limit: int, offset: int
) -> tuple[list[Task], int]:
    """Newest first, with the total count for paging."""
    query = _filtered(_visible_to(select(TaskRow), worker), task_filter)
    total = await db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = await db.scalars(
        query.order_by(TaskRow.created_at.desc(), TaskRow.id).limit(limit).offset(offset)
    )
    return [_to_task(row) for row in rows], total


async def car_ids_with_open_tasks(db: AsyncSession, car_ids: Collection[UUID]) -> set[UUID]:
    if not car_ids:
        return set()
    rows = await db.scalars(
        select(TaskRow.car_id).where(TaskRow.car_id.in_(car_ids), TaskRow.status.in_(OPEN_STATUSES))
    )
    return set(rows)


async def has_other_task_in_progress(db: AsyncSession, car_id: UUID, task_id: UUID) -> bool:
    found = await db.scalar(
        select(TaskRow.id).where(
            TaskRow.car_id == car_id,
            TaskRow.id != task_id,
            TaskRow.status == TaskStatus.IN_PROGRESS,
        )
    )
    return found is not None


async def assign(
    db: AsyncSession, task_id: UUID, status: TaskStatus, assignee_id: UUID, now: datetime
) -> None:
    await db.execute(
        update(TaskRow)
        .where(TaskRow.id == task_id)
        .values(status=status, assignee_id=assignee_id, updated_at=now)
    )


async def start(
    db: AsyncSession, task_id: UUID, status: TaskStatus, assignee_id: UUID, now: datetime
) -> None:
    await db.execute(
        update(TaskRow)
        .where(TaskRow.id == task_id)
        .values(status=status, assignee_id=assignee_id, started_at=now, updated_at=now)
    )


async def finish(
    db: AsyncSession, task_id: UUID, status: TaskStatus, comment: str | None, now: datetime
) -> None:
    await db.execute(
        update(TaskRow)
        .where(TaskRow.id == task_id)
        .values(status=status, comment=comment, finished_at=now, updated_at=now)
    )


@dataclass(frozen=True)
class StoredPhoto:
    id: UUID
    object_key: str
    uploaded_at: datetime


async def list_photos(db: AsyncSession, task_id: UUID) -> list[StoredPhoto]:
    rows = await db.scalars(
        select(PhotoRow)
        .where(PhotoRow.task_id == task_id)
        .order_by(PhotoRow.uploaded_at, PhotoRow.id)
    )
    return [StoredPhoto(row.id, row.object_key, row.uploaded_at) for row in rows]


async def count_photos(db: AsyncSession, task_id: UUID) -> int:
    count = await db.scalar(select(func.count()).where(PhotoRow.task_id == task_id))
    return count or 0


async def add_photo(
    db: AsyncSession, task_id: UUID, object_key: str, size_bytes: int, now: datetime
) -> None:
    db.add(PhotoRow(task_id=task_id, object_key=object_key, size_bytes=size_bytes, uploaded_at=now))
    await db.flush()
