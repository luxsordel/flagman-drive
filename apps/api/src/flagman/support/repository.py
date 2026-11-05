"""Support tables. Rows stay inside the module: the service maps them to schemas."""

from collections import defaultdict
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import case, func, select, text, tuple_, update
from sqlalchemy.dialects.postgresql import distinct_on, insert
from sqlalchemy.ext.asyncio import AsyncSession

from flagman.support.domain import (
    OPEN_INCIDENT_STATUSES,
    ConversationStatus,
    Fine,
    FineCursor,
    FineEventAction,
    FineFilter,
    FineStatus,
    IncidentCursor,
    IncidentEventAction,
    IncidentStatus,
    IncidentType,
    MessageAuthor,
    MessageKind,
    RefundStatus,
)
from flagman.support.models import (
    Conversation,
    FineEvent,
    FinePhoto,
    Incident,
    IncidentEvent,
    IncidentPhoto,
    Message,
)
from flagman.support.models import Fine as FineRow

# Any fixed number unique among the two-key advisory locks of the database.
_INCIDENTS_LOCK_CLASS = 4_600_201
_RENTAL_FINES_LOCK_CLASS = 4_600_202


@dataclass(frozen=True)
class NewMessage:
    author: MessageAuthor
    kind: MessageKind
    at: datetime
    staff_id: UUID | None = None
    body: str | None = None
    object_key: str | None = None
    client_message_id: UUID | None = None


@dataclass(frozen=True)
class NewIncident:
    user_id: UUID
    rental_id: UUID | None
    car_id: UUID | None
    type: IncidentType
    priority: int
    description: str
    location: tuple[float, float] | None
    at: datetime
    client_report_id: UUID | None = None


@dataclass(frozen=True)
class NewIncidentEvent:
    staff_id: UUID | None
    action: IncidentEventAction
    at: datetime
    comment: str | None = None


async def get_open_conversation(
    db: AsyncSession, user_id: UUID, *, for_update: bool = False
) -> Conversation | None:
    query = select(Conversation).where(
        Conversation.user_id == user_id, Conversation.status == ConversationStatus.OPEN
    )
    if for_update:
        query = query.with_for_update()
    return await db.scalar(query)


async def get_latest_conversation(db: AsyncSession, user_id: UUID) -> Conversation | None:
    """The open one, or the last closed one so the client still sees the answer."""
    return await db.scalar(
        select(Conversation)
        .where(Conversation.user_id == user_id)
        # The open one wins a tie: closing and the next message can share a timestamp.
        .order_by(
            (Conversation.status == ConversationStatus.OPEN).desc(),
            Conversation.created_at.desc(),
            Conversation.id.desc(),
        )
        .limit(1)
    )


async def open_conversation(
    db: AsyncSession, user_id: UUID, rental_id: UUID | None, now: datetime
) -> Conversation:
    """The client's open conversation, created if there is none; holds its row until commit.

    ON CONFLICT DO NOTHING on the partial unique index: two first messages sent at once both
    end up in the conversation one of them created.
    """
    await db.execute(
        insert(Conversation)
        .values(
            user_id=user_id,
            rental_id=rental_id,
            status=ConversationStatus.OPEN,
            staff_unread=0,
            client_unread=0,
            created_at=now,
            last_message_at=now,
        )
        .on_conflict_do_nothing(index_elements=["user_id"], index_where=text("status = 'open'"))
    )
    conversation = await get_open_conversation(db, user_id, for_update=True)
    if conversation is None:
        raise RuntimeError("the open conversation vanished inside its own transaction")
    return conversation


async def get_conversation(
    db: AsyncSession, conversation_id: UUID, *, for_update: bool = False
) -> Conversation | None:
    query = select(Conversation).where(Conversation.id == conversation_id)
    if for_update:
        query = query.with_for_update()
    return await db.scalar(query)


