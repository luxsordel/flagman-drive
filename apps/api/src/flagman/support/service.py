"""Support chat and incident reports (#120): what clients and operators may do with them."""

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid4

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from flagman.audit import service as audit
from flagman.audit.domain import (
    Actor,
    ActorType,
    AuditAction,
    AuditEntry,
    AuditTarget,
    AuditTargetType,
    DetailValue,
    RequestOrigin,
)
from flagman.core.photos import (
    OUTPUT_CONTENT_TYPE,
    PhotoTooLargeError,
    PhotoTypeUnsupportedError,
    clean_photo,
)
from flagman.core.ratelimit import Limit, RateLimiter
from flagman.core.storage import ObjectStorage
from flagman.fleet import service as fleet
from flagman.fleet.domain import CarTransition
from flagman.identity import service as identity
from flagman.rentals import service as rentals
from flagman.support import repository
from flagman.support.domain import (
    MAX_INCIDENT_PHOTOS,
    MAX_OPEN_INCIDENTS_PER_CLIENT,
    OPEN_INCIDENT_STATUSES,
    SIGNED_URL_TTL,
    ConversationClosedError,
    ConversationNotFoundError,
    ConversationStatus,
    IncidentClosedError,
    IncidentCursor,
    IncidentEventAction,
    IncidentNotFoundError,
    IncidentPhotoLimitError,
    IncidentPhotoNotFoundError,
    IncidentStatus,
    IncidentTransition,
    IncidentTransitionNotAllowedError,
    IncidentType,
    MessageAuthor,
    MessageKind,
    MessageNotFoundError,
    SupportPhotoTooLargeError,
    SupportPhotoTypeUnsupportedError,
    SupportRentalNotFoundError,
    TooManyOpenIncidentsError,
    apply_incident_transition,
    ensure_conversation_owner,
    ensure_incident_owner,
    is_queue,
)
from flagman.support.models import Conversation, Incident, Message
from flagman.support.schemas import (
    ClientUnreadOut,
    ConversationOut,
    ConversationQueueOut,
    IncidentEventOut,
    IncidentListOut,
    IncidentOut,
    IncidentPhotoOut,
    LocationOut,
    MessageOut,
    MessagePageOut,
    PhotoUrlOut,
    StaffIncidentListOut,
    StaffIncidentOut,
    StaffMessageOut,
    StaffMessagePageOut,
    UnreadOut,
)

logger = structlog.get_logger(__name__)

_HOUR = 3600
# A person types a few messages a minute; more is a script flooding the operators.
MESSAGE_LIMIT = Limit("support-message-user", 20, 60)
# An operator answers several clients at once, but still types every answer.
STAFF_MESSAGE_LIMIT = Limit("support-message-staff", 30, 60)
# A handful of photos of a scratch or a dashboard warning, with retries on a bad network.
UPLOAD_LIMIT = Limit("support-photo-upload-user", 30, _HOUR)
PHOTO_URL_LIMIT = Limit("support-photo-url-user", 120, _HOUR)
INCIDENT_LIMIT = Limit("support-incident-user", 10, _HOUR)
STAFF_PHOTO_URL_LIMIT = Limit("support-photo-url-staff", 600, _HOUR)

_EVENT_ACTIONS = {
    IncidentTransition.TAKE: IncidentEventAction.TAKE,
    IncidentTransition.RESOLVE: IncidentEventAction.RESOLVE,
    IncidentTransition.REJECT: IncidentEventAction.REJECT,
}

# An accident during a trip comes before anything else in the operator's queue.
_URGENT = 0
_NORMAL = 1


@dataclass(frozen=True)
class StaffRef:
    staff_id: UUID
    admin: bool


def _message_out(message: Message, names: dict[UUID, str]) -> MessageOut:
    # The client gets the operator's name only, never which staff account answered.
    return MessageOut(
        id=message.id,
        seq=message.seq,
        author=MessageAuthor(message.author),
        staff_name=names.get(message.staff_id) if message.staff_id else None,
        kind=MessageKind(message.kind),
        text=message.body,
        created_at=message.created_at,
    )


