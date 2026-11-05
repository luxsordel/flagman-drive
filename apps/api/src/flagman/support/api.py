"""Support chat, incidents and fines: `/support` for clients, `/admin/support` for operators.

New messages are polled with `after` (docs/adr/0023): the client and the admin panel pass the
largest `seq` they have and get only what is newer.
"""

from datetime import UTC, datetime
from http import HTTPStatus
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from flagman.audit.dependencies import get_request_origin
from flagman.audit.domain import RequestOrigin
from flagman.core.db import get_session
from flagman.core.errors import RATE_LIMITED_RESPONSE, Problem
from flagman.core.photos import PhotoTooLargeError, photo_request_body, read_photo_body
from flagman.core.ratelimit import RateLimiter, get_rate_limiter
from flagman.core.resources import Resources, get_resources
from flagman.core.storage import ObjectStorage, object_storage_from_settings
from flagman.identity.dependencies import (
    CurrentClient,
    CurrentStaff,
    get_current_client,
    require_live_staff_session,
    require_staff_role,
)
from flagman.identity.domain import StaffRole
from flagman.payments import service as payments
from flagman.payments.domain import PaymentsUnavailableError
from flagman.support.domain import (
    POLL_PAGE_SIZE_MAX,
    QUEUE_SIZE_MAX,
    SEQ_MAX,
    FineCursor,
    FineFilter,
    IncidentTransition,
    SupportPhotoStorageUnavailableError,
    SupportPhotoTooLargeError,
)
from flagman.support.schemas import (
    ClientUnreadOut,
    ConversationOut,
    ConversationQueueOut,
    FineCancelIn,
    FineIn,
    FineListOut,
    FineOut,
    IncidentCommentIn,
    IncidentIn,
    IncidentListOut,
    IncidentOut,
    IncidentPageQuery,
    MessageIn,
    MessageOut,
    MessagePageOut,
    PhotoUrlOut,
    StaffFineListOut,
    StaffFineOut,
    StaffFineQuery,
    StaffIncidentListOut,
    StaffIncidentOut,
    StaffIncidentQuery,
    StaffMessageIn,
    StaffMessageOut,
    StaffMessagePageOut,
    UnreadOut,
)
from flagman.support.service import (
    ClientFineService,
    ClientSupportService,
    IncidentReport,
    ManualFineRequest,
    PhotoTarget,
    StaffFineService,
    StaffRef,
    StaffSupportService,
    fine_rules,
)

_PAGE_DEFAULT = 50


def _problem(description: str) -> dict[str, Any]:
    return {"model": Problem, "description": description}


router = APIRouter(
    prefix="/support",
    tags=["support"],
    responses={
        HTTPStatus.UNAUTHORIZED: _problem(
            "`urn:flagman:problem:access-token-invalid`: refresh or sign in."
        ),
    },
)

# Ids in the path are fine here: every operator may open every conversation and incident, and
# nobody else gets past the role check; a technician has no business in clients' messages.
staff_router = APIRouter(
    prefix="/admin/support",
    tags=["support-staff"],
    responses={
        HTTPStatus.UNAUTHORIZED: _problem(
            "`urn:flagman:problem:access-token-invalid`: sign in as staff."
        ),
        HTTPStatus.FORBIDDEN: _problem(
            "`urn:flagman:problem:forbidden`: only `operator` and `admin` handle support."
        ),
    },
)


def get_client_support_service(
    db: Annotated[AsyncSession, Depends(get_session)],
    limiter: Annotated[RateLimiter, Depends(get_rate_limiter)],
) -> ClientSupportService:
    return ClientSupportService(db=db, limiter=limiter)


def get_staff_support_service(
    db: Annotated[AsyncSession, Depends(get_session)],
    limiter: Annotated[RateLimiter, Depends(get_rate_limiter)],
    origin: Annotated[RequestOrigin, Depends(get_request_origin)],
) -> StaffSupportService:
    return StaffSupportService(db=db, limiter=limiter, origin=origin)


