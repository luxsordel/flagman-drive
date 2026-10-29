"""Support tables. Rows stay inside the module: the service maps them to schemas."""

from collections import defaultdict
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import case, func, select, text, tuple_, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from flagman.support.domain import (
    OPEN_INCIDENT_STATUSES,
    ConversationStatus,
    IncidentCursor,
    IncidentEventAction,
    IncidentStatus,
    IncidentType,
    MessageAuthor,
    MessageKind,
)
from flagman.support.models import Conversation, Incident, IncidentEvent, IncidentPhoto, Message

# Any fixed number unique among the two-key advisory locks of the database.
_INCIDENTS_LOCK_CLASS = 4_600_201


@dataclass(frozen=True)
class NewMessage:
    author: MessageAuthor
    kind: MessageKind
    at: datetime
    staff_id: UUID | None = None
    body: str | None = None
    object_key: str | None = None


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