def _staff_message_out(message: Message, names: dict[UUID, str]) -> StaffMessageOut:
    return StaffMessageOut(**_message_out(message, names).model_dump(), staff_id=message.staff_id)


async def _staff_names(db: AsyncSession, messages: Sequence[Message]) -> dict[UUID, str]:
    """One query for a whole page of messages."""
    return await identity.get_staff_display_names(
        db, {message.staff_id for message in messages if message.staff_id is not None}
    )


def _next_cursor(page: Sequence[Incident], fetched: int, limit: int) -> str | None:
    if fetched <= limit:
        return None
    last = page[-1]
    return IncidentCursor(last.priority, last.created_at, last.id).encode()


def _seen(items: Sequence[Message], after: int) -> int:
    """The last message the reader got: a page cut by `limit` leaves the rest unread."""
    return items[-1].seq if items else after


def _location(incident: Incident) -> LocationOut | None:
    if incident.lat is None or incident.lon is None:
        return None
    return LocationOut(lat=incident.lat, lon=incident.lon)


async def _incidents_out(db: AsyncSession, incidents: Sequence[Incident]) -> list[IncidentOut]:
    photos = await repository.photos_by_incident(db, [incident.id for incident in incidents])
    return [
        IncidentOut(
            id=incident.id,
            type=IncidentType(incident.type),
            status=IncidentStatus(incident.status),
            description=incident.description,
            location=_location(incident),
            rental_id=incident.rental_id,
            car_id=incident.car_id,
            photos=[
                IncidentPhotoOut(id=photo.id, uploaded_at=photo.uploaded_at)
                for photo in photos.get(incident.id, [])
            ],
            created_at=incident.created_at,
            updated_at=incident.updated_at,
        )
        for incident in incidents
    ]


async def _incident_out(db: AsyncSession, incident: Incident) -> IncidentOut:
    return (await _incidents_out(db, [incident]))[0]


async def _clean(data: bytes) -> bytes:
    try:
        return await asyncio.to_thread(clean_photo, data)
    except PhotoTooLargeError as error:
        raise SupportPhotoTooLargeError(str(error) or None) from error
    except PhotoTypeUnsupportedError as error:
        raise SupportPhotoTypeUnsupportedError from error


async def _delete_quietly(storage: ObjectStorage, key: str) -> None:
    try:
        await storage.delete(key)
    except Exception:
        # An orphan photo is a storage cost, not a reason to fail the request.
        logger.warning("support_photo_orphaned")


async def _signed(storage: ObjectStorage, key: str) -> PhotoUrlOut:
    url = await storage.signed_get_url(key, SIGNED_URL_TTL)
    return PhotoUrlOut(url=url, expires_in=int(SIGNED_URL_TTL.total_seconds()))