def get_photo_storage(resources: Annotated[Resources, Depends(get_resources)]) -> ObjectStorage:
    storage = object_storage_from_settings(resources.settings)
    if storage is None:
        raise SupportPhotoStorageUnavailableError("No photo storage is configured.")
    return storage


def _no_store(response: Response) -> None:
    # The answer carries a signed link to a photo; a cached copy would hand it out again later.
    response.headers["Cache-Control"] = "no-store"


_SUPPORT_ROLES = (StaffRole.OPERATOR, StaffRole.ADMIN)

# The caller comes first in every signature: without a token the answer is 401, not 503.
Client = Annotated[CurrentClient, Depends(get_current_client)]
Staff = Annotated[CurrentStaff, Depends(require_staff_role(*_SUPPORT_ROLES))]
# Sending a car to service is a fleet action: a revoked session must not do it.
LiveStaff = Annotated[CurrentStaff, Depends(require_live_staff_session(*_SUPPORT_ROLES))]
ClientService = Annotated[ClientSupportService, Depends(get_client_support_service)]
StaffService = Annotated[StaffSupportService, Depends(get_staff_support_service)]
Storage = Annotated[ObjectStorage, Depends(get_photo_storage)]
After = Annotated[int, Query(ge=0, le=SEQ_MAX, description="Only messages with a larger `seq`.")]
PageSize = Annotated[int, Query(ge=1, le=POLL_PAGE_SIZE_MAX)]
QueueSize = Annotated[int, Query(ge=1, le=QUEUE_SIZE_MAX)]


def _ref(staff: CurrentStaff) -> StaffRef:
    return StaffRef(staff_id=staff.staff_id, admin=staff.role is StaffRole.ADMIN)


_STORAGE_UNAVAILABLE = _problem("`urn:flagman:problem:photo-storage-unavailable`.")
_PHOTO_ERRORS: dict[int | str, dict[str, Any]] = {
    HTTPStatus.REQUEST_ENTITY_TOO_LARGE: _problem("`urn:flagman:problem:support-photo-too-large`."),
    HTTPStatus.UNSUPPORTED_MEDIA_TYPE: _problem(
        "`urn:flagman:problem:support-photo-type-unsupported`: not a photo."
    ),
    HTTPStatus.TOO_MANY_REQUESTS: RATE_LIMITED_RESPONSE,
    HTTPStatus.SERVICE_UNAVAILABLE: _STORAGE_UNAVAILABLE,
}
_URL_ERRORS: dict[int | str, dict[str, Any]] = {
    HTTPStatus.TOO_MANY_REQUESTS: RATE_LIMITED_RESPONSE,
    HTTPStatus.SERVICE_UNAVAILABLE: _STORAGE_UNAVAILABLE,
}
_CONVERSATION_NOT_FOUND = _problem("`urn:flagman:problem:conversation-not-found`.")
_CONVERSATION_CONFLICT = _problem(
    "`urn:flagman:problem:conversation-closed`; `conversation-taken`: another operator has it."
)
_INCIDENT_NOT_FOUND = _problem(
    "`urn:flagman:problem:incident-not-found`, also for another client's."
)


async def _read_photo(request: Request) -> bytes:
    try:
        return await read_photo_body(request)
    except PhotoTooLargeError as error:
        raise SupportPhotoTooLargeError from error


@router.get("/messages", summary="The client's conversation with support, newer than `after`")
async def get_messages(
    client: Client, service: ClientService, after: After = 0, limit: PageSize = _PAGE_DEFAULT
) -> MessagePageOut:
    return await service.messages(client.user_id, after, limit)


@router.get("/unread", summary="How many answers of support the client has not seen")
async def get_unread(client: Client, service: ClientService) -> ClientUnreadOut:
    return await service.unread(client.user_id)


@router.post(
    "/messages",
    status_code=HTTPStatus.CREATED,
    summary="Write to support; opens a conversation if none is open",
    responses={
        HTTPStatus.NOT_FOUND: _problem("`urn:flagman:problem:rental-not-found`."),
        HTTPStatus.TOO_MANY_REQUESTS: RATE_LIMITED_RESPONSE,
    },
)
async def send_message(client: Client, body: MessageIn, service: ClientService) -> MessageOut:
    return await service.send_text(
        client.user_id, body.text, body.rental_id, body.client_message_id, datetime.now(UTC)
    )