async def add_message(db: AsyncSession, conversation: Conversation, new: NewMessage) -> Message:
    message = Message(
        conversation_id=conversation.id,
        author=new.author,
        staff_id=new.staff_id,
        kind=new.kind,
        body=new.body,
        object_key=new.object_key,
        client_message_id=new.client_message_id,
        created_at=new.at,
    )
    db.add(message)
    conversation.last_message_at = new.at
    if new.author is MessageAuthor.CLIENT:
        conversation.staff_unread += 1
    else:
        conversation.client_unread += 1
    await db.flush()
    return message


async def find_resent_message(
    db: AsyncSession, user_id: UUID, client_message_id: UUID
) -> Message | None:
    """Across the client's conversations: support may close one between two attempts."""
    return await db.scalar(
        select(Message)
        .join(Conversation, Conversation.id == Message.conversation_id)
        .where(Conversation.user_id == user_id, Message.client_message_id == client_message_id)
    )


async def list_messages(
    db: AsyncSession, conversation_id: UUID, after_seq: int, limit: int
) -> Sequence[Message]:
    return (
        await db.scalars(
            select(Message)
            .where(Message.conversation_id == conversation_id, Message.seq > after_seq)
            .order_by(Message.seq)
            .limit(limit)
        )
    ).all()


async def last_messages(db: AsyncSession, conversation_ids: Sequence[UUID]) -> dict[UUID, Message]:
    rows = await db.scalars(
        select(Message)
        .where(Message.conversation_id.in_(conversation_ids))
        .ext(distinct_on(Message.conversation_id))
        .order_by(Message.conversation_id, Message.seq.desc())
    )
    return {message.conversation_id: message for message in rows}


async def get_message(db: AsyncSession, conversation_id: UUID, message_id: UUID) -> Message | None:
    return await db.scalar(
        select(Message).where(Message.id == message_id, Message.conversation_id == conversation_id)
    )


async def get_client_message(db: AsyncSession, user_id: UUID, message_id: UUID) -> Message | None:
    """The owner is part of the query: a message of another client's conversation is not found."""
    return await db.scalar(
        select(Message)
        .join(Conversation, Conversation.id == Message.conversation_id)
        .where(Message.id == message_id, Conversation.user_id == user_id)
    )


async def list_queue(db: AsyncSession, limit: int) -> Sequence[Conversation]:
    """Open conversations: with unread messages first, then the longest waiting."""
    return (
        await db.scalars(
            select(Conversation)
            .where(Conversation.status == ConversationStatus.OPEN)
            .order_by(
                case((Conversation.staff_unread > 0, 0), else_=1), Conversation.last_message_at
            )
            .limit(limit)
        )
    ).all()


async def count_unread_conversations(db: AsyncSession) -> int:
    return (
        await db.scalar(
            select(func.count())
            .select_from(Conversation)
            .where(Conversation.status == ConversationStatus.OPEN, Conversation.staff_unread > 0)
        )
        or 0
    )


async def mark_read(
    db: AsyncSession, conversation_id: UUID, reader: MessageAuthor, seen_seq: int
) -> None:
    """Leaves unread only the other side's messages the reader has not been shown, past `seen_seq`.

    The row lock comes first: an answer being saved right now commits before the count below, so
    it stays unread instead of being cleared along with what the reader saw.
    """
    await db.execute(
        select(Conversation.id).where(Conversation.id == conversation_id).with_for_update()
    )
    if reader is MessageAuthor.CLIENT:
        column, writer = Conversation.client_unread, MessageAuthor.STAFF
    else:
        column, writer = Conversation.staff_unread, MessageAuthor.CLIENT
    unseen = (
        select(func.count())
        .select_from(Message)
        .where(
            Message.conversation_id == conversation_id,
            Message.author == writer,
            Message.seq > seen_seq,
        )
        .scalar_subquery()
    )
    await db.execute(
        update(Conversation).where(Conversation.id == conversation_id).values({column: unseen})
    )


async def find_resent_incident(
    db: AsyncSession, user_id: UUID, client_report_id: UUID
) -> Incident | None:
    return await db.scalar(
        select(Incident).where(
            Incident.user_id == user_id, Incident.client_report_id == client_report_id
        )
    )


