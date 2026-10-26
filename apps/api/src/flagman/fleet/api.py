import asyncio
import contextlib
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from http import HTTPStatus
from typing import Annotated, Any
from uuid import UUID

import httpx2
from fastapi import APIRouter, Depends, FastAPI, Query, Request, WebSocket
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from flagman.audit.dependencies import get_request_origin
from flagman.audit.domain import RequestOrigin
from flagman.core.db import get_session
from flagman.core.errors import RATE_LIMITED_RESPONSE, Problem
from flagman.core.ratelimit import Limit, RateLimiter, get_rate_limiter
from flagman.core.resources import Resources, get_resources
from flagman.fleet import service
from flagman.fleet.domain import (
    AvailabilityRules,
    CarOnMap,
    TelematicsUnavailableError,
    TelemetryTooLargeError,
    TelemetryUnavailableError,
)
from flagman.fleet.emulator import EmulatorTelematicsGateway, EmulatorTelemetryReceiver
from flagman.fleet.ports import TelematicsGateway, TelemetryReceiver, TelemetryWebhook
from flagman.fleet.schemas import (
    MAX_TELEMETRY_BODY_BYTES,
    CarCommandIn,
    CarCommandOut,
    ClientPoint,
    MapCarList,
    MapCarOut,
    MapCarsQuery,
    MapTariffOut,
    StaffCarList,
    StaffCarOut,
    StaffCarsQuery,
    TelemetryReceived,
)
from flagman.fleet.stream import FleetHub, StaffStream
from flagman.identity.dependencies import (
    CurrentClient,
    CurrentStaff,
    get_current_client,
    require_live_staff_session,
    require_staff_role,
)
from flagman.identity.domain import StaffRole
from flagman.pricing import service as pricing

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
    tariffs = await _map_tariffs(db, cars)
    return MapCarList(items=[MapCarOut.of(car, tariffs.get(car.model_id)) for car in cars])


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
    tariffs = await _map_tariffs(db, [car])
    return MapCarOut.of(car, tariffs.get(car.model_id))


async def _map_tariffs(db: AsyncSession, cars: list[CarOnMap]) -> dict[UUID, MapTariffOut]:
    # The tariffs belong to pricing, which reads models from the fleet: asking it from here keeps
    # the dependency one way.
    versions = await pricing.current_versions(db, {car.model_id for car in cars})
    return {
        model_id: MapTariffOut(
            driving_per_minute=version.prices.driving_per_minute,
            parked_per_minute=version.prices.parked_per_minute,
        )
        for model_id, version in versions.items()
    }


@contextlib.asynccontextmanager
async def _run_fleet(app: FastAPI) -> AsyncIterator[None]:
    # Inside the app's own lifespan, so the process-wide resources exist already.
    resources: Resources = app.state.resources
    hub = FleetHub(resources.settings, resources.session_factory)
    app.state.fleet_hub = hub
    task = asyncio.create_task(hub.run())
    # The gateway bounds each command as a whole; the client's own limits only keep a dead
    # connection from lingering.
    timeout = resources.settings.telematics_command_timeout_seconds
    async with httpx2.AsyncClient(timeout=timeout) as telematics_client:
        app.state.telematics_client = telematics_client
        try:
            yield
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task


# Car ids in the path are fine: every staff member sees the whole fleet, nobody else gets past
# the role check.
staff_router = APIRouter(
    prefix="/admin/fleet",
    tags=["fleet"],
    lifespan=_run_fleet,
    responses={
        HTTPStatus.UNAUTHORIZED: {
            "model": Problem,
            "description": "`urn:flagman:problem:access-token-invalid`: sign in as staff.",
        },
        HTTPStatus.FORBIDDEN: {
            "model": Problem,
            "description": "`urn:flagman:problem:forbidden`: not a staff role that sees the fleet.",
        },
    },
)

FLEET_VIEWERS = (StaffRole.OPERATOR, StaffRole.TECHNICIAN, StaffRole.ADMIN)
FleetViewer = Annotated[CurrentStaff, Depends(require_staff_role(*FLEET_VIEWERS))]
# Technicians too: they open and immobilize the cars they service on the spot.
CAR_COMMANDERS = (StaffRole.OPERATOR, StaffRole.TECHNICIAN, StaffRole.ADMIN)


@staff_router.get(
    "/cars",
    summary="The fleet with the last known state of every car",
    description=(
        "Changes after this answer come over `/admin/fleet/stream` (docs/adr/0017). "
        "Freshness of telemetry is not a field: it changes with time, so the panel counts it "
        "from `as_of` and `rules`."
    ),
)
async def list_staff_cars(
    staff: FleetViewer, db: Db, rules: Rules, query: Annotated[StaffCarsQuery, Query()]
) -> StaffCarList:
    cars = await service.list_cars_for_staff(db, rules, query.filter())
    return StaffCarList.of(cars, rules, datetime.now(UTC))