@router.post(
    "/messages/photos",
    status_code=HTTPStatus.CREATED,
    summary="Send a photo to support",
    openapi_extra=photo_request_body("The photo"),
    responses={
        HTTPStatus.NOT_FOUND: _problem("`urn:flagman:problem:rental-not-found`."),
        **_PHOTO_ERRORS,
    },
)
async def send_photo(
    client: Client,
    request: Request,
    service: ClientService,
    storage: Storage,
    target: Annotated[PhotoTarget, Depends()],
) -> MessageOut:
    await service.count_upload(client.user_id)
    photo = await _read_photo(request)
    return await service.send_photo(storage, client.user_id, photo, target, datetime.now(UTC))


@router.get(
    "/messages/{message_id}/photo-url",
    summary="A short-lived link to a photo of the conversation",
    dependencies=[Depends(_no_store)],
    responses={
        HTTPStatus.NOT_FOUND: _problem("`urn:flagman:problem:message-not-found`."),
        **_URL_ERRORS,
    },
)
async def get_photo_url(
    client: Client, message_id: UUID, service: ClientService, storage: Storage
) -> PhotoUrlOut:
    return await service.photo_url(storage, client.user_id, message_id)


@router.post(
    "/incidents",
    status_code=HTTPStatus.CREATED,
    summary="Report an accident, damage or a fault; the current rental is attached by the server",
    description=(
        "An accident during a rental goes to the top of the operators' queue. The rental is not "
        "ended and its billing is not paused: the operator decides (docs/adr/0023)."
    ),
    responses={
        HTTPStatus.NOT_FOUND: _problem("`urn:flagman:problem:rental-not-found`."),
        HTTPStatus.CONFLICT: _problem("`urn:flagman:problem:too-many-open-incidents`."),
        HTTPStatus.TOO_MANY_REQUESTS: RATE_LIMITED_RESPONSE,
    },
)
async def report_incident(client: Client, body: IncidentIn, service: ClientService) -> IncidentOut:
    location = (body.location.lat, body.location.lon) if body.location else None
    report = IncidentReport(
        body.type, body.description, location, body.rental_id, body.client_report_id
    )
    return await service.report_incident(client.user_id, report, datetime.now(UTC))


@router.get("/incidents", summary="The client's incidents, the newest first")
async def list_my_incidents(
    client: Client, service: ClientService, query: Annotated[IncidentPageQuery, Query()]
) -> IncidentListOut:
    return await service.incidents(client.user_id, query.cursor, query.limit)


@router.get(
    "/incidents/{incident_id}",
    summary="One of the client's incidents",
    responses={HTTPStatus.NOT_FOUND: _INCIDENT_NOT_FOUND},
)
async def get_my_incident(client: Client, incident_id: UUID, service: ClientService) -> IncidentOut:
    return await service.incident(client.user_id, incident_id)


@router.post(
    "/incidents/{incident_id}/photos",
    status_code=HTTPStatus.CREATED,
    summary="Add a photo to an open incident",
    openapi_extra=photo_request_body("The photo"),
    responses={
        HTTPStatus.NOT_FOUND: _INCIDENT_NOT_FOUND,
        HTTPStatus.CONFLICT: _problem(
            "`urn:flagman:problem:incident-closed`; `incident-photo-limit`."
        ),
        **_PHOTO_ERRORS,
    },
)
async def add_incident_photo(
    client: Client, incident_id: UUID, request: Request, service: ClientService, storage: Storage
) -> IncidentOut:
    await service.count_upload(client.user_id)
    photo = await _read_photo(request)
    return await service.add_incident_photo(
        storage, client.user_id, incident_id, photo, datetime.now(UTC)
    )


@router.get(
    "/incidents/{incident_id}/photos/{photo_id}/url",
    summary="A short-lived link to a photo of the incident",
    dependencies=[Depends(_no_store)],
    responses={
        HTTPStatus.NOT_FOUND: _problem(
            "`urn:flagman:problem:incident-not-found`, `incident-photo-not-found`."
        ),
        **_URL_ERRORS,
    },
)
async def get_my_incident_photo_url(
    client: Client, incident_id: UUID, photo_id: UUID, service: ClientService, storage: Storage
) -> PhotoUrlOut:
    return await service.incident_photo_url(storage, client.user_id, incident_id, photo_id)


