"""Public interface of the audit log for other modules: `record` and `list_events`."""

from sqlalchemy.ext.asyncio import AsyncSession

from flagman.audit import repository
from flagman.audit.domain import (
    AuditEntry,
    AuditEventFilter,
    AuditEventRecord,
    Cursor,
    validate_details,
)
from flagman.audit.schemas import AuditEventOut, AuditEventPage, AuditEventTarget


async def record(db: AsyncSession, entry: AuditEntry) -> None:
    """Adds an event to the caller's open transaction.

    Committed together with the action itself: a rolled back action leaves no event, and an
    action never commits without its event. To keep the event of a refused action, record it,
    let the transaction commit and raise only after it (docs/adr/0010).
    """
    if not db.in_transaction():
        raise RuntimeError("audit.service.record must run inside the caller's transaction")
    await repository.insert_event(db, entry, validate_details(entry.details))


async def list_events(
    db: AsyncSession, event_filter: AuditEventFilter, cursor: str | None, limit: int
) -> AuditEventPage:
    after = Cursor.decode(cursor) if cursor is not None else None
    # One extra row tells whether there is a next page without a separate count.
    records = await repository.list_events(db, event_filter, after, limit + 1)
    page = records[:limit]
    next_cursor = (
        Cursor(page[-1].occurred_at, page[-1].id).encode() if len(records) > limit else None
    )
    return AuditEventPage(items=[_to_out(item) for item in page], next_cursor=next_cursor)


def _to_out(item: AuditEventRecord) -> AuditEventOut:
    return AuditEventOut(
        id=item.id,
        occurred_at=item.occurred_at,
        actor_type=item.actor.type,
        actor_id=item.actor.id,
        action=item.action,
        target=AuditEventTarget(type=item.target.type, id=item.target.id) if item.target else None,
        ip_hash=item.origin.ip_hash,
        request_id=item.origin.request_id,
        details=dict(item.details),
    )
