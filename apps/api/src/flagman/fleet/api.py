from datetime import UTC, datetime
from http import HTTPStatus
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from flagman.core.db import get_session
from flagman.core.errors import Problem
from flagman.core.ratelimit import Limit, RateLimiter, get_rate_limiter
from flagman.core.resources import Resources, get_resources
from flagman.fleet import service
from flagman.fleet.domain import (
    AvailabilityRules,
    TelemetryTooLargeError,
    TelemetryUnavailableError,
)
from flagman.fleet.emulator import EmulatorTelemetryReceiver
from flagman.fleet.ports import TelemetryReceiver, TelemetryWebhook
from flagman.fleet.schemas import (
    MAX_TELEMETRY_BODY_BYTES,
    ClientPoint,
    MapCarList,
    MapCarOut,
    MapCarsQuery,
    TelemetryReceived,
)
from flagman.identity.dependencies import CurrentClient, get_current_client

router = APIRouter(prefix="/fleet", tags=["fleet"])


def get_telemetry_receiver(
    resources: Annotated[Resources, Depends(get_resources)],
) -> TelemetryReceiver:
    secret = resources.settings.emulator_telemetry_secret
    if secret is None:
        raise TelemetryUnavailableError("No telemetry secret is configured.")
    return EmulatorTelemetryReceiver(
        secret=secret.get_secret_value().encode(),
        tolerance_s=resources.settings.telematics_signature_tolerance_seconds,
    )


async def _read_body(request: Request) -> bytes:
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > MAX_TELEMETRY_BODY_BYTES:
        raise TelemetryTooLargeError
    body = bytearray()
    # Chunked requests have no length up front, so the limit is also enforced while reading.
    async for chunk in request.stream():
        body += chunk
        if len(body) > MAX_TELEMETRY_BODY_BYTES:
            raise TelemetryTooLargeError
    return bytes(body)


def _target(request: Request) -> bytes:
    # The bytes the client sent: a decoded and re-encoded path could differ from what was signed.
    path: bytes = request.scope.get("raw_path") or request.url.path.encode()
    query: bytes = request.scope.get("query_string", b"")
    return path + b"?" + query if query else path


# Called by telematics, not by our clients: kept out of the OpenAPI schema and the generated client.
@router.post("/telemetry", include_in_schema=False)
async def receive_telemetry(
    request: Request,
    receiver: Annotated[TelemetryReceiver, Depends(get_telemetry_receiver)],
    resources: Annotated[Resources, Depends(get_resources)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> TelemetryReceived:
    webhook = TelemetryWebhook(
        method=request.method,
        target=_target(request),
        headers=request.headers,
        body=await _read_body(request),
    )
    now = datetime.now(UTC)
    try:
        snapshots = receiver.parse(webhook, now)
    except ValidationError as error:
        raise RequestValidationError(
            [{**item, "loc": ("body", *item["loc"])} for item in error.errors(include_input=False)]
        ) from error
    report = await service.receive_telemetry(
        db, snapshots, now, service.telemetry_clock_skew(resources.settings)
    )
    return TelemetryReceived(
        applied=report.applied,
        stale=report.stale,
        unknown_car=report.unknown_car,
        from_future=report.from_future,
    )


# A client pans the map and the app refreshes it every 30 s: about one request per second at most.
MAP_LIMIT = Limit("map-cars-user", 300, 300)

_NOT_ON_MAP: dict[str, Any] = {
    "model": Problem,
    "description": (
        "`urn:flagman:problem:car-not-found`: no such car, or it cannot be rented now; "
        "the reason is not told."
    ),
}

# Car ids in the path are fine: the endpoint shows only what the map shows to any client.
map_router = APIRouter(
    prefix="/map/cars",
    tags=["map"],
    responses={
        HTTPStatus.UNAUTHORIZED: {
            "model": Problem,
            "description": "`urn:flagman:problem:access-token-invalid`: sign in as a client.",
        },
        HTTPStatus.TOO_MANY_REQUESTS: {
            "model": Problem,
            "description": "`urn:flagman:problem:rate-limited`: wait for `Retry-After` seconds.",
        },
    },
)


async def limit_map_requests(
    client: Annotated[CurrentClient, Depends(get_current_client)],
    limiter: Annotated[RateLimiter, Depends(get_rate_limiter)],
) -> CurrentClient:
    await limiter.hit(MAP_LIMIT, str(client.user_id))
    return client


def get_availability_rules(
    resources: Annotated[Resources, Depends(get_resources)],
) -> AvailabilityRules:
    return service.availability_rules(resources.settings)


MapClient = Annotated[CurrentClient, Depends(limit_map_requests)]
Db = Annotated[AsyncSession, Depends(get_session)]
Rules = Annotated[AvailabilityRules, Depends(get_availability_rules)]


@map_router.get(
    "",
    summary="Cars a client may rent in the visible part of the map or near them",
    description=(
        "Give the four edges of the area, the client's `lat` and `lon`, or both. With the point "
        "the cars come nearest first and carry `distance_m`. KYC is not needed to look."
    ),
)
async def list_map_cars(
    client: MapClient, db: Db, rules: Rules, query: Annotated[MapCarsQuery, Query()]
) -> MapCarList:
    cars = await service.find_cars_on_map(db, rules, datetime.now(UTC), query.query())
    return MapCarList(items=[MapCarOut.of(car) for car in cars])


@map_router.get(
    "/{car_id}",
    summary="One car of the map",
    responses={HTTPStatus.NOT_FOUND: _NOT_ON_MAP},
)
async def get_map_car(
    car_id: UUID,
    client: MapClient,
    db: Db,
    rules: Rules,
    point: Annotated[ClientPoint, Query()],
) -> MapCarOut:
    car = await service.get_car_on_map(db, car_id, rules, datetime.now(UTC), point.point())
    return MapCarOut.of(car)