@staff_router.get("/unread", summary="Counters for the menu: unread conversations, new incidents")
async def get_staff_unread(staff: Staff, service: StaffService) -> UnreadOut:
    return await service.unread()


@staff_router.get(
    "/conversations", summary="Open conversations: unread first, then the longest waiting"
)
async def list_conversations(
    staff: Staff, service: StaffService, limit: QueueSize = _PAGE_DEFAULT
) -> ConversationQueueOut:
    return await service.queue(limit)


@staff_router.get(
    "/conversations/{conversation_id}",
    summary="A conversation with the client and the rental it is about",
    responses={HTTPStatus.NOT_FOUND: _CONVERSATION_NOT_FOUND},
)
async def get_conversation(
    staff: Staff, conversation_id: UUID, service: StaffService
) -> ConversationOut:
    return await service.conversation(conversation_id)


@staff_router.get(
    "/conversations/{conversation_id}/messages",
    summary="Messages newer than `after`; reading clears the unread count",
    responses={HTTPStatus.NOT_FOUND: _CONVERSATION_NOT_FOUND},
)
async def get_conversation_messages(
    staff: Staff,
    conversation_id: UUID,
    service: StaffService,
    after: After = 0,
    limit: PageSize = _PAGE_DEFAULT,
) -> StaffMessagePageOut:
    return await service.messages(conversation_id, after, limit)


@staff_router.post(
    "/conversations/{conversation_id}/take",
    summary="Take the conversation; other operators can no longer answer it",
    responses={
        HTTPStatus.NOT_FOUND: _CONVERSATION_NOT_FOUND,
        HTTPStatus.CONFLICT: _CONVERSATION_CONFLICT,
    },
)
async def take_conversation(
    staff: Staff, conversation_id: UUID, service: StaffService
) -> ConversationOut:
    return await service.take(_ref(staff), conversation_id)


@staff_router.post(
    "/conversations/{conversation_id}/messages",
    status_code=HTTPStatus.CREATED,
    summary="Answer the client; answering takes the conversation",
    description=(
        "Client messages after `seen_seq` stay unread: the operator answered without seeing them."
    ),
    responses={
        HTTPStatus.NOT_FOUND: _CONVERSATION_NOT_FOUND,
        HTTPStatus.CONFLICT: _CONVERSATION_CONFLICT,
        HTTPStatus.TOO_MANY_REQUESTS: RATE_LIMITED_RESPONSE,
    },
)
async def reply(
    staff: Staff, conversation_id: UUID, body: StaffMessageIn, service: StaffService
) -> StaffMessageOut:
    return await service.reply(
        _ref(staff), conversation_id, body.text, body.seen_seq, datetime.now(UTC)
    )


@staff_router.post(
    "/conversations/{conversation_id}/close",
    summary="Close the conversation; the client's next message opens a new one",
    responses={
        HTTPStatus.NOT_FOUND: _CONVERSATION_NOT_FOUND,
        HTTPStatus.CONFLICT: _CONVERSATION_CONFLICT,
    },
)
async def close_conversation(
    staff: Staff, conversation_id: UUID, service: StaffService
) -> ConversationOut:
    return await service.close(_ref(staff), conversation_id, datetime.now(UTC))


@staff_router.get(
    "/conversations/{conversation_id}/messages/{message_id}/photo-url",
    summary="A short-lived link to a photo; every link is in the audit log",
    dependencies=[Depends(_no_store)],
    responses={
        HTTPStatus.NOT_FOUND: _problem("`urn:flagman:problem:message-not-found`."),
        **_URL_ERRORS,
    },
)
async def get_staff_photo_url(
    staff: Staff, conversation_id: UUID, message_id: UUID, service: StaffService, storage: Storage
) -> PhotoUrlOut:
    return await service.photo_url(storage, staff.staff_id, conversation_id, message_id)


