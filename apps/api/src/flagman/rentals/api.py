from datetime import UTC, datetime
from http import HTTPStatus
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from flagman.audit.dependencies import get_request_origin
from flagman.audit.domain import RequestOrigin
from flagman.core.db import get_session
from flagman.core.errors import RATE_LIMITED_RESPONSE, Problem
from flagman.core.photos import PhotoTooLargeError, photo_request_body, read_photo_body
from flagman.core.ratelimit import RateLimiter, get_rate_limiter
from flagman.core.resources import Resources, get_resources
from flagman.core.storage import ObjectStorage, object_storage_from_settings
from flagman.fleet import service as fleet
from flagman.fleet.ports import TelematicsGateway
from flagman.identity.dependencies import CurrentClient, get_current_client
from flagman.payments import service as payments
from flagman.payments.domain import PaymentsUnavailableError
from flagman.rentals.domain import (
    SIGNED_URL_TTL,
    InspectionPhotoTooLargeError,
    InspectionStage,
    PhotoStorageUnavailableError,
)
from flagman.rentals.schemas import (
    CommandIn,
    CurrentRentalOut,
    DamageIn,
    DebtOut,
    DebtsOut,
    InspectionOut,
    PhotoUrlOut,
    ReceiptOut,
    RentalOut,
    ReserveIn,
)
from flagman.rentals.service import CommandRequest, InspectionRef, RentalService, rental_rules

router = APIRouter(
    prefix="/rentals",
    tags=["rentals"],
    responses={
        HTTPStatus.UNAUTHORIZED: {
            "model": Problem,
            "description": "`urn:flagman:problem:access-token-invalid`: refresh or sign in.",
        },
        HTTPStatus.NOT_FOUND: {
            "model": Problem,
            "description": "`urn:flagman:problem:rental-not-found`, also for another client's.",
        },
    },
)


def get_rental_service(
    db: Annotated[AsyncSession, Depends(get_session)],
    limiter: Annotated[RateLimiter, Depends(get_rate_limiter)],
    resources: Annotated[Resources, Depends(get_resources)],
) -> RentalService:
    try:
        gateway = payments.gateway(resources.settings)
    except PaymentsUnavailableError:
        # Reading a rental works without payments; holding and paying answer 503.
        gateway = None
    return RentalService(
        db=db, limiter=limiter, rules=rental_rules(resources.settings), gateway=gateway
    )


def get_photo_storage(resources: Annotated[Resources, Depends(get_resources)]) -> ObjectStorage:
    storage = object_storage_from_settings(resources.settings)
    if storage is None:
        raise PhotoStorageUnavailableError("No photo storage is configured.")
    return storage


def get_telematics_gateway(
    request: Request, resources: Annotated[Resources, Depends(get_resources)]
) -> TelematicsGateway:
    return fleet.create_telematics_gateway(resources.settings, request.app.state.telematics_client)


def get_rental_car_commander(
    db: Annotated[AsyncSession, Depends(get_session)],
    origin: Annotated[RequestOrigin, Depends(get_request_origin)],
    gateway: Annotated[TelematicsGateway, Depends(get_telematics_gateway)],
    resources: Annotated[Resources, Depends(get_resources)],
) -> fleet.RentalCarCommander:
    return fleet.RentalCarCommander(
        db=db,
        gateway=gateway,
        origin=origin,
        max_skew=fleet.telemetry_clock_skew(resources.settings),
    )


def _no_store(response: Response) -> None:
    # The answer carries a signed link to a photo; a cached copy would hand it out again later.
    response.headers["Cache-Control"] = "no-store"


# The client comes first in every signature: without a token the answer is 401, not 503.
Client = Annotated[CurrentClient, Depends(get_current_client)]
Service = Annotated[RentalService, Depends(get_rental_service)]
Storage = Annotated[ObjectStorage, Depends(get_photo_storage)]
Commander = Annotated[fleet.RentalCarCommander, Depends(get_rental_car_commander)]


def _problem(description: str) -> dict[str, Any]:
    return {"model": Problem, "description": description}


