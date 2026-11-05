"""Support chat, incident reports (#120) and fines (#121): what clients and operators may do."""

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from uuid import UUID, uuid4

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from flagman.audit import service as audit
from flagman.audit.domain import (
    SYSTEM,
    Actor,
    ActorType,
    AuditAction,
    AuditEntry,
    AuditTarget,
    AuditTargetType,
    DetailValue,
    RequestOrigin,
)
from flagman.core.config import TripSettings
from flagman.core.photos import (
    OUTPUT_CONTENT_TYPE,
    PhotoTooLargeError,
    PhotoTypeUnsupportedError,
    clean_photo,
)
from flagman.core.ratelimit import Limit, RateLimiter
from flagman.core.storage import ObjectStorage
from flagman.fleet import service as fleet
from flagman.fleet.domain import CarStatus, CarTransition
from flagman.identity import service as identity
from flagman.notifications import service as notifications
from flagman.notifications.domain import Notification, NotificationEvent
from flagman.payments import service as payments
from flagman.payments.domain import (
    IdempotencyKeyReusedError,
    NoUsableCardError,
    OperationStatus,
    PaymentOperation,
    PaymentsUnavailableError,
)
from flagman.pricing import service as pricing
from flagman.pricing.domain import LOW_BATTERY_FINE_CODE, FineKind
from flagman.rentals import service as rentals
from flagman.rentals.domain import Rental, next_charge_attempt
from flagman.support import repository
from flagman.support.domain import (
    FINE_PAGE_SIZE_MAX,
    MAX_FINE_PHOTOS,
    MAX_INCIDENT_PHOTOS,
    MAX_OPEN_INCIDENTS_PER_CLIENT,
    OPEN_INCIDENT_STATUSES,
    OWED_FINE_STATUSES,
    SIGNED_URL_TTL,
    ConversationClosedError,
    ConversationNotFoundError,
    ConversationStatus,
    Fine,
    FineCursor,
    FineEventAction,
    FineFilter,
    FineNotFoundError,
    FinePhotoLimitError,
    FinePhotoNotFoundError,
    FineRentalNotFinishedError,
    FineStatus,
    FineTransition,
    FineTransitionNotAllowedError,
    FineTypeNotManualError,
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
    RefundStatus,
    SupportPhotoTooLargeError,
    SupportPhotoTypeUnsupportedError,
    SupportRentalNotFoundError,
    TooManyOpenIncidentsError,
    apply_fine_transition,
    apply_incident_transition,
    ensure_conversation_owner,
    ensure_incident_owner,
    ensure_reason,
    fine_charge_key,
    fine_refund_key,
    is_low_battery_end,
    is_queue,
)
from flagman.support.models import Conversation, FineEvent, Incident, Message
from flagman.support.schemas import (
    ClientUnreadOut,
    ConversationOut,
    ConversationQueueOut,
    FineEventOut,
    FineListOut,
    FineOut,
    FinePhotoOut,
    IncidentEventOut,
    IncidentListOut,
    IncidentOut,
    IncidentPhotoOut,
    LocationOut,
    MessageOut,
    MessagePageOut,
    PhotoUrlOut,
    StaffFineListOut,
    StaffFineOut,
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
# A shift of fines with every photo they may hold; more is a stolen session filling the storage.
STAFF_UPLOAD_LIMIT = Limit("support-fine-photo-upload-staff", 60, _HOUR)

_EVENT_ACTIONS = {
    IncidentTransition.TAKE: IncidentEventAction.TAKE,
    IncidentTransition.RESOLVE: IncidentEventAction.RESOLVE,
    IncidentTransition.REJECT: IncidentEventAction.REJECT,
}

# An accident during a trip comes before anything else in the operator's queue.
_URGENT = 0
_NORMAL = 1


async def car_held_by_incident(db: AsyncSession, car_id: UUID) -> bool:
    """Whether an open incident took the car off the road; in the caller's transaction."""
    return await repository.car_held_by_incident(db, car_id)


@dataclass(frozen=True)
class PhotoTarget:
    rental_id: UUID | None = None
    client_message_id: UUID | None = None


@dataclass(frozen=True)
class IncidentReport:
    type: IncidentType
    description: str
    location: tuple[float, float] | None
    rental_id: UUID | None
    client_report_id: UUID | None = None


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

    async def _resent(self, user_id: UUID, client_message_id: UUID | None) -> MessageOut | None:
        if client_message_id is None:
            return None
        message = await repository.find_resent_message(self.db, user_id, client_message_id)
        return None if message is None else _message_out(message, {})

    async def send_text(
        self,
        user_id: UUID,
        text: str,
        rental_id: UUID | None,
        client_message_id: UUID | None,
        now: datetime,
    ) -> MessageOut:
        await self.limiter.hit(MESSAGE_LIMIT, str(user_id))
        async with self.db.begin():
            # Before opening: a resend must not open an empty conversation after a close.
            if (resent := await self._resent(user_id, client_message_id)) is not None:
                return resent
            conversation = await self._open(user_id, rental_id, now)
            # Again under the conversation's lock, for two attempts sent at once.
            if (resent := await self._resent(user_id, client_message_id)) is not None:
                return resent
            message = await repository.add_message(
                self.db,
                conversation,
                repository.NewMessage(
                    author=MessageAuthor.CLIENT,
                    kind=MessageKind.TEXT,
                    at=now,
                    body=text,
                    client_message_id=client_message_id,
                ),
            )
            out = _message_out(message, {})
        logger.info("support_message_sent", conversation_id=str(conversation.id))
        return out

    async def count_upload(self, user_id: UUID) -> None:
        """Counted before the body is read, so an exhausted limit does not cost 10 MB each time."""
        await self.limiter.hit(UPLOAD_LIMIT, str(user_id))

    async def send_photo(
        self,
        storage: ObjectStorage,
        user_id: UUID,
        data: bytes,
        target: PhotoTarget,
        now: datetime,
    ) -> MessageOut:
        async with self.db.begin():
            resent = await self._resent(user_id, target.client_message_id)
        if resent is not None:
            return resent
        photo = await _clean(data)
        # A new key for every photo, without anything about the client in it. The conversation
        # is opened only after the upload, so a failed upload leaves no empty one in the queue.
        key = f"support/messages/{uuid4()}.jpg"
        await storage.put(key, photo, OUTPUT_CONTENT_TYPE)
        try:
            async with self.db.begin():
                conversation = await self._open(user_id, target.rental_id, now)
                resent = await self._resent(user_id, target.client_message_id)
                if resent is not None:
                    await _delete_quietly(storage, key)
                    return resent
                message = await repository.add_message(
                    self.db,
                    conversation,
                    repository.NewMessage(
                        author=MessageAuthor.CLIENT,
                        kind=MessageKind.PHOTO,
                        at=now,
                        object_key=key,
                        client_message_id=target.client_message_id,
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
        self, user_id: UUID, report: IncidentReport, now: datetime
    ) -> IncidentOut:
        await self.limiter.hit(INCIDENT_LIMIT, str(user_id))
        async with self.db.begin():
            await repository.lock_client_incidents(self.db, user_id)
            # Before the count: the report may be the very one that reached the limit.
            if report.client_report_id is not None:
                resent = await repository.find_resent_incident(
                    self.db, user_id, report.client_report_id
                )
                if resent is not None:
                    return await _incident_out(self.db, resent)
            if await repository.count_open_incidents(self.db, user_id) >= (
                MAX_OPEN_INCIDENTS_PER_CLIENT
            ):
                raise TooManyOpenIncidentsError
            # The rental is found by the server: the client cannot attach someone else's car.
            rental = await rentals.get_client_active_rental(self.db, user_id, now)
            if rental is None and report.rental_id is not None:
                # From a receipt: only the client's own rental, whichever id the app sent.
                rental = await rentals.get_client_rental(self.db, user_id, report.rental_id)
                if rental is None:
                    raise SupportRentalNotFoundError
            # Before the trip nobody is on the road: an accident by a reserved car waits its turn.
            urgent = (
                report.type is IncidentType.ACCIDENT and rental is not None and rental.is_driving
            )
            incident = await repository.create_incident(
                self.db,
                repository.NewIncident(
                    user_id=user_id,
                    rental_id=rental.id if rental else None,
                    car_id=rental.car_id if rental else None,
                    type=report.type,
                    priority=_URGENT if urgent else _NORMAL,
                    description=report.description,
                    location=report.location,
                    at=now,
                    client_report_id=report.client_report_id,
                ),
            )
            out = await _incident_out(self.db, incident)
        logger.info(
            "support_incident_reported", incident_id=str(incident.id), type=report.type.value
        )
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
        names = await identity.get_staff_display_names(
            self.db,
            {item.assigned_staff_id for item in conversations if item.assigned_staff_id},
        )
        last_messages = await repository.last_messages(self.db, [item.id for item in conversations])
        items = []
        for item in conversations:
            person = people.get(item.user_id)
            rental = attached.get(item.rental_id) if item.rental_id else None
            car = cars.get(rental.car_id) if rental else None
            last = last_messages.get(item.id)
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
                    assigned_staff_name=(
                        names.get(item.assigned_staff_id) if item.assigned_staff_id else None
                    ),
                    unread=item.staff_unread,
                    first_name=person.first_name if person else None,
                    last_name=person.last_name if person else None,
                    last_message_text=last.body if last else None,
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
            await notifications.notify_client(
                self.db,
                conversation.user_id,
                Notification(
                    NotificationEvent.SUPPORT_REPLY, conversation.rental_id, {"seq": message.seq}
                ),
                now,
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
        staff_ids = {item.assigned_staff_id for item in incidents if item.assigned_staff_id}
        staff_ids |= {
            event.staff_id for page in events.values() for event in page if event.staff_id
        }
        names = await identity.get_staff_display_names(self.db, staff_ids)
        attached = await rentals.get_rentals(
            self.db, {item.rental_id for item in incidents if item.rental_id}
        )
        car_ids = {item.car_id for item in incidents if item.car_id}
        cars = {car.id: car for car in await fleet.get_cars(self.db, car_ids)} if car_ids else {}
        items = []
        for incident, base in zip(incidents, bases, strict=True):
            person = people.get(incident.user_id)
            rental = attached.get(incident.rental_id) if incident.rental_id else None
            car = cars.get(incident.car_id) if incident.car_id else None
            items.append(
                StaffIncidentOut(
                    **base.model_dump(),
                    user_id=incident.user_id,
                    first_name=person.first_name if person else None,
                    last_name=person.last_name if person else None,
                    priority=incident.priority,
                    assigned_staff_id=incident.assigned_staff_id,
                    assigned_staff_name=(
                        names.get(incident.assigned_staff_id)
                        if incident.assigned_staff_id
                        else None
                    ),
                    rental_status=rental.status if rental else None,
                    car_plate=car.plate if car else None,
                    sent_to_service=incident.sent_to_service,
                    events=[
                        IncidentEventOut(
                            action=IncidentEventAction(event.action),
                            staff_id=event.staff_id,
                            staff_name=names.get(event.staff_id) if event.staff_id else None,
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
            # Locked first: a task ending on this car decides by the flag set here. A car already
            # in service is on a task, and without the flag that task would free it.
            car = await fleet.lock_car(self.db, incident.car_id)
            if car.status is not CarStatus.SERVICE:
                await fleet.change_status(self.db, car.id, CarTransition.SEND_TO_SERVICE, now)
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


# Fines (#121, docs/adr/0028): charged by the system at the end of a trip or by an operator,
# taken from the card like a rental's debt, cancelled with a refund.

PAY_FINE_LIMIT = Limit("support-fine-pay-user", 10, _HOUR)
_FINE_BATCH = 50


@dataclass(frozen=True)
class FineRules:
    # Below this battery at the end of a client's trip the automatic fine is charged; 0 is off.
    low_battery_pct: int
    # The retries of a declined charge are the rentals' (docs/adr/0024).
    settlement: rentals.SettlementRules


def fine_rules(settings: TripSettings) -> FineRules:
    return FineRules(
        low_battery_pct=settings.fine_low_battery_pct,
        settlement=rentals.settlement_rules(settings),
    )


@dataclass(frozen=True)
class TripFines:
    """The fines as rentals see them (rentals.ports.Fines); both run in the rental's transaction."""

    rules: FineRules

    async def has_unpaid(self, db: AsyncSession, user_id: UUID) -> bool:
        return await repository.has_unpaid_fine(db, user_id)

    async def trip_finished(self, db: AsyncSession, rental: Rental, now: datetime) -> None:
        if not is_low_battery_end(rental.end_battery_pct, self.rules.low_battery_pct):
            return
        version = await pricing.current_fine(db, LOW_BATTERY_FINE_CODE)
        if version is None:
            # The catalog lost the type: no amount to charge, and the trip must still end.
            logger.warning("fine_type_missing", code=LOW_BATTERY_FINE_CODE)
            return
        fine = await repository.add_fine(
            db,
            repository.NewFine(
                rental_id=rental.id,
                user_id=rental.user_id,
                fine_type_id=version.fine_type_id,
                fine_type_version_id=version.id,
                amount=version.terms.amount,
                comment=None,
                created_by=None,
                at=now,
            ),
        )
        if fine is not None:
            await _charged(db, fine, RequestOrigin(), now)
            logger.info("fine_charged", fine_id=str(fine.id), automatic=True)


async def _charged(db: AsyncSession, fine: Fine, origin: RequestOrigin, now: datetime) -> None:
    """The history, the audit and the client's push of a new fine; in its transaction."""
    await repository.add_fine_event(
        db,
        fine.id,
        repository.NewFineEvent(FineEventAction.CHARGED, fine.created_by, now, fine.comment),
    )
    actor = SYSTEM if fine.created_by is None else Actor(ActorType.STAFF, fine.created_by)
    await audit.record(
        db,
        AuditEntry(
            actor,
            AuditAction.FINE_CHARGED,
            origin,
            AuditTarget(AuditTargetType.FINE, fine.id),
            details={
                "rental_id": fine.rental_id,
                "fine_type_version_id": fine.fine_type_version_id,
                "amount": fine.amount,
            },
        ),
    )
    number = await repository.count_rental_fines(db, fine.rental_id)
    notification = Notification(
        NotificationEvent.FINE_CHARGED, fine.rental_id, {"amount": fine.amount, "number": number}
    )
    await notifications.notify_client(db, fine.user_id, notification, now)


@dataclass(frozen=True)
class _Charged:
    # None while the provider's answer is not known.
    status: FineStatus | None
    attempts: int


async def _try_charge(
    db: AsyncSession, gw: payments.Gateway, fine: Fine, now: datetime
) -> _Charged:
    attempt = fine.charge_attempts
    key = fine_charge_key(fine.id, attempt)
    last = await payments.find_operation(db, key) if attempt else None
    if last is not None and last.status is OperationStatus.SUCCEEDED:
        return _Charged(FineStatus.PAID, attempt)
    # An unknown outcome goes again with its own key: the provider never takes it twice. A
    # declined key is spent, the next try gets the next one.
    if last is None or last.is_final:
        attempt += 1
    payment = payments.Payment(fine.amount, fine_charge_key(fine.id, attempt))
    try:
        operation = await payments.charge(db, gw, fine.user_id, payment, now)
    except NoUsableCardError:
        return _Charged(FineStatus.UNPAID, attempt)
    if operation.status is OperationStatus.SUCCEEDED:
        return _Charged(FineStatus.PAID, attempt)
    if operation.status is OperationStatus.DECLINED:
        return _Charged(FineStatus.UNPAID, attempt)
    return _Charged(None, attempt)


async def _record_charge(
    db: AsyncSession, fine_id: UUID, charged: _Charged, rules: FineRules, now: datetime
) -> Fine | None:
    """Returns the fine when it was cancelled while its charge went through: it is refunded."""
    async with db.begin():
        fine = await repository.get_fine(db, fine_id, for_update=True)
        if fine is None:
            return None
        change = replace(repository.FineUpdate.of(fine), charge_attempts=charged.attempts)
        if fine.status is FineStatus.CANCELLED:
            if charged.status is FineStatus.PAID and fine.refund_status is None:
                change = replace(change, refund_status=RefundStatus.PENDING, next_at=now)
                await repository.update_fine(db, fine.id, change, now)
                return replace(fine, refund_status=RefundStatus.PENDING)
            if charged.status is None and fine.refund_status is None:
                # The money may have been taken: the refund job asks again and returns it if so.
                change = replace(
                    change,
                    refund_status=RefundStatus.PENDING,
                    next_at=now + rules.settlement.pending_retry,
                )
            await repository.update_fine(db, fine.id, change, now)
            return None
        if fine.status not in OWED_FINE_STATUSES:
            return None
        if charged.status is None:
            change = replace(change, next_at=now + rules.settlement.pending_retry)
        elif charged.status is FineStatus.PAID:
            status = apply_fine_transition(fine.status, FineTransition.CHARGE_SUCCEEDED)
            change = replace(change, status=status, next_at=None)
            await repository.add_fine_event(
                db, fine.id, repository.NewFineEvent(FineEventAction.PAID, None, now)
            )
        else:
            status = apply_fine_transition(fine.status, FineTransition.CHARGE_DECLINED)
            next_at = next_charge_attempt(charged.attempts, rules.settlement.retry, now)
            change = replace(change, status=status, next_at=next_at)
            if fine.status is not FineStatus.UNPAID:
                await repository.add_fine_event(
                    db, fine.id, repository.NewFineEvent(FineEventAction.DECLINED, None, now)
                )
        await repository.update_fine(db, fine.id, change, now)
    logger.info("fine_charge_settled", fine_id=str(fine_id), status=charged.status)
    return None


@dataclass(frozen=True)
class _Taken:
    # The charge that took the money; None when none did, or none is known to have.
    paid: PaymentOperation | None
    # True while a charge's outcome is still unknown: nothing can be said yet.
    unknown: bool


async def _taken(db: AsyncSession, gw: payments.Gateway, fine: Fine, now: datetime) -> _Taken:
    operations = await payments.operations_with_key_prefix(db, f"fine:{fine.id}:charge:")
    paid = next((op for op in operations if op.status is OperationStatus.SUCCEEDED), None)
    open_ = next((op for op in operations if not op.is_final), None)
    if paid is not None or open_ is None:
        return _Taken(paid, unknown=False)
    # The same key asks the provider what became of the charge and never takes it twice.
    try:
        asked = await payments.charge(
            db, gw, fine.user_id, payments.Payment(fine.amount, open_.idempotency_key), now
        )
    except NoUsableCardError:
        return _Taken(None, unknown=True)
    if asked.status is OperationStatus.SUCCEEDED:
        return _Taken(asked, unknown=False)
    return _Taken(None, unknown=not asked.is_final)


async def _send_refund(
    db: AsyncSession, gw: payments.Gateway, fine: Fine, paid: PaymentOperation, now: datetime
) -> tuple[int, PaymentOperation]:
    """The refund attempt it went under, and its operation."""
    attempt = fine.refund_attempts
    last = await payments.find_operation(db, fine_refund_key(fine.id, attempt)) if attempt else None
    if last is not None and last.status is OperationStatus.SUCCEEDED:
        return attempt, last
    if last is None or last.is_final:
        attempt += 1
    payment = payments.Payment(fine.amount, fine_refund_key(fine.id, attempt))
    return attempt, await payments.refund(db, gw, paid.id, payment, now)


async def _refund(
    db: AsyncSession, gw: payments.Gateway, fine: Fine, rules: FineRules, now: datetime
) -> None:
    """Returns the money of a cancelled fine; one more try after a declined refund."""
    taken = await _taken(db, gw, fine, now)
    attempt = fine.refund_attempts
    operation = None
    if taken.paid is not None:
        attempt, operation = await _send_refund(db, gw, fine, taken.paid, now)
    async with db.begin():
        locked = await repository.get_fine(db, fine.id, for_update=True)
        if locked is None or locked.refund_status is not RefundStatus.PENDING:
            return
        change = replace(repository.FineUpdate.of(locked), refund_attempts=attempt)
        if operation is None and taken.unknown:
            change = replace(change, next_at=now + rules.settlement.pending_retry)
        elif operation is None:
            # Nothing was taken: nothing to return.
            change = replace(change, refund_status=None, next_at=None)
        elif operation.status is OperationStatus.SUCCEEDED:
            change = replace(change, refund_status=RefundStatus.SUCCEEDED, next_at=None)
            await repository.add_fine_event(
                db, fine.id, repository.NewFineEvent(FineEventAction.REFUNDED, None, now)
            )
        elif operation.status is OperationStatus.DECLINED:
            change = replace(change, refund_status=RefundStatus.FAILED, next_at=None)
            await repository.add_fine_event(
                db, fine.id, repository.NewFineEvent(FineEventAction.REFUND_FAILED, None, now)
            )
        else:
            change = replace(change, next_at=now + rules.settlement.pending_retry)
        await repository.update_fine(db, fine.id, change, now)
    logger.info("fine_refund_settled", fine_id=str(fine.id), refund_status=change.refund_status)


async def unpaid_fines_total(db: AsyncSession, user_id: UUID) -> int:
    """Kopecks of the client's unpaid fines: with the rental debts, what blocks a reservation."""
    async with db.begin():
        return await repository.unpaid_fines_total(db, user_id)


async def settle_fine(  # noqa: PLR0913 - `debt` only tells the callers apart
    db: AsyncSession,
    gw: payments.Gateway,
    fine_id: UUID,
    rules: FineRules,
    now: datetime,
    *,
    debt: bool = True,
) -> None:
    """Takes a fine's money, or returns that of a cancelled one. Without `debt` a declined fine
    is left alone, as a rental's is: only the job and the client's button try it again."""
    async with db.begin():
        fine = await repository.get_fine(db, fine_id)
    if fine is None:
        return
    if fine.status is FineStatus.CANCELLED:
        if fine.refund_status is RefundStatus.PENDING:
            await _refund(db, gw, fine, rules, now)
        return
    owing = OWED_FINE_STATUSES if debt else frozenset({FineStatus.PENDING})
    if fine.status not in owing:
        return
    charged = await _try_charge(db, gw, fine, now)
    cancelled = await _record_charge(db, fine.id, charged, rules, now)
    if cancelled is not None:
        await _refund(db, gw, cancelled, rules, now)


async def settle_due_fines(
    db: AsyncSession, gw: payments.Gateway, rules: FineRules, now: datetime
) -> int:
    """The worker's part: new fines, unknown outcomes, debts and refunds whose time has come."""
    async with db.begin():
        due = await repository.lock_due_fines(db, now, _FINE_BATCH)
        for fine in due:
            # Claimed before paying, as rentals do: a crash in the middle leaves it to a later run.
            await repository.set_fine_next_at(
                db, fine.id, now + rules.settlement.pending_retry, now
            )
    for fine in due:
        try:
            await settle_fine(db, gw, fine.id, rules, now)
        except Exception:
            logger.exception("fine_settlement_failed", fine_id=str(fine.id))
            async with db.begin():
                await repository.set_fine_next_at(
                    db, fine.id, now + rules.settlement.retry.interval, now
                )
    return len(due)


async def _fine_page(
    db: AsyncSession, fine_filter: FineFilter, after: FineCursor | None, limit: int
) -> tuple[list[Fine], str | None]:
    """A page of fines and the cursor of the next one, None on the last."""
    # One more than the page: tells whether there is a next one.
    fines = await repository.list_fines(db, fine_filter, after, limit + 1)
    page = fines[:limit]
    if len(fines) <= limit:
        return page, None
    last = page[-1]
    return page, FineCursor(last.created_at, last.id).encode()


async def _fines_out(db: AsyncSession, fines: Sequence[Fine]) -> list[FineOut]:
    return [item for item, _ in await _fine_parts(db, fines)]


async def _fine_parts(
    db: AsyncSession, fines: Sequence[Fine]
) -> list[tuple[FineOut, list[FineEvent]]]:
    """A fixed number of queries per page."""
    versions = await pricing.get_fine_versions(db, {fine.fine_type_version_id for fine in fines})
    ids = [fine.id for fine in fines]
    photos = await repository.photos_by_fine(db, ids)
    events = await repository.events_by_fine(db, ids)
    parts = []
    for fine in fines:
        version = versions[fine.fine_type_version_id]
        out = FineOut(
            id=fine.id,
            rental_id=fine.rental_id,
            fine_type_id=version.fine_type_id,
            code=version.code,
            name_ru=version.terms.name_ru,
            name_en=version.terms.name_en,
            amount=fine.amount,
            automatic=fine.is_automatic,
            status=fine.status,
            comment=fine.comment,
            cancel_reason=fine.cancel_reason,
            refund_status=fine.refund_status,
            photos=[
                FinePhotoOut(id=photo.id, uploaded_at=photo.uploaded_at)
                for photo in photos.get(fine.id, [])
            ],
            created_at=fine.created_at,
            cancelled_at=fine.cancelled_at,
        )
        parts.append((out, events.get(fine.id, [])))
    return parts


async def _staff_fines_out(db: AsyncSession, fines: Sequence[Fine]) -> list[StaffFineOut]:
    parts = await _fine_parts(db, fines)
    people = await identity.get_client_identities(db, {fine.user_id for fine in fines})
    attached = await rentals.get_rentals(db, {fine.rental_id for fine in fines})
    car_ids = {rental.car_id for rental in attached.values()}
    cars = {car.id: car for car in await fleet.get_cars(db, car_ids)} if car_ids else {}
    staff_ids = {fine.created_by for fine in fines if fine.created_by}
    staff_ids |= {event.staff_id for _, events in parts for event in events if event.staff_id}
    names = await identity.get_staff_display_names(db, staff_ids)
    items = []
    for fine, (out, events) in zip(fines, parts, strict=True):
        person = people.get(fine.user_id)
        rental = attached.get(fine.rental_id)
        car = cars.get(rental.car_id) if rental else None
        items.append(
            StaffFineOut(
                **out.model_dump(),
                user_id=fine.user_id,
                first_name=person.first_name if person else None,
                last_name=person.last_name if person else None,
                car_plate=car.plate if car else None,
                created_by=fine.created_by,
                created_by_name=names.get(fine.created_by) if fine.created_by else None,
                cancelled_by=fine.cancelled_by,
                events=[
                    FineEventOut(
                        action=FineEventAction(event.action),
                        staff_id=event.staff_id,
                        staff_name=names.get(event.staff_id) if event.staff_id else None,
                        comment=event.comment,
                        created_at=event.created_at,
                    )
                    for event in events
                ],
            )
        )
    return items


@dataclass(frozen=True)
class ClientFineService:
    """A client's own fines; every read carries the owner."""

    db: AsyncSession
    limiter: RateLimiter
    rules: FineRules
    # None when PAYMENTS_PROVIDER is not set: paying answers 503.
    gateway: payments.Gateway | None = None

    async def _own(self, user_id: UUID, fine_id: UUID) -> Fine:
        fine = await repository.get_fine(self.db, fine_id, user_id)
        if fine is None:
            raise FineNotFoundError
        return fine

    async def fines(
        self, user_id: UUID, rental_id: UUID | None, after: FineCursor | None = None
    ) -> FineListOut:
        """Newest first, by pages of FINE_PAGE_SIZE_MAX: a client has a handful, so the first
        page is nearly always all of them."""
        fine_filter = FineFilter(user_id=user_id, rental_id=rental_id)
        async with self.db.begin():
            page, next_cursor = await _fine_page(self.db, fine_filter, after, FINE_PAGE_SIZE_MAX)
            return FineListOut(items=await _fines_out(self.db, page), next_cursor=next_cursor)

    async def fine(self, user_id: UUID, fine_id: UUID) -> FineOut:
        async with self.db.begin():
            fine = await self._own(user_id, fine_id)
            return (await _fines_out(self.db, [fine]))[0]

    async def pay(self, user_id: UUID, fine_id: UUID, now: datetime) -> FineOut:
        """One more try at an unpaid fine, e.g. after the client bound another card."""
        await self.limiter.hit(PAY_FINE_LIMIT, str(user_id))
        if self.gateway is None:
            raise PaymentsUnavailableError
        async with self.db.begin():
            fine = await self._own(user_id, fine_id)
        if fine.status in OWED_FINE_STATUSES:
            await settle_fine(self.db, self.gateway, fine.id, self.rules, now)
        return await self.fine(user_id, fine_id)

    async def photo_url(
        self, storage: ObjectStorage, user_id: UUID, fine_id: UUID, photo_id: UUID
    ) -> PhotoUrlOut:
        await self.limiter.hit(PHOTO_URL_LIMIT, str(user_id))
        async with self.db.begin():
            fine = await self._own(user_id, fine_id)
            photo = await repository.get_fine_photo(self.db, fine.id, photo_id)
        if photo is None:
            raise FinePhotoNotFoundError
        return await _signed(storage, photo.object_key)


@dataclass(frozen=True)
class ManualFineRequest:
    rental_id: UUID
    fine_type_id: UUID
    comment: str
    # The operator's form's: a repeat with it returns the fine of the first request.
    key: UUID


@dataclass(frozen=True)
class StaffFineService:
    """What `operator` and `admin` do with fines; the router checks the role."""

    db: AsyncSession
    limiter: RateLimiter
    origin: RequestOrigin
    rules: FineRules
    gateway: payments.Gateway | None = None

    async def _audit(
        self,
        staff_id: UUID,
        action: AuditAction,
        fine_id: UUID,
        details: dict[str, DetailValue] | None = None,
    ) -> None:
        await audit.record(
            self.db,
            AuditEntry(
                Actor(ActorType.STAFF, staff_id),
                action,
                self.origin,
                AuditTarget(AuditTargetType.FINE, fine_id),
                details or {},
            ),
        )

    async def _out(self, fine_id: UUID) -> StaffFineOut:
        async with self.db.begin():
            fine = await repository.get_fine(self.db, fine_id)
            if fine is None:
                raise FineNotFoundError
            return (await _staff_fines_out(self.db, [fine]))[0]

    async def list(
        self, fine_filter: FineFilter, after: FineCursor | None, limit: int
    ) -> StaffFineListOut:
        async with self.db.begin():
            page, next_cursor = await _fine_page(self.db, fine_filter, after, limit)
            items = await _staff_fines_out(self.db, page)
        return StaffFineListOut(items=items, next_cursor=next_cursor)

    async def get(self, fine_id: UUID) -> StaffFineOut:
        return await self._out(fine_id)

    async def charge(
        self, staff_id: UUID, request: ManualFineRequest, now: datetime
    ) -> StaffFineOut:
        """Raises SupportRentalNotFoundError, FineRentalNotFinishedError, FineTypeNotFoundError,
        FineTypeNotManualError, IdempotencyKeyReusedError. The money is taken right after, or by
        the job; a repeat with the same key returns the fine charged the first time."""
        request = replace(request, comment=ensure_reason(request.comment))
        rental_id = request.rental_id
        async with self.db.begin():
            repeat = await repository.get_fine_by_key(self.db, request.key)
            if repeat is not None:
                return await self._repeated(repeat, staff_id, request)
            rental = (await rentals.get_rentals(self.db, {rental_id})).get(rental_id)
            if rental is None:
                raise SupportRentalNotFoundError
            # A cancelled reservation had no trip to be fined for.
            if rental.trip_started_at is None or rental.ended_at is None:
                raise FineRentalNotFinishedError
            version = await pricing.get_fine_type(self.db, request.fine_type_id)
            if version.kind is not FineKind.MANUAL:
                raise FineTypeNotManualError
            fine = await repository.add_fine(
                self.db,
                repository.NewFine(
                    rental_id=rental.id,
                    user_id=rental.user_id,
                    fine_type_id=version.fine_type_id,
                    fine_type_version_id=version.id,
                    amount=version.terms.amount,
                    comment=request.comment,
                    created_by=staff_id,
                    at=now,
                    idempotency_key=request.key,
                ),
            )
            if fine is None:
                # A parallel request with the same key got here first.
                repeat = await repository.get_fine_by_key(self.db, request.key)
                if repeat is None:
                    raise RuntimeError("a manual fine was taken for an automatic one")
                return await self._repeated(repeat, staff_id, request)
            await _charged(self.db, fine, self.origin, now)
        logger.info("fine_charged", fine_id=str(fine.id), automatic=False)
        if self.gateway is not None:
            try:
                await settle_fine(self.db, self.gateway, fine.id, self.rules, now, debt=False)
            except Exception:
                # The fine is charged already and due at once: the job takes the money instead.
                logger.exception("fine_settlement_failed", fine_id=str(fine.id))
        return await self._out(fine.id)

    async def _repeated(
        self, repeat: Fine, staff_id: UUID, request: ManualFineRequest
    ) -> StaffFineOut:
        versions = await pricing.get_fine_versions(self.db, {repeat.fine_type_version_id})
        type_id = versions[repeat.fine_type_version_id].fine_type_id
        sent = ManualFineRequest(repeat.rental_id, type_id, repeat.comment or "", request.key)
        if (repeat.created_by, sent) != (staff_id, request):
            raise IdempotencyKeyReusedError
        return (await _staff_fines_out(self.db, [repeat]))[0]

    async def cancel(
        self, staff_id: UUID, fine_id: UUID, reason: str, now: datetime
    ) -> StaffFineOut:
        """The fine stays, cancelled, with the reason; paid money goes back. Cancelling again
        after a declined refund tries the refund once more."""
        reason = ensure_reason(reason)
        async with self.db.begin():
            fine = await repository.get_fine(self.db, fine_id, for_update=True)
            if fine is None:
                raise FineNotFoundError
            if fine.status is FineStatus.CANCELLED:
                if fine.refund_status is not RefundStatus.FAILED:
                    return (await _staff_fines_out(self.db, [fine]))[0]
                change = replace(
                    repository.FineUpdate.of(fine), refund_status=RefundStatus.PENDING, next_at=now
                )
                # The fine stays cancelled for its first reason; this one explains the retry.
                event, action = FineEventAction.REFUND_RETRIED, AuditAction.FINE_REFUND_RETRIED
            else:
                status = apply_fine_transition(fine.status, FineTransition.CANCEL)
                # A charge that may have gone through is checked, and returned if it did.
                refund = fine.status is FineStatus.PAID or fine.charge_attempts > 0
                change = replace(
                    repository.FineUpdate.of(fine),
                    status=status,
                    next_at=now if refund else None,
                    cancel_reason=reason,
                    cancelled_by=staff_id,
                    cancelled_at=now,
                    refund_status=RefundStatus.PENDING if refund else None,
                )
                event, action = FineEventAction.CANCELLED, AuditAction.FINE_CANCELLED
            await repository.update_fine(self.db, fine.id, change, now)
            await repository.add_fine_event(
                self.db, fine.id, repository.NewFineEvent(event, staff_id, now, reason)
            )
            details: dict[str, DetailValue] = {
                "rental_id": fine.rental_id,
                "amount": fine.amount,
                "refund": change.refund_status is RefundStatus.PENDING,
            }
            await self._audit(staff_id, action, fine.id, details)
        logger.info("fine_cancelled", fine_id=str(fine_id), action=str(action))
        if self.gateway is not None and change.refund_status is RefundStatus.PENDING:
            try:
                await settle_fine(self.db, self.gateway, fine_id, self.rules, now)
            except Exception:
                # The cancellation is saved and the refund due at once: the job returns the money.
                logger.exception("fine_settlement_failed", fine_id=str(fine_id))
        return await self._out(fine_id)

    async def _lock_for_photo(self, fine_id: UUID) -> Fine:
        fine = await repository.get_fine(self.db, fine_id, for_update=True)
        if fine is None:
            raise FineNotFoundError
        if fine.status is FineStatus.CANCELLED:
            raise FineTransitionNotAllowedError
        if await repository.count_fine_photos(self.db, fine.id) >= MAX_FINE_PHOTOS:
            raise FinePhotoLimitError
        return fine

    async def count_upload(self, staff_id: UUID) -> None:
        """Counted before the body is read, so an exhausted limit does not cost 10 MB each time."""
        await self.limiter.hit(STAFF_UPLOAD_LIMIT, str(staff_id))

    async def add_photo(
        self, storage: ObjectStorage, staff_id: UUID, fine_id: UUID, data: bytes, now: datetime
    ) -> StaffFineOut:
        """Evidence the client sees; stored privately, opened by short-lived links."""
        async with self.db.begin():
            await self._lock_for_photo(fine_id)
        photo = await _clean(data)
        key = f"fines/{fine_id}/{uuid4()}.jpg"
        await storage.put(key, photo, OUTPUT_CONTENT_TYPE)
        try:
            async with self.db.begin():
                fine = await self._lock_for_photo(fine_id)
                photo_id = await repository.add_fine_photo(self.db, fine.id, key, len(photo), now)
                await self._audit(
                    staff_id, AuditAction.FINE_PHOTO_ADDED, fine.id, {"photo_id": photo_id}
                )
        except Exception:
            await _delete_quietly(storage, key)
            raise
        return await self._out(fine_id)

    async def photo_url(
        self, storage: ObjectStorage, staff_id: UUID, fine_id: UUID, photo_id: UUID
    ) -> PhotoUrlOut:
        await self.limiter.hit(STAFF_PHOTO_URL_LIMIT, str(staff_id))
        async with self.db.begin():
            photo = await repository.get_fine_photo(self.db, fine_id, photo_id)
            if photo is None:
                raise FinePhotoNotFoundError
            await self._audit(
                staff_id, AuditAction.FINE_PHOTO_VIEWED, fine_id, {"photo_id": photo.id}
            )
        return await _signed(storage, photo.object_key)