async def create_incident(db: AsyncSession, new: NewIncident) -> Incident:
    incident = Incident(
        user_id=new.user_id,
        rental_id=new.rental_id,
        car_id=new.car_id,
        type=new.type,
        status=IncidentStatus.NEW,
        priority=new.priority,
        description=new.description,
        lat=new.location[0] if new.location else None,
        lon=new.location[1] if new.location else None,
        client_report_id=new.client_report_id,
        sent_to_service=False,
        created_at=new.at,
        updated_at=new.at,
    )
    db.add(incident)
    await db.flush()
    return incident


async def get_incident(
    db: AsyncSession,
    incident_id: UUID,
    *,
    user_id: UUID | None = None,
    for_update: bool = False,
) -> Incident | None:
    """With `user_id` another client's incident is simply not found."""
    query = select(Incident).where(Incident.id == incident_id)
    if user_id is not None:
        query = query.where(Incident.user_id == user_id)
    if for_update:
        query = query.with_for_update()
    return await db.scalar(query)


async def car_held_by_incident(db: AsyncSession, car_id: UUID) -> bool:
    found = await db.scalar(
        select(Incident.id)
        .where(
            Incident.car_id == car_id,
            Incident.sent_to_service.is_(True),
            Incident.status.in_(OPEN_INCIDENT_STATUSES),
        )
        .limit(1)
    )
    return found is not None


async def count_open_incidents(db: AsyncSession, user_id: UUID) -> int:
    return (
        await db.scalar(
            select(func.count())
            .select_from(Incident)
            .where(Incident.user_id == user_id, Incident.status.in_(OPEN_INCIDENT_STATUSES))
        )
        or 0
    )


async def count_new_incidents(db: AsyncSession) -> int:
    return (
        await db.scalar(
            select(func.count()).select_from(Incident).where(Incident.status == IncidentStatus.NEW)
        )
        or 0
    )


async def lock_client_incidents(db: AsyncSession, user_id: UUID) -> None:
    """A client with no open incidents has no rows to lock, so a transaction lock on the client
    comes first: parallel reports would otherwise all pass the count of open ones."""
    # The two-key form keeps clear of the worker's single-key locks; two clients sharing the
    # 32 bits of the id only wait for each other.
    await db.execute(
        select(func.pg_advisory_xact_lock(_INCIDENTS_LOCK_CLASS, (user_id.int >> 96) - 2**31))
    )


@dataclass(frozen=True)
class IncidentFilter:
    user_id: UUID | None = None
    statuses: frozenset[IncidentStatus] | None = None
    type: IncidentType | None = None
    # The operators' queue: accidents in a trip first, then the oldest. Otherwise the newest first.
    queue: bool = False


async def list_incidents(
    db: AsyncSession, incident_filter: IncidentFilter, after: IncidentCursor | None, limit: int
) -> Sequence[Incident]:
    query = select(Incident)
    if incident_filter.queue:
        query = query.order_by(Incident.priority, Incident.created_at, Incident.id)
    else:
        query = query.order_by(Incident.created_at.desc(), Incident.id.desc())
    # Keyset instead of OFFSET: incidents reported between pages do not shift them.
    if after is not None and incident_filter.queue:
        query = query.where(
            tuple_(Incident.priority, Incident.created_at, Incident.id)
            > tuple_(after.priority, after.created_at, after.incident_id)
        )
    elif after is not None:
        query = query.where(
            tuple_(Incident.created_at, Incident.id) < tuple_(after.created_at, after.incident_id)
        )
    if incident_filter.user_id is not None:
        query = query.where(Incident.user_id == incident_filter.user_id)
    if incident_filter.statuses:
        query = query.where(Incident.status.in_(incident_filter.statuses))
    if incident_filter.type is not None:
        query = query.where(Incident.type == incident_filter.type)
    return (await db.scalars(query.limit(limit))).all()