@dataclass(frozen=True)
class ClientSupportService:
    db: AsyncSession
    limiter: RateLimiter

    async def messages(self, user_id: UUID, after: int, limit: int) -> MessagePageOut:
        """The latest conversation from `after` on; what it shows no longer counts as unread."""
        async with self.db.begin():
            conversation = await repository.get_latest_conversation(self.db, user_id)
            if conversation is None:
                return MessagePageOut(conversation_id=None, status=None, items=[])
            items = await repository.list_messages(self.db, conversation.id, after, limit)
            if conversation.client_unread:
                await repository.mark_read(
                    self.db, conversation.id, MessageAuthor.CLIENT, _seen(items, after)
                )
            names = await _staff_names(self.db, items)
        return MessagePageOut(
            conversation_id=conversation.id,
            status=ConversationStatus(conversation.status),
            items=[_message_out(item, names) for item in items],
        )

    async def unread(self, user_id: UUID) -> ClientUnreadOut:
        async with self.db.begin():
            conversation = await repository.get_latest_conversation(self.db, user_id)
        return ClientUnreadOut(messages=conversation.client_unread if conversation else 0)

    async def _open(self, user_id: UUID, rental_id: UUID | None, now: datetime) -> Conversation:
        if (
            rental_id is not None
            and await rentals.get_client_rental(self.db, user_id, rental_id) is None
        ):
            raise SupportRentalNotFoundError
        conversation = await repository.open_conversation(self.db, user_id, rental_id, now)
        if rental_id is not None and conversation.rental_id != rental_id:
            # The client writes from a trip screen: the operator needs that rental, not an older
            # one or none. The row is already locked by open_conversation.
            conversation.rental_id = rental_id
        return conversation

    async def send_text(
        self, user_id: UUID, text: str, rental_id: UUID | None, now: datetime
    ) -> MessageOut:
        await self.limiter.hit(MESSAGE_LIMIT, str(user_id))
        async with self.db.begin():
            conversation = await self._open(user_id, rental_id, now)
            message = await repository.add_message(
                self.db,
                conversation,
                repository.NewMessage(
                    author=MessageAuthor.CLIENT, kind=MessageKind.TEXT, at=now, body=text
                ),
            )
            out = _message_out(message, {})
        logger.info("support_message_sent", conversation_id=str(conversation.id))
        return out

    async def count_upload(self, user_id: UUID) -> None:
        """Counted before the body is read, so an exhausted limit does not cost 10 MB each time."""
        await self.limiter.hit(UPLOAD_LIMIT, str(user_id))

    async def send_photo(
        self, storage: ObjectStorage, user_id: UUID, data: bytes, now: datetime
    ) -> MessageOut:
        photo = await _clean(data)
        # A new key for every photo, without anything about the client in it. The conversation
        # is opened only after the upload, so a failed upload leaves no empty one in the queue.
        key = f"support/messages/{uuid4()}.jpg"
        await storage.put(key, photo, OUTPUT_CONTENT_TYPE)
        try:
            async with self.db.begin():
                conversation = await self._open(user_id, None, now)
                message = await repository.add_message(
                    self.db,
                    conversation,
                    repository.NewMessage(
                        author=MessageAuthor.CLIENT, kind=MessageKind.PHOTO, at=now, object_key=key
                    ),
                )
                out = _message_out(message, {})
        except Exception:
            await _delete_quietly(storage, key)
            raise
        logger.info("support_photo_sent", conversation_id=str(conversation.id))
        return out

    async def photo_url(
        self, storage: ObjectStorage, user_id: UUID, message_id: UUID
    ) -> PhotoUrlOut:
        await self.limiter.hit(PHOTO_URL_LIMIT, str(user_id))
        async with self.db.begin():
            message = await repository.get_client_message(self.db, user_id, message_id)
        if message is None or message.object_key is None:
            raise MessageNotFoundError
        return await _signed(storage, message.object_key)

    async def report_incident(
        self,
        user_id: UUID,
        type_: IncidentType,
        description: str,
        location: tuple[float, float] | None,
        now: datetime,
    ) -> IncidentOut:
        await self.limiter.hit(INCIDENT_LIMIT, str(user_id))
        async with self.db.begin():
            await repository.lock_client_incidents(self.db, user_id)
            if await repository.count_open_incidents(self.db, user_id) >= (
                MAX_OPEN_INCIDENTS_PER_CLIENT
            ):
                raise TooManyOpenIncidentsError
            # The rental is found by the server: the client cannot attach someone else's car.
            rental = await rentals.get_client_active_rental(self.db, user_id, now)
            # Before the trip nobody is on the road: an accident by a reserved car waits its turn.
            urgent = type_ is IncidentType.ACCIDENT and rental is not None and rental.is_driving
            incident = await repository.create_incident(
                self.db,
                repository.NewIncident(
                    user_id=user_id,
                    rental_id=rental.id if rental else None,
                    car_id=rental.car_id if rental else None,
                    type=type_,
                    priority=_URGENT if urgent else _NORMAL,
                    description=description,
                    location=location,
                    at=now,
                ),
            )
            out = await _incident_out(self.db, incident)
        logger.info("support_incident_reported", incident_id=str(incident.id), type=type_.value)
        return out

    async def incidents(self, user_id: UUID, cursor: str | None, limit: int) -> IncidentListOut:
        after = IncidentCursor.decode(cursor) if cursor is not None else None
        async with self.db.begin():
            # One extra row tells whether there is a next page without a separate count.
            fetched = await repository.list_incidents(
                self.db, repository.IncidentFilter(user_id=user_id), after, limit + 1
            )
            page = fetched[:limit]
            return IncidentListOut(
                items=await _incidents_out(self.db, page),
                next_cursor=_next_cursor(page, len(fetched), limit),
            )

    async def incident(self, user_id: UUID, incident_id: UUID) -> IncidentOut:
        async with self.db.begin():
            incident = await repository.get_incident(self.db, incident_id, user_id=user_id)
            if incident is None:
                raise IncidentNotFoundError
            return await _incident_out(self.db, incident)

    async def _lock_for_photo(self, user_id: UUID, incident_id: UUID) -> Incident:
        incident = await repository.get_incident(
            self.db, incident_id, user_id=user_id, for_update=True
        )
        if incident is None:
            raise IncidentNotFoundError
        if IncidentStatus(incident.status) not in OPEN_INCIDENT_STATUSES:
            raise IncidentClosedError
        if await repository.count_incident_photos(self.db, incident.id) >= MAX_INCIDENT_PHOTOS:
            raise IncidentPhotoLimitError
        return incident

    async def add_incident_photo(
        self, storage: ObjectStorage, user_id: UUID, incident_id: UUID, data: bytes, now: datetime
    ) -> IncidentOut:
        async with self.db.begin():
            # Checked before the photo is decoded and stored, and again under the lock below.
            await self._lock_for_photo(user_id, incident_id)
        photo = await _clean(data)
        key = f"incidents/{incident_id}/{uuid4()}.jpg"
        await storage.put(key, photo, OUTPUT_CONTENT_TYPE)
        try:
            async with self.db.begin():
                incident = await self._lock_for_photo(user_id, incident_id)
                await repository.add_incident_photo(self.db, incident.id, key, len(photo), now)
                incident.updated_at = now
                out = await _incident_out(self.db, incident)
        except Exception:
            await _delete_quietly(storage, key)
            raise
        return out

    async def incident_photo_url(
        self, storage: ObjectStorage, user_id: UUID, incident_id: UUID, photo_id: UUID
    ) -> PhotoUrlOut:
        await self.limiter.hit(PHOTO_URL_LIMIT, str(user_id))
        async with self.db.begin():
            incident = await repository.get_incident(self.db, incident_id, user_id=user_id)
            if incident is None:
                raise IncidentNotFoundError
            photo = await repository.get_incident_photo(self.db, incident.id, photo_id)
        if photo is None:
            raise IncidentPhotoNotFoundError
        return await _signed(storage, photo.object_key)