@staff_router.get(
    "/cars/{car_id}",
    summary="One car of the fleet",
    responses={
        HTTPStatus.NOT_FOUND: {
            "model": Problem,
            "description": "`urn:flagman:problem:car-not-found`.",
        }
    },
)
async def get_staff_car(car_id: UUID, staff: FleetViewer, db: Db) -> StaffCarOut:
    return StaffCarOut.of(await service.get_car(db, car_id))


def get_telematics_gateway(
    request: Request, resources: Annotated[Resources, Depends(get_resources)]
) -> TelematicsGateway:
    settings = resources.settings
    if settings.emulator_url is None or settings.emulator_command_secret is None:
        raise TelematicsUnavailableError("Car commands are not configured.")
    return EmulatorTelematicsGateway(
        client=request.app.state.telematics_client,
        base_url=settings.emulator_url,
        secret=settings.emulator_command_secret.get_secret_value().encode(),
        timeout_s=settings.telematics_command_timeout_seconds,
    )


# A command per car is a matter of seconds of thought; dozens a minute are a script or a mistake.
COMMAND_LIMIT = Limit("car-commands-staff", 20, 60)


async def limit_commands(
    staff: Annotated[CurrentStaff, Depends(require_live_staff_session(*CAR_COMMANDERS))],
    limiter: Annotated[RateLimiter, Depends(get_rate_limiter)],
) -> CurrentStaff:
    await limiter.hit(COMMAND_LIMIT, str(staff.staff_id))
    return staff


def get_car_commander(
    staff: Annotated[CurrentStaff, Depends(limit_commands)],
    db: Db,
    origin: Annotated[RequestOrigin, Depends(get_request_origin)],
    gateway: Annotated[TelematicsGateway, Depends(get_telematics_gateway)],
    resources: Annotated[Resources, Depends(get_resources)],
) -> service.CarCommander:
    return service.CarCommander(
        db=db,
        gateway=gateway,
        staff_id=staff.staff_id,
        origin=origin,
        max_skew=service.telemetry_clock_skew(resources.settings),
    )


@staff_router.post(
    "/cars/{car_id}/commands",
    summary="Send a command to a car",
    description=(
        "Waits for the car. Every command that reached telematics goes to the audit log and to "
        "everyone on `/admin/fleet/stream`, whatever its outcome."
    ),
    responses={
        HTTPStatus.NOT_FOUND: {
            "model": Problem,
            "description": "`urn:flagman:problem:car-not-found`.",
        },
        HTTPStatus.CONFLICT: {
            "model": Problem,
            "description": (
                "`urn:flagman:problem:car-in-active-rental`: nothing was sent, repeat with "
                "`confirm_active_rental`. `car-command-rejected`: the car refused, `detail` is its "
                "reason code, e.g. `ignition_on`. `car-command-duplicate`: telematics had seen "
                "the command."
            ),
        },
        HTTPStatus.TOO_MANY_REQUESTS: RATE_LIMITED_RESPONSE,
        HTTPStatus.SERVICE_UNAVAILABLE: {
            "model": Problem,
            "description": "`urn:flagman:problem:telematics-unavailable`: nothing was done.",
        },
        HTTPStatus.GATEWAY_TIMEOUT: {
            "model": Problem,
            "description": (
                "`urn:flagman:problem:telematics-timeout`: the car may or may not have done it; "
                "check its state before sending again."
            ),
        },
    },
)
async def send_car_command(
    car_id: UUID,
    body: CarCommandIn,
    commander: Annotated[service.CarCommander, Depends(get_car_commander)],
) -> CarCommandOut:
    request = service.CarCommandRequest(
        car_id=car_id,
        command=body.command,
        comment=body.comment,
        confirm_active_rental=body.confirm_active_rental,
    )
    result = await commander.send(request, datetime.now(UTC))
    return CarCommandOut(command_id=result.command_id, car=StaffCarOut.of(result.car))


# Browsers cannot set headers on a WebSocket, so the token comes in the first message instead of
# the URL, where proxies and logs would keep it (docs/adr/0017).
@staff_router.websocket("/stream")
async def stream_fleet(
    websocket: WebSocket,
    resources: Annotated[Resources, Depends(get_resources)],
    rules: Rules,
) -> None:
    hub: FleetHub = websocket.app.state.fleet_hub
    await StaffStream(
        websocket=websocket,
        hub=hub,
        settings=resources.settings,
        session_factory=resources.session_factory,
        rules=rules,
        roles=frozenset(FLEET_VIEWERS),
    ).serve()