async def count_incident_photos(db: AsyncSession, incident_id: UUID) -> int:
    return (
        await db.scalar(
            select(func.count())
            .select_from(IncidentPhoto)
            .where(IncidentPhoto.incident_id == incident_id)
        )
        or 0
    )


async def photos_by_incident(
    db: AsyncSession, incident_ids: Collection[UUID]
) -> dict[UUID, list[IncidentPhoto]]:
    """One query for a whole page of incidents; an incident without photos is not a key."""
    rows = await db.scalars(
        select(IncidentPhoto)
        .where(IncidentPhoto.incident_id.in_(incident_ids))
        .order_by(IncidentPhoto.uploaded_at, IncidentPhoto.id)
    )
    grouped: defaultdict[UUID, list[IncidentPhoto]] = defaultdict(list)
    for row in rows:
        grouped[row.incident_id].append(row)
    return grouped


async def add_incident_photo(
    db: AsyncSession, incident_id: UUID, object_key: str, size_bytes: int, now: datetime
) -> None:
    db.add(
        IncidentPhoto(
            incident_id=incident_id, object_key=object_key, size_bytes=size_bytes, uploaded_at=now
        )
    )
    await db.flush()


async def get_incident_photo(
    db: AsyncSession, incident_id: UUID, photo_id: UUID
) -> IncidentPhoto | None:
    return await db.scalar(
        select(IncidentPhoto).where(
            IncidentPhoto.id == photo_id, IncidentPhoto.incident_id == incident_id
        )
    )


async def add_incident_event(db: AsyncSession, incident_id: UUID, new: NewIncidentEvent) -> None:
    db.add(
        IncidentEvent(
            incident_id=incident_id,
            staff_id=new.staff_id,
            action=new.action,
            comment=new.comment,
            created_at=new.at,
        )
    )
    await db.flush()


async def events_by_incident(
    db: AsyncSession, incident_ids: Collection[UUID]
) -> dict[UUID, list[IncidentEvent]]:
    """One query for a whole page of incidents; an incident without events is not a key."""
    rows = await db.scalars(
        select(IncidentEvent)
        .where(IncidentEvent.incident_id.in_(incident_ids))
        .order_by(IncidentEvent.created_at, IncidentEvent.id)
    )
    grouped: defaultdict[UUID, list[IncidentEvent]] = defaultdict(list)
    for row in rows:
        grouped[row.incident_id].append(row)
    return grouped


# Fines (docs/adr/0027)


def _to_fine(row: FineRow) -> Fine:
    return Fine(
        id=row.id,
        rental_id=row.rental_id,
        user_id=row.user_id,
        fine_type_version_id=row.fine_type_version_id,
        amount=row.amount,
        status=FineStatus(row.status),
        comment=row.comment,
        created_by=row.created_by,
        created_at=row.created_at,
        charge_attempts=row.charge_attempts,
        next_at=row.next_at,
        cancel_reason=row.cancel_reason,
        cancelled_by=row.cancelled_by,
        cancelled_at=row.cancelled_at,
        refund_status=RefundStatus(row.refund_status) if row.refund_status else None,
        refund_attempts=row.refund_attempts,
    )


@dataclass(frozen=True)
class NewFine:
    rental_id: UUID
    user_id: UUID
    fine_type_id: UUID
    fine_type_version_id: UUID
    amount: int
    comment: str | None
    created_by: UUID | None
    at: datetime
    # The operator's form's; None for an automatic fine.
    idempotency_key: UUID | None = None


