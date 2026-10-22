"""Insert and read only: the table is append-only, so there is nothing to update or delete."""

from collections.abc import Mapping, Sequence

from sqlalchemy import ColumnElement, and_, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from flagman.audit.domain import (
    Actor,
    ActorType,
    AuditEntry,
    AuditEventFilter,
    AuditEventRecord,
    AuditTarget,
    Cursor,
    RequestOrigin,
)
from flagman.audit.models import AuditEvent


async def insert_event(
    db: AsyncSession, entry: AuditEntry, details: Mapping[str, str | int | bool | None]
) -> None:
    target = entry.target
    await db.execute(
        insert(AuditEvent).values(
            actor_type=entry.actor.type,
            actor_id=entry.actor.id,
            action=entry.action,
            target_type=target.type if target else None,
            target_id=target.id if target else None,
            ip_hash=entry.origin.ip_hash,
            request_id=entry.origin.request_id,
            details=dict(details),
        )
    )


async def list_events(
    db: AsyncSession, event_filter: AuditEventFilter, after: Cursor | None, limit: int
) -> Sequence[AuditEventRecord]:
    conditions: list[ColumnElement[bool]] = []
    if event_filter.actor_id is not None:
        conditions.append(AuditEvent.actor_id == event_filter.actor_id)
    if event_filter.action is not None:
        conditions.append(AuditEvent.action == event_filter.action)
    if event_filter.occurred_from is not None:
        conditions.append(AuditEvent.occurred_at >= event_filter.occurred_from)
    if event_filter.occurred_to is not None:
        conditions.append(AuditEvent.occurred_at < event_filter.occurred_to)
    if after is not None:
        # Keyset instead of OFFSET: new events arriving between pages do not shift them.
        conditions.append(
            or_(
                AuditEvent.occurred_at < after.occurred_at,
                and_(AuditEvent.occurred_at == after.occurred_at, AuditEvent.id < after.event_id),
            )
        )
    rows = await db.scalars(
        select(AuditEvent)
        .where(*conditions)
        .order_by(AuditEvent.occurred_at.desc(), AuditEvent.id.desc())
        .limit(limit)
    )
    return [_to_record(row) for row in rows]


def _to_record(row: AuditEvent) -> AuditEventRecord:
    target = (
        AuditTarget(row.target_type, row.target_id)
        if row.target_type is not None and row.target_id is not None
        else None
    )
    return AuditEventRecord(
        id=row.id,
        occurred_at=row.occurred_at,
        actor=Actor(ActorType(row.actor_type), row.actor_id),
        action=row.action,
        target=target,
        origin=RequestOrigin(ip_hash=row.ip_hash, request_id=row.request_id),
        details=row.details,
    )