_ADMISSION = (
    "`urn:flagman:problem:kyc-not-approved`: the documents are not approved yet; "
    "`client-blocked`: an operator has blocked the client."
)
_EXPIRED = "`urn:flagman:problem:reservation-expired`: the reservation has been cancelled."
_INSPECTION_CONFLICT = _problem(
    "`urn:flagman:problem:inspection-closed`: submitted already or not at this stage of the "
    "rental; `inspection-photo-limit`, `inspection-damage-limit`, `inspection-incomplete`; "
    + _EXPIRED
)
_STORAGE_UNAVAILABLE = _problem("`urn:flagman:problem:photo-storage-unavailable`.")


@router.post(
    "",
    status_code=HTTPStatus.CREATED,
    summary="Reserve a car from the map",
    description=(
        "The first minutes are free, then the waiting is paid until `expires_at`, when the "
        "reservation is cancelled unless the car has been opened."
    ),
    responses={
        HTTPStatus.FORBIDDEN: _problem(_ADMISSION),
        HTTPStatus.NOT_FOUND: _problem(
            "`urn:flagman:problem:car-not-found`: the car cannot be rented now, the reason is "
            "not told."
        ),
        HTTPStatus.CONFLICT: _problem(
            "`urn:flagman:problem:active-rental-exists`: finish or cancel the current one first; "
            "`card-required`: bind a card; `outstanding-debt`: an earlier rental is not paid."
        ),
        HTTPStatus.TOO_MANY_REQUESTS: RATE_LIMITED_RESPONSE,
    },
)
async def reserve(client: Client, body: ReserveIn, service: Service) -> RentalOut:
    now = datetime.now(UTC)
    return RentalOut.of(await service.reserve(client.user_id, body.car_id, now), now)


@router.get("/current", summary="The client's active rental, to come back to it after a restart")
async def get_current_rental(client: Client, service: Service) -> CurrentRentalOut:
    now = datetime.now(UTC)
    view = await service.current(client.user_id, now)
    return CurrentRentalOut(rental=RentalOut.of(view, now) if view else None)


@router.get("/debts", summary="What the client owes for earlier rentals")
async def get_debts(client: Client, service: Service) -> DebtsOut:
    return DebtsOut(debts=[DebtOut.of(debt) for debt in await service.debts(client.user_id)])


@router.get("/{rental_id}", summary="One of the client's rentals")
async def get_rental(client: Client, rental_id: UUID, service: Service) -> RentalOut:
    now = datetime.now(UTC)
    return RentalOut.of(await service.get(client.user_id, rental_id, now), now)


@router.post(
    "/{rental_id}/cancel",
    summary="Cancel the reservation",
    description=(
        "Cancelling a cancelled reservation returns it as it is. A paid wait is charged: "
        "`charge_status` and `receipt` tell how it went."
    ),
    responses={
        HTTPStatus.CONFLICT: _problem(
            "`urn:flagman:problem:rental-transition-not-allowed`: the trip has started; "
            "`rental-command-in-progress`: the car is answering a command, try again."
        ),
    },
)
async def cancel_reservation(client: Client, rental_id: UUID, service: Service) -> RentalOut:
    now = datetime.now(UTC)
    return RentalOut.of(await service.cancel(client.user_id, rental_id, now), now)


_PAYMENT_UNKNOWN = (
    "`urn:flagman:problem:payment-outcome-unknown`: the provider did not answer, try again; "
    "`payments-unavailable`: payments are not configured."
)


@router.post(
    "/{rental_id}/park",
    summary="Park during the trip, at the parked price",
    description=(
        "Needs fresh telemetry of a car with its ignition off: the parked price is for a car "
        "that stands. Parking a parked rental returns it as it is."
    ),
    responses={
        HTTPStatus.CONFLICT: _problem(
            "One reason at a time: `urn:flagman:problem:rental-transition-not-allowed`: not on "
            "a trip; `rental-command-in-progress`; `car-state-stale`: no fresh telemetry, try "
            "again; `ignition-on`."
        ),
        HTTPStatus.TOO_MANY_REQUESTS: RATE_LIMITED_RESPONSE,
    },
)
async def park(client: Client, rental_id: UUID, service: Service) -> RentalOut:
    now = datetime.now(UTC)
    return RentalOut.of(await service.park(client.user_id, rental_id, now), now)