async def add_fine(db: AsyncSession, new: NewFine) -> Fine | None:
    """Returns None when the trip has this automatic fine already, or a manual fine with this
    key was added first by a parallel request."""
    values = {
        "rental_id": new.rental_id,
        "user_id": new.user_id,
        "fine_type_id": new.fine_type_id,
        "fine_type_version_id": new.fine_type_version_id,
        "amount": new.amount,
        "status": FineStatus.PENDING,
        "comment": new.comment,
        "created_by": new.created_by,
        "idempotency_key": new.idempotency_key,
        "created_at": new.at,
        # Due at once: the request or the worker charges it right after the commit.
        "next_at": new.at,
        "updated_at": new.at,
    }
    query = insert(FineRow).values(**values)
    if new.created_by is None:
        query = query.on_conflict_do_nothing(
            index_elements=[FineRow.rental_id, FineRow.fine_type_id],
            index_where=text("created_by IS NULL"),
        )
    else:
        query = query.on_conflict_do_nothing(
            index_elements=[FineRow.idempotency_key],
            index_where=text("idempotency_key IS NOT NULL"),
        )
    row = await db.scalar(query.returning(FineRow))
    return _to_fine(row) if row else None


async def get_fine_by_key(db: AsyncSession, idempotency_key: UUID) -> Fine | None:
    row = await db.scalar(select(FineRow).where(FineRow.idempotency_key == idempotency_key))
    return _to_fine(row) if row else None


async def get_fine(
    db: AsyncSession, fine_id: UUID, user_id: UUID | None = None, *, for_update: bool = False
) -> Fine | None:
    """With `user_id` only that client's fine is found."""
    query = select(FineRow).where(FineRow.id == fine_id)
    if user_id is not None:
        query = query.where(FineRow.user_id == user_id)
    if for_update:
        query = query.with_for_update()
    row = await db.scalar(query)
    return _to_fine(row) if row else None


@dataclass(frozen=True)
class FineUpdate:
    status: FineStatus
    charge_attempts: int
    next_at: datetime | None
    cancel_reason: str | None = None
    cancelled_by: UUID | None = None
    cancelled_at: datetime | None = None
    refund_status: RefundStatus | None = None
    refund_attempts: int = 0

    @classmethod
    def of(cls, fine: Fine) -> "FineUpdate":
        return cls(
            status=fine.status,
            charge_attempts=fine.charge_attempts,
            next_at=fine.next_at,
            cancel_reason=fine.cancel_reason,
            cancelled_by=fine.cancelled_by,
            cancelled_at=fine.cancelled_at,
            refund_status=fine.refund_status,
            refund_attempts=fine.refund_attempts,
        )


async def update_fine(db: AsyncSession, fine_id: UUID, change: FineUpdate, now: datetime) -> None:
    await db.execute(
        update(FineRow)
        .where(FineRow.id == fine_id)
        .values(
            status=change.status,
            charge_attempts=change.charge_attempts,
            next_at=change.next_at,
            cancel_reason=change.cancel_reason,
            cancelled_by=change.cancelled_by,
            cancelled_at=change.cancelled_at,
            refund_status=change.refund_status,
            refund_attempts=change.refund_attempts,
            updated_at=now,
        )
    )


async def has_unpaid_fine(db: AsyncSession, user_id: UUID) -> bool:
    query = select(FineRow.id).where(
        FineRow.user_id == user_id, FineRow.status == FineStatus.UNPAID
    )
    return await db.scalar(query.limit(1)) is not None


async def unpaid_fines_total(db: AsyncSession, user_id: UUID) -> int:
    query = select(func.coalesce(func.sum(FineRow.amount), 0)).where(
        FineRow.user_id == user_id, FineRow.status == FineStatus.UNPAID
    )
    return int(await db.scalar(query) or 0)