@dataclass(frozen=True)
class StaffSupportService:
    db: AsyncSession
    limiter: RateLimiter
    origin: RequestOrigin

    async def _audit(
        self,
        staff_id: UUID,
        action: AuditAction,
        target: AuditTarget,
        details: dict[str, DetailValue] | None = None,
    ) -> None:
        await audit.record(
            self.db,
            AuditEntry(
                Actor(ActorType.STAFF, staff_id), action, self.origin, target, details or {}
            ),
        )

    async def _conversations_out(
        self, conversations: Sequence[Conversation]
    ) -> list[ConversationOut]:
        """A fixed number of queries per page: the admin panel polls the whole queue."""
        people = await identity.get_client_identities(
            self.db, {item.user_id for item in conversations}
        )
        attached = await rentals.get_rentals(
            self.db, {item.rental_id for item in conversations if item.rental_id is not None}
        )
        car_ids = {rental.car_id for rental in attached.values()}
        cars = {car.id: car for car in await fleet.get_cars(self.db, car_ids)} if car_ids else {}
        items = []
        for item in conversations:
            person = people.get(item.user_id)
            rental = attached.get(item.rental_id) if item.rental_id else None
            car = cars.get(rental.car_id) if rental else None
            items.append(
                ConversationOut(
                    id=item.id,
                    user_id=item.user_id,
                    rental_id=item.rental_id,
                    rental_status=rental.status if rental else None,
                    car_id=rental.car_id if rental else None,
                    car_plate=car.plate if car else None,
                    status=ConversationStatus(item.status),
                    assigned_staff_id=item.assigned_staff_id,
                    unread=item.staff_unread,
                    first_name=person.first_name if person else None,
                    last_name=person.last_name if person else None,
                    created_at=item.created_at,
                    last_message_at=item.last_message_at,
                )
            )
        return items

    async def queue(self, limit: int) -> ConversationQueueOut:
        async with self.db.begin():
            conversations = await repository.list_queue(self.db, limit)
            return ConversationQueueOut(items=await self._conversations_out(conversations))

    async def unread(self) -> UnreadOut:
        async with self.db.begin():
            return UnreadOut(
                conversations=await repository.count_unread_conversations(self.db),
                incidents=await repository.count_new_incidents(self.db),
            )

    async def conversation(self, conversation_id: UUID) -> ConversationOut:
        async with self.db.begin():
            conversation = await repository.get_conversation(self.db, conversation_id)
            if conversation is None:
                raise ConversationNotFoundError
            return (await self._conversations_out([conversation]))[0]

    async def messages(self, conversation_id: UUID, after: int, limit: int) -> StaffMessagePageOut:
        """What a staff member is shown no longer counts as unread, for every staff member."""
        async with self.db.begin():
            conversation = await repository.get_conversation(self.db, conversation_id)
            if conversation is None:
                raise ConversationNotFoundError
            items = await repository.list_messages(self.db, conversation.id, after, limit)
            if conversation.staff_unread:
                await repository.mark_read(
                    self.db, conversation.id, MessageAuthor.STAFF, _seen(items, after)
                )
            names = await _staff_names(self.db, items)
        return StaffMessagePageOut(
            conversation_id=conversation.id,
            status=ConversationStatus(conversation.status),
            items=[_staff_message_out(item, names) for item in items],
        )

    async def _lock_open(self, staff: StaffRef, conversation_id: UUID) -> Conversation:
        conversation = await repository.get_conversation(self.db, conversation_id, for_update=True)
        if conversation is None:
            raise ConversationNotFoundError
        if conversation.status != ConversationStatus.OPEN:
            raise ConversationClosedError
        ensure_conversation_owner(conversation.assigned_staff_id, staff.staff_id, admin=staff.admin)
        return conversation

    def _target(self, conversation_id: UUID) -> AuditTarget:
        return AuditTarget(AuditTargetType.SUPPORT_CONVERSATION, conversation_id)

    async def take(self, staff: StaffRef, conversation_id: UUID) -> ConversationOut:
        async with self.db.begin():
            conversation = await self._lock_open(staff, conversation_id)
            if conversation.assigned_staff_id != staff.staff_id:
                conversation.assigned_staff_id = staff.staff_id
                await self._audit(
                    staff.staff_id,
                    AuditAction.SUPPORT_CONVERSATION_TAKEN,
                    self._target(conversation.id),
                )
            return (await self._conversations_out([conversation]))[0]

    async def reply(
        self, staff: StaffRef, conversation_id: UUID, text: str, seen_seq: int, now: datetime
    ) -> StaffMessageOut:
        await self.limiter.hit(STAFF_MESSAGE_LIMIT, str(staff.staff_id))
        async with self.db.begin():
            conversation = await self._lock_open(staff, conversation_id)
            # Answering takes the conversation: two operators do not reply to one client.
            conversation.assigned_staff_id = staff.staff_id
            message = await repository.add_message(
                self.db,
                conversation,
                repository.NewMessage(
                    author=MessageAuthor.STAFF,
                    kind=MessageKind.TEXT,
                    at=now,
                    staff_id=staff.staff_id,
                    body=text,
                ),
            )
            # A client message that came after the operator's last poll stays unread: they
            # answered without having seen it.
            await repository.mark_read(self.db, conversation.id, MessageAuthor.STAFF, seen_seq)
            await self._audit(
                staff.staff_id,
                AuditAction.SUPPORT_MESSAGE_SENT,
                self._target(conversation.id),
                {"message_id": message.id},
            )
            return _staff_message_out(message, await _staff_names(self.db, [message]))

    async def close(self, staff: StaffRef, conversation_id: UUID, now: datetime) -> ConversationOut:
        async with self.db.begin():
            conversation = await self._lock_open(staff, conversation_id)
            conversation.status = ConversationStatus.CLOSED
            conversation.closed_at = now
            conversation.staff_unread = 0
            await self._audit(
                staff.staff_id,
                AuditAction.SUPPORT_CONVERSATION_CLOSED,
                self._target(conversation.id),
            )
            return (await self._conversations_out([conversation]))[0]

    async def photo_url(
        self, storage: ObjectStorage, staff_id: UUID, conversation_id: UUID, message_id: UUID
    ) -> PhotoUrlOut:
        await self.limiter.hit(STAFF_PHOTO_URL_LIMIT, str(staff_id))
        async with self.db.begin():
            message = await repository.get_message(self.db, conversation_id, message_id)
            if message is None or message.object_key is None:
                raise MessageNotFoundError
            # Who looked at whose photos; recorded before the link exists.
            await self._audit(
                staff_id,
                AuditAction.SUPPORT_PHOTO_VIEWED,
                self._target(conversation_id),
                {"message_id": message.id},
            )
        return await _signed(storage, message.object_key)

    async def _staff_incidents_out(self, incidents: Sequence[Incident]) -> list[StaffIncidentOut]:
        """A fixed number of queries per page: the admin panel polls the whole list."""
        bases = await _incidents_out(self.db, incidents)
        people = await identity.get_client_identities(
            self.db, {incident.user_id for incident in incidents}
        )
        events = await repository.events_by_incident(
            self.db, [incident.id for incident in incidents]
        )
        items = []
        for incident, base in zip(incidents, bases, strict=True):
            person = people.get(incident.user_id)
            items.append(
                StaffIncidentOut(
                    **base.model_dump(),
                    user_id=incident.user_id,
                    first_name=person.first_name if person else None,
                    last_name=person.last_name if person else None,
                    priority=incident.priority,
                    assigned_staff_id=incident.assigned_staff_id,
                    sent_to_service=incident.sent_to_service,
                    events=[
                        IncidentEventOut(
                            action=IncidentEventAction(event.action),
                            staff_id=event.staff_id,
                            comment=event.comment,
                            created_at=event.created_at,
                        )
                        for event in events.get(incident.id, [])
                    ],
                )
            )
        return items

    async def _staff_incident_out(self, incident: Incident) -> StaffIncidentOut:
        return (await self._staff_incidents_out([incident]))[0]

    async def incidents(
        self,
        statuses: frozenset[IncidentStatus] | None,
        type_: IncidentType | None,
        cursor: str | None,
        limit: int,
    ) -> StaffIncidentListOut:
        after = IncidentCursor.decode(cursor) if cursor is not None else None
        async with self.db.begin():
            incident_filter = repository.IncidentFilter(
                statuses=statuses, type=type_, queue=is_queue(statuses)
            )
            fetched = await repository.list_incidents(self.db, incident_filter, after, limit + 1)
            page = fetched[:limit]
            return StaffIncidentListOut(
                items=await self._staff_incidents_out(page),
                next_cursor=_next_cursor(page, len(fetched), limit),
            )

    async def incident(self, incident_id: UUID) -> StaffIncidentOut:
        async with self.db.begin():
            incident = await repository.get_incident(self.db, incident_id)
            if incident is None:
                raise IncidentNotFoundError
            return await self._staff_incident_out(incident)

    async def _lock_incident(self, incident_id: UUID) -> Incident:
        incident = await repository.get_incident(self.db, incident_id, for_update=True)
        if incident is None:
            raise IncidentNotFoundError
        return incident

    def _incident_target(self, incident_id: UUID) -> AuditTarget:
        return AuditTarget(AuditTargetType.INCIDENT, incident_id)

    async def change_incident(
        self,
        staff: StaffRef,
        incident_id: UUID,
        transition: IncidentTransition,
        comment: str | None,
        now: datetime,
    ) -> StaffIncidentOut:
        actions = {
            IncidentTransition.TAKE: AuditAction.INCIDENT_TAKEN,
            IncidentTransition.RESOLVE: AuditAction.INCIDENT_RESOLVED,
            IncidentTransition.REJECT: AuditAction.INCIDENT_REJECTED,
        }
        async with self.db.begin():
            incident = await self._lock_incident(incident_id)
            if transition is not IncidentTransition.TAKE:
                ensure_incident_owner(incident.assigned_staff_id, staff.staff_id, admin=staff.admin)
            status = apply_incident_transition(IncidentStatus(incident.status), transition, comment)
            incident.status = status
            incident.updated_at = now
            if transition is IncidentTransition.TAKE:
                incident.assigned_staff_id = staff.staff_id
            await repository.add_incident_event(
                self.db,
                incident.id,
                repository.NewIncidentEvent(
                    staff.staff_id, _EVENT_ACTIONS[transition], now, comment
                ),
            )
            await self._audit(
                staff.staff_id,
                actions[transition],
                self._incident_target(incident.id),
                {"status": status.value},
            )
            return await self._staff_incident_out(incident)

    async def send_car_to_service(
        self, staff: StaffRef, incident_id: UUID, now: datetime
    ) -> StaffIncidentOut:
        """The fleet refuses a car still held by a rental: the rental ends first (stage 3)."""
        async with self.db.begin():
            incident = await self._lock_incident(incident_id)
            if incident.car_id is None or incident.sent_to_service:
                raise IncidentTransitionNotAllowedError
            if IncidentStatus(incident.status) is not IncidentStatus.IN_PROGRESS:
                raise IncidentTransitionNotAllowedError
            ensure_incident_owner(incident.assigned_staff_id, staff.staff_id, admin=staff.admin)
            await fleet.change_status(self.db, incident.car_id, CarTransition.SEND_TO_SERVICE, now)
            incident.sent_to_service = True
            incident.updated_at = now
            await repository.add_incident_event(
                self.db,
                incident.id,
                repository.NewIncidentEvent(
                    staff.staff_id, IncidentEventAction.SENT_TO_SERVICE, now, None
                ),
            )
            await self._audit(
                staff.staff_id,
                AuditAction.INCIDENT_CAR_SENT_TO_SERVICE,
                self._incident_target(incident.id),
                {"car_id": incident.car_id},
            )
            return await self._staff_incident_out(incident)

    async def incident_photo_url(
        self, storage: ObjectStorage, staff_id: UUID, incident_id: UUID, photo_id: UUID
    ) -> PhotoUrlOut:
        await self.limiter.hit(STAFF_PHOTO_URL_LIMIT, str(staff_id))
        async with self.db.begin():
            photo = await repository.get_incident_photo(self.db, incident_id, photo_id)
            if photo is None:
                raise IncidentPhotoNotFoundError
            await self._audit(
                staff_id,
                AuditAction.INCIDENT_PHOTO_VIEWED,
                self._incident_target(incident_id),
                {"photo_id": photo.id},
            )
        return await _signed(storage, photo.object_key)