@staff_router.get(
    "/incidents",
    summary="Incidents: the open ones as a queue, any other list from the newest",
    description="A cursor is valid only with the same `status` and `type`.",
)
async def list_incidents(
    staff: Staff, service: StaffService, query: Annotated[StaffIncidentQuery, Query()]
) -> StaffIncidentListOut:
    statuses = frozenset(query.status) if query.status else None
    return await service.incidents(statuses, query.type, query.cursor, query.limit)


@staff_router.get(
    "/incidents/{incident_id}",
    summary="An incident with its photos and chronology",
    responses={HTTPStatus.NOT_FOUND: _problem("`urn:flagman:problem:incident-not-found`.")},
)
async def get_incident(staff: Staff, incident_id: UUID, service: StaffService) -> StaffIncidentOut:
    return await service.incident(incident_id)


_INCIDENT_CHANGE_ERRORS: dict[int | str, dict[str, Any]] = {
    HTTPStatus.NOT_FOUND: _problem("`urn:flagman:problem:incident-not-found`."),
    HTTPStatus.CONFLICT: _problem(
        "`urn:flagman:problem:incident-transition-not-allowed`; "
        "`incident-not-yours`: another operator works on it."
    ),
}


@staff_router.post(
    "/incidents/{incident_id}/take",
    summary="Take a new incident in work",
    responses=_INCIDENT_CHANGE_ERRORS,
)
async def take_incident(staff: Staff, incident_id: UUID, service: StaffService) -> StaffIncidentOut:
    return await service.change_incident(
        _ref(staff), incident_id, IncidentTransition.TAKE, None, datetime.now(UTC)
    )


@staff_router.post(
    "/incidents/{incident_id}/resolve",
    summary="Close the incident as resolved, with a comment",
    responses=_INCIDENT_CHANGE_ERRORS,
)
async def resolve_incident(
    staff: Staff, incident_id: UUID, body: IncidentCommentIn, service: StaffService
) -> StaffIncidentOut:
    return await service.change_incident(
        _ref(staff), incident_id, IncidentTransition.RESOLVE, body.comment, datetime.now(UTC)
    )


@staff_router.post(
    "/incidents/{incident_id}/reject",
    summary="Close the incident as rejected, with a comment",
    responses=_INCIDENT_CHANGE_ERRORS,
)
async def reject_incident(
    staff: Staff, incident_id: UUID, body: IncidentCommentIn, service: StaffService
) -> StaffIncidentOut:
    return await service.change_incident(
        _ref(staff), incident_id, IncidentTransition.REJECT, body.comment, datetime.now(UTC)
    )


@staff_router.post(
    "/incidents/{incident_id}/send-to-service",
    summary="Send the incident's car to service",
    description=(
        "Only for a car that is free: a car still held by a rental is refused with "
        "`urn:flagman:problem:car-transition-not-allowed` until the rental ends."
    ),
    responses={
        HTTPStatus.NOT_FOUND: _problem(
            "`urn:flagman:problem:incident-not-found`, `car-not-found`."
        ),
        HTTPStatus.CONFLICT: _problem(
            "`urn:flagman:problem:incident-transition-not-allowed`: not in work, no car or sent "
            "already; `incident-not-yours`; `car-transition-not-allowed`."
        ),
    },
)
async def send_car_to_service(
    staff: LiveStaff, incident_id: UUID, service: StaffService
) -> StaffIncidentOut:
    return await service.send_car_to_service(_ref(staff), incident_id, datetime.now(UTC))


@staff_router.get(
    "/incidents/{incident_id}/photos/{photo_id}/url",
    summary="A short-lived link to a photo of the incident; every link is in the audit log",
    dependencies=[Depends(_no_store)],
    responses={
        HTTPStatus.NOT_FOUND: _problem("`urn:flagman:problem:incident-photo-not-found`."),
        **_URL_ERRORS,
    },
)
async def get_staff_incident_photo_url(
    staff: Staff, incident_id: UUID, photo_id: UUID, service: StaffService, storage: Storage
) -> PhotoUrlOut:
    return await service.incident_photo_url(storage, staff.staff_id, incident_id, photo_id)