async def lock_due_fines(db: AsyncSession, now: datetime, limit: int) -> list[Fine]:
    """Fines whose charge or refund is due, locked and skipped by a parallel run."""
    rows = await db.scalars(
        select(FineRow)
        .where(FineRow.next_at <= now)
        .order_by(FineRow.next_at, FineRow.id)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    return [_to_fine(row) for row in rows]


async def set_fine_next_at(
    db: AsyncSession, fine_id: UUID, next_at: datetime | None, now: datetime
) -> None:
    await db.execute(
        update(FineRow).where(FineRow.id == fine_id).values(next_at=next_at, updated_at=now)
    )


async def list_fines(
    db: AsyncSession, fine_filter: FineFilter, after: FineCursor | None, limit: int
) -> list[Fine]:
    """Newest first; keyset pages, as with incidents."""
    query = select(FineRow).order_by(FineRow.created_at.desc(), FineRow.id.desc())
    if after is not None:
        query = query.where(
            tuple_(FineRow.created_at, FineRow.id) < tuple_(after.created_at, after.fine_id)
        )
    if fine_filter.statuses:
        query = query.where(FineRow.status.in_(fine_filter.statuses))
    if fine_filter.fine_type_ids:
        query = query.where(FineRow.fine_type_id.in_(fine_filter.fine_type_ids))
    if fine_filter.user_id is not None:
        query = query.where(FineRow.user_id == fine_filter.user_id)
    if fine_filter.rental_id is not None:
        query = query.where(FineRow.rental_id == fine_filter.rental_id)
    if fine_filter.created_from is not None:
        query = query.where(FineRow.created_at >= fine_filter.created_from)
    if fine_filter.created_to is not None:
        query = query.where(FineRow.created_at < fine_filter.created_to)
    return [_to_fine(row) for row in await db.scalars(query.limit(limit))]


async def count_fine_photos(db: AsyncSession, fine_id: UUID) -> int:
    return (
        await db.scalar(
            select(func.count()).select_from(FinePhoto).where(FinePhoto.fine_id == fine_id)
        )
        or 0
    )


async def photos_by_fine(
    db: AsyncSession, fine_ids: Collection[UUID]
) -> dict[UUID, list[FinePhoto]]:
    rows = await db.scalars(
        select(FinePhoto)
        .where(FinePhoto.fine_id.in_(fine_ids))
        .order_by(FinePhoto.uploaded_at, FinePhoto.id)
    )
    grouped: defaultdict[UUID, list[FinePhoto]] = defaultdict(list)
    for row in rows:
        grouped[row.fine_id].append(row)
    return grouped


async def add_fine_photo(
    db: AsyncSession, fine_id: UUID, object_key: str, size_bytes: int, now: datetime
) -> UUID:
    row = FinePhoto(fine_id=fine_id, object_key=object_key, size_bytes=size_bytes, uploaded_at=now)
    db.add(row)
    await db.flush()
    return row.id


async def get_fine_photo(db: AsyncSession, fine_id: UUID, photo_id: UUID) -> FinePhoto | None:
    return await db.scalar(
        select(FinePhoto).where(FinePhoto.id == photo_id, FinePhoto.fine_id == fine_id)
    )


@dataclass(frozen=True)
class NewFineEvent:
    action: FineEventAction
    # None for what the system did on its own.
    staff_id: UUID | None
    at: datetime
    comment: str | None = None


async def add_fine_event(db: AsyncSession, fine_id: UUID, new: NewFineEvent) -> None:
    db.add(
        FineEvent(
            fine_id=fine_id,
            staff_id=new.staff_id,
            action=new.action,
            comment=new.comment,
            created_at=new.at,
        )
    )
    await db.flush()


async def events_by_fine(
    db: AsyncSession, fine_ids: Collection[UUID]
) -> dict[UUID, list[FineEvent]]:
    rows = await db.scalars(
        select(FineEvent).where(FineEvent.fine_id.in_(fine_ids)).order_by(FineEvent.seq)
    )
    grouped: defaultdict[UUID, list[FineEvent]] = defaultdict(list)
    for row in rows:
        grouped[row.fine_id].append(row)
    return grouped


async def count_rental_fines(db: AsyncSession, rental_id: UUID) -> int:
    """Under a transaction lock on the rental: two fines charged at once would otherwise both
    count only themselves, get the same number and the second push be dropped as a repeat."""
    await db.execute(
        select(func.pg_advisory_xact_lock(_RENTAL_FINES_LOCK_CLASS, (rental_id.int >> 96) - 2**31))
    )
    return (
        await db.scalar(
            select(func.count()).select_from(FineRow).where(FineRow.rental_id == rental_id)
        )
        or 0
    )