@router.post(
    "/{rental_id}/resume",
    summary="Drive on after parking, at the driving price",
    description="Resuming a rental on the road returns it as it is.",
    responses={
        HTTPStatus.CONFLICT: _problem(
            "`urn:flagman:problem:rental-transition-not-allowed`: not on a trip; "
            "`rental-command-in-progress`."
        ),
        HTTPStatus.TOO_MANY_REQUESTS: RATE_LIMITED_RESPONSE,
    },
)
async def resume(client: Client, rental_id: UUID, service: Service) -> RentalOut:
    now = datetime.now(UTC)
    return RentalOut.of(await service.resume(client.user_id, rental_id, now), now)


@router.post(
    "/{rental_id}/finish",
    summary="Finish the trip: check and lock the car, issue the receipt and pay",
    description=(
        "Needs the inspection after the trip. Checks the car's fresh telemetry, locks it, then "
        "takes the cost from the hold and charges the rest. A repeat after the end checks "
        "nothing again and never charges twice: it answers the rental with its receipt."
    ),
    responses={
        HTTPStatus.CONFLICT: _problem(
            "One reason at a time: `urn:flagman:problem:inspection-required`; "
            "`car-state-stale`: no fresh telemetry, try again; `outside-end-zone`; "
            "`ignition-on`; `doors-open`; `car-lock-failed`: `detail` is the car's reason; "
            "`rental-command-in-progress`; `rental-transition-not-allowed`: not on a trip."
        ),
        HTTPStatus.TOO_MANY_REQUESTS: RATE_LIMITED_RESPONSE,
        HTTPStatus.SERVICE_UNAVAILABLE: _problem(
            "`urn:flagman:problem:telematics-unavailable`: nothing was done, try again."
        ),
        HTTPStatus.GATEWAY_TIMEOUT: _problem(
            "`urn:flagman:problem:telematics-timeout`: the car may have locked, try again."
        ),
    },
)
async def finish(
    client: Client, rental_id: UUID, service: Service, commander: Commander
) -> RentalOut:
    view = await service.finish(commander, client.user_id, rental_id, datetime.now(UTC))
    return RentalOut.of(view, datetime.now(UTC))


@router.get(
    "/{rental_id}/receipt",
    summary="The receipt of an ended rental",
    responses={
        HTTPStatus.NOT_FOUND: _problem(
            "`urn:flagman:problem:receipt-not-found`: still running, or nothing was billed."
        ),
    },
)
async def get_receipt(client: Client, rental_id: UUID, service: Service) -> ReceiptOut:
    return ReceiptOut.of(await service.receipt(client.user_id, rental_id))


@router.post(
    "/{rental_id}/pay",
    summary="Try to pay the debt of the rental again",
    description=(
        "For a client who has bound another card; the worker retries a debt by itself too. "
        "A rental without a debt is returned as it is."
    ),
    responses={
        HTTPStatus.TOO_MANY_REQUESTS: RATE_LIMITED_RESPONSE,
        HTTPStatus.SERVICE_UNAVAILABLE: _problem(_PAYMENT_UNKNOWN),
    },
)
async def pay_debt(client: Client, rental_id: UUID, service: Service) -> RentalOut:
    view = await service.pay_debt(client.user_id, rental_id, datetime.now(UTC))
    return RentalOut.of(view, datetime.now(UTC))


def get_inspection_ref(client: Client, rental_id: UUID, stage: InspectionStage) -> InspectionRef:
    return InspectionRef(user_id=client.user_id, rental_id=rental_id, stage=stage)


# Carries the client, so it comes first in the signatures as the client does.
Ref = Annotated[InspectionRef, Depends(get_inspection_ref)]


@router.get("/{rental_id}/inspections/{stage}", summary="The inspection of the car so far")
async def get_inspection(ref: Ref, service: Service) -> InspectionOut:
    return InspectionOut.of(await service.inspection(ref, datetime.now(UTC)))


async def _read_photo(request: Request) -> bytes:
    try:
        return await read_photo_body(request)
    except PhotoTooLargeError as error:
        raise InspectionPhotoTooLargeError from error