# Fines (#121, docs/adr/0028)


def _payments_gateway(resources: Resources) -> payments.Gateway | None:
    try:
        return payments.gateway(resources.settings)
    except PaymentsUnavailableError:
        # Reading and charging work without payments; the job takes the money once it is set.
        return None


def get_client_fine_service(
    db: Annotated[AsyncSession, Depends(get_session)],
    limiter: Annotated[RateLimiter, Depends(get_rate_limiter)],
    resources: Annotated[Resources, Depends(get_resources)],
) -> ClientFineService:
    return ClientFineService(
        db=db,
        limiter=limiter,
        rules=fine_rules(resources.settings),
        gateway=_payments_gateway(resources),
    )


def get_staff_fine_service(
    db: Annotated[AsyncSession, Depends(get_session)],
    limiter: Annotated[RateLimiter, Depends(get_rate_limiter)],
    origin: Annotated[RequestOrigin, Depends(get_request_origin)],
    resources: Annotated[Resources, Depends(get_resources)],
) -> StaffFineService:
    return StaffFineService(
        db=db,
        limiter=limiter,
        origin=origin,
        rules=fine_rules(resources.settings),
        gateway=_payments_gateway(resources),
    )


ClientFines = Annotated[ClientFineService, Depends(get_client_fine_service)]
StaffFines = Annotated[StaffFineService, Depends(get_staff_fine_service)]
_FINE_NOT_FOUND = _problem("`urn:flagman:problem:fine-not-found`, also for another client's.")
_FINE_PHOTO_NOT_FOUND = _problem("`urn:flagman:problem:fine-not-found`, `fine-photo-not-found`.")


@router.get("/fines", summary="The client's fines, newest first")
async def list_my_fines(
    client: Client,
    service: ClientFines,
    rental_id: Annotated[UUID | None, Query(description="Only the fines of this trip.")] = None,
    cursor: Annotated[
        str | None, Query(max_length=200, description="`next_cursor` of the previous page.")
    ] = None,
) -> FineListOut:
    after = FineCursor.decode(cursor) if cursor else None
    return await service.fines(client.user_id, rental_id, after)


@router.get(
    "/fines/{fine_id}",
    summary="One of the client's fines",
    responses={HTTPStatus.NOT_FOUND: _FINE_NOT_FOUND},
)
async def get_my_fine(client: Client, fine_id: UUID, service: ClientFines) -> FineOut:
    return await service.fine(client.user_id, fine_id)


@router.post(
    "/fines/{fine_id}/pay",
    summary="Try the card again for an unpaid fine",
    description="Does nothing to a paid or cancelled fine; the answer shows where it stands.",
    responses={
        HTTPStatus.NOT_FOUND: _FINE_NOT_FOUND,
        HTTPStatus.TOO_MANY_REQUESTS: RATE_LIMITED_RESPONSE,
        HTTPStatus.SERVICE_UNAVAILABLE: _problem("`urn:flagman:problem:payments-unavailable`."),
    },
)
async def pay_my_fine(client: Client, fine_id: UUID, service: ClientFines) -> FineOut:
    return await service.pay(client.user_id, fine_id, datetime.now(UTC))


@router.get(
    "/fines/{fine_id}/photos/{photo_id}/url",
    summary="A short-lived link to a photo of the fine",
    dependencies=[Depends(_no_store)],
    responses={HTTPStatus.NOT_FOUND: _FINE_PHOTO_NOT_FOUND, **_URL_ERRORS},
)
async def get_my_fine_photo_url(
    client: Client, fine_id: UUID, photo_id: UUID, service: ClientFines, storage: Storage
) -> PhotoUrlOut:
    return await service.photo_url(storage, client.user_id, fine_id, photo_id)


@staff_router.get("/fines", summary="Charged fines, newest first, with filters")
async def list_fines(
    staff: Staff, query: Annotated[StaffFineQuery, Query()], service: StaffFines
) -> StaffFineListOut:
    fine_filter = FineFilter(
        statuses=frozenset(query.status) if query.status else None,
        fine_type_ids=frozenset(query.fine_type_id) if query.fine_type_id else None,
        user_id=query.user_id,
        rental_id=query.rental_id,
        created_from=query.created_from,
        created_to=query.created_to,
    )
    cursor = FineCursor.decode(query.cursor) if query.cursor else None
    return await service.list(fine_filter, cursor, query.limit)


@staff_router.post(
    "/fines",
    status_code=HTTPStatus.CREATED,
    summary="Charge a manual fine to an ended trip",
    description=(
        "The card is charged right after; a declined card makes the fine a debt of the client. "
        "Photos are added with `POST /admin/support/fines/{fine_id}/photos`."
    ),
    responses={
        HTTPStatus.NOT_FOUND: _problem(
            "`urn:flagman:problem:rental-not-found`; `fine-type-not-found`."
        ),
        HTTPStatus.CONFLICT: _problem(
            "`urn:flagman:problem:fine-rental-not-finished`; "
            "`idempotency-key-reused`: the key was sent with another fine."
        ),
        HTTPStatus.UNPROCESSABLE_ENTITY: _problem(
            "`urn:flagman:problem:fine-type-not-manual`: the system charges that one itself."
        ),
    },
)
async def charge_fine(
    staff: LiveStaff,
    body: FineIn,
    idempotency_key: Annotated[
        UUID,
        Header(
            alias="Idempotency-Key",
            description="A new one per filled form; a repeat with it charges nothing again.",
        ),
    ],
    service: StaffFines,
) -> StaffFineOut:
    request = ManualFineRequest(body.rental_id, body.fine_type_id, body.comment, idempotency_key)
    return await service.charge(staff.staff_id, request, datetime.now(UTC))


@staff_router.get(
    "/fines/{fine_id}",
    summary="One fine with its history",
    responses={HTTPStatus.NOT_FOUND: _FINE_NOT_FOUND},
)
async def get_fine(staff: Staff, fine_id: UUID, service: StaffFines) -> StaffFineOut:
    return await service.get(fine_id)


@staff_router.post(
    "/fines/{fine_id}/cancel",
    summary="Cancel a fine; paid money goes back to the card",
    description=(
        "The fine stays in the list as `cancelled`, with the reason. Cancelling a cancelled fine "
        "whose refund was declined tries the refund again; otherwise it changes nothing."
    ),
    responses={HTTPStatus.NOT_FOUND: _FINE_NOT_FOUND},
)
async def cancel_fine(
    staff: LiveStaff, fine_id: UUID, body: FineCancelIn, service: StaffFines
) -> StaffFineOut:
    return await service.cancel(staff.staff_id, fine_id, body.reason, datetime.now(UTC))


@staff_router.post(
    "/fines/{fine_id}/photos",
    status_code=HTTPStatus.CREATED,
    summary="Add a photo of the evidence to a fine",
    openapi_extra=photo_request_body("The photo"),
    responses={
        HTTPStatus.NOT_FOUND: _FINE_NOT_FOUND,
        HTTPStatus.CONFLICT: _problem(
            "`urn:flagman:problem:fine-photo-limit`; `fine-transition-not-allowed`: cancelled."
        ),
        **_PHOTO_ERRORS,
    },
)
async def add_fine_photo(
    staff: LiveStaff, fine_id: UUID, request: Request, service: StaffFines, storage: Storage
) -> StaffFineOut:
    await service.count_upload(staff.staff_id)
    photo = await _read_photo(request)
    return await service.add_photo(storage, staff.staff_id, fine_id, photo, datetime.now(UTC))


@staff_router.get(
    "/fines/{fine_id}/photos/{photo_id}/url",
    summary="A short-lived link to a photo of the fine",
    dependencies=[Depends(_no_store)],
    responses={HTTPStatus.NOT_FOUND: _FINE_PHOTO_NOT_FOUND, **_URL_ERRORS},
)
async def get_fine_photo_url(
    staff: Staff, fine_id: UUID, photo_id: UUID, service: StaffFines, storage: Storage
) -> PhotoUrlOut:
    return await service.photo_url(storage, staff.staff_id, fine_id, photo_id)