@router.post(
    "/{rental_id}/inspections/{stage}/photos",
    summary="Add a photo of the car to the inspection",
    description="Photos are evidence: they cannot be deleted or replaced once uploaded.",
    openapi_extra=photo_request_body("The photo of the car"),
    responses={
        HTTPStatus.CONFLICT: _INSPECTION_CONFLICT,
        HTTPStatus.REQUEST_ENTITY_TOO_LARGE: _problem(
            "`urn:flagman:problem:inspection-photo-too-large`."
        ),
        HTTPStatus.UNSUPPORTED_MEDIA_TYPE: _problem(
            "`urn:flagman:problem:inspection-photo-type-unsupported`: not a photo."
        ),
        HTTPStatus.TOO_MANY_REQUESTS: RATE_LIMITED_RESPONSE,
        HTTPStatus.SERVICE_UNAVAILABLE: _STORAGE_UNAVAILABLE,
    },
)
async def add_inspection_photo(
    ref: Ref, request: Request, service: Service, storage: Storage
) -> InspectionOut:
    await service.count_upload(ref.user_id)
    photo = await _read_photo(request)
    return InspectionOut.of(await service.add_photo(storage, ref, photo, datetime.now(UTC)))


@router.get(
    "/{rental_id}/inspections/{stage}/photos/{photo_id}/url",
    summary="A short-lived link to a photo of the inspection",
    dependencies=[Depends(_no_store)],
    responses={
        HTTPStatus.NOT_FOUND: _problem(
            "`urn:flagman:problem:rental-not-found`, `inspection-photo-not-found`."
        ),
        HTTPStatus.TOO_MANY_REQUESTS: RATE_LIMITED_RESPONSE,
        HTTPStatus.SERVICE_UNAVAILABLE: _STORAGE_UNAVAILABLE,
    },
)
async def get_inspection_photo_url(
    ref: Ref, photo_id: UUID, service: Service, storage: Storage
) -> PhotoUrlOut:
    url = await service.photo_url(storage, ref, photo_id)
    return PhotoUrlOut(url=url, expires_in=int(SIGNED_URL_TTL.total_seconds()))


@router.post(
    "/{rental_id}/inspections/{stage}/damages",
    summary="Mark a damage of the car in the inspection",
    responses={HTTPStatus.CONFLICT: _INSPECTION_CONFLICT},
)
async def add_inspection_damage(ref: Ref, body: DamageIn, service: Service) -> InspectionOut:
    inspection = await service.add_damage(ref, body.side, body.comment, datetime.now(UTC))
    return InspectionOut.of(inspection)


@router.post(
    "/{rental_id}/inspections/{stage}/submit",
    summary="Finish the inspection; nothing can be added to it after",
    responses={HTTPStatus.CONFLICT: _INSPECTION_CONFLICT},
)
async def submit_inspection(ref: Ref, service: Service) -> InspectionOut:
    return InspectionOut.of(await service.submit_inspection(ref, datetime.now(UTC)))


@router.post(
    "/{rental_id}/commands",
    summary="Open or close the car of the rental",
    description=(
        "Only within reach of the car, after the inspection. The first opening holds the "
        "tariff's amount on the card and starts the trip. Waits for the car; a failed command "
        "leaves the rental as it was."
    ),
    responses={
        HTTPStatus.FORBIDDEN: _problem(_ADMISSION),
        HTTPStatus.CONFLICT: _problem(
            "`urn:flagman:problem:inspection-required`; `too-far-from-car`; "
            "`car-location-unknown`: no fresh telemetry from the car; "
            "`rental-command-in-progress`; `rental-transition-not-allowed`: the rental is over; "
            "`car-command-rejected`: the car refused, `detail` is its reason; "
            "`card-required`: no usable card for the hold; " + _EXPIRED
        ),
        HTTPStatus.PAYMENT_REQUIRED: _problem(
            "`urn:flagman:problem:payment-hold-declined`: the car stays closed; change the card."
        ),
        HTTPStatus.TOO_MANY_REQUESTS: RATE_LIMITED_RESPONSE,
        HTTPStatus.SERVICE_UNAVAILABLE: _problem(
            "`urn:flagman:problem:telematics-unavailable`: nothing was done, try again; "
            + _PAYMENT_UNKNOWN
        ),
        HTTPStatus.GATEWAY_TIMEOUT: _problem(
            "`urn:flagman:problem:telematics-timeout`: the car may or may not have done it."
        ),
    },
)
async def send_command(
    client: Client,
    rental_id: UUID,
    body: CommandIn,
    service: Service,
    commander: Commander,
) -> RentalOut:
    request = CommandRequest(
        user_id=client.user_id, rental_id=rental_id, command=body.command, client_at=body.point()
    )
    view = await service.send_command(commander, request, datetime.now(UTC))
    return RentalOut.of(view, datetime.now(UTC))
