import asyncio
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from http import HTTPStatus
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from structlog.testing import capture_logs

from flagman.audit import service as audit
from flagman.audit.domain import ActorType, AuditAction, AuditEventFilter
from flagman.core.config import Settings
from flagman.core.db import create_engine, create_session_factory
from flagman.core.geo import GeoPoint
from flagman.fleet.domain import (
    CarCommand,
    CarCommandDuplicateError,
    CarCommandRejectedError,
    CarState,
    CarStatus,
    TelematicsTimeoutError,
    TelematicsUnavailableError,
)
from flagman.fleet.tests.conftest import FakeGateway, add_car, run_in_db
from flagman.geo import service as geo
from flagman.rentals import api, service
from flagman.rentals.tests.conftest import (
    CAR_POINT,
    FAR_FROM_CAR,
    NEAR_CAR,
    Person,
    car_status,
    execute,
    opened_state,
    problem_type,
    query,
)

pytestmark = pytest.mark.integration


@pytest.fixture
def person(integration_settings: Settings, app_client: TestClient) -> Person:
    return Person(integration_settings, app_client)


@pytest.fixture
def car(integration_settings: Settings) -> str:
    return add_car(integration_settings, CAR_POINT)


@pytest.fixture
def rental(person: Person, car: str) -> str:
    response = person.reserve(car)
    assert response.status_code == HTTPStatus.CREATED
    return str(response.json()["id"])


@pytest.fixture
def inspected(person: Person, rental: str) -> str:
    person.inspect(rental)
    return rental


def _command_until(settings: Settings, rental: str) -> Any:
    rows = query(settings, "SELECT command_until FROM rentals WHERE id = :id", id=uuid.UUID(rental))
    return rows[0].command_until


def _client_events(settings: Settings, client_id: uuid.UUID) -> list[Any]:
    async def read() -> list[Any]:
        engine = create_engine(settings)
        try:
            async with create_session_factory(engine)() as db, db.begin():
                event_filter = AuditEventFilter(
                    actor_id=client_id, action=AuditAction.CAR_COMMAND_SENT
                )
                return list((await audit.list_events(db, event_filter, None, 10)).items)
        finally:
            await engine.dispose()

    return asyncio.run(read())


def test_the_first_opening_starts_the_trip(
    integration_settings: Settings, person: Person, inspected: str, car: str, gateway: FakeGateway
) -> None:
    response = person.command(inspected, "open")

    assert response.status_code == HTTPStatus.OK
    rental = response.json()
    assert rental["status"] == "in_trip"
    assert rental["trip_started_at"] is not None
    assert rental["car"]["doors_locked"] is False
    assert car_status(integration_settings, car) == CarStatus.IN_RENTAL
    assert [command for _, _, command in gateway.calls] == [CarCommand.OPEN]
    assert _command_until(integration_settings, inspected) is None


def test_the_command_is_audited_without_the_clients_location(
    integration_settings: Settings, person: Person, inspected: str, car: str
) -> None:
    person.command(inspected, "open")

    [event] = _client_events(integration_settings, person.id)
    assert event.actor_type is ActorType.CLIENT
    assert event.target.id == uuid.UUID(car)
    assert event.details["command"] == "open"
    assert event.details["outcome"] == "done"
    assert event.details["rental_id"] == inspected
    assert not {"lat", "lon", "distance_m"} & set(event.details)


def test_the_trip_goes_on_after_closing_and_opening_again(
    integration_settings: Settings, person: Person, inspected: str, gateway: FakeGateway
) -> None:
    person.command(inspected, "open")

    closed = person.command(inspected, "close")
    opened = person.command(inspected, "open")

    assert closed.json()["status"] == "in_trip"
    assert opened.json()["status"] == "in_trip"
    assert opened.json()["trip_started_at"] == closed.json()["trip_started_at"]
    assert len(gateway.calls) == 3


def test_the_car_opens_only_after_the_inspection(
    person: Person, rental: str, gateway: FakeGateway
) -> None:
    response = person.command(rental, "open")

    assert response.status_code == HTTPStatus.CONFLICT
    assert problem_type(response) == "inspection-required"
    assert gateway.calls == []


def test_the_car_opens_only_within_reach(
    integration_settings: Settings, person: Person, inspected: str, gateway: FakeGateway
) -> None:
    response = person.command(inspected, "open", at=FAR_FROM_CAR)

    assert response.status_code == HTTPStatus.CONFLICT
    assert problem_type(response) == "too-far-from-car"
    assert gateway.calls == []
    assert person.get(inspected).json()["status"] == "reserved"


def test_a_car_without_fresh_telemetry_does_not_open(
    integration_settings: Settings, person: Person, inspected: str, car: str
) -> None:
    execute(
        integration_settings,
        "UPDATE cars SET state_at = now() - interval '1 hour' WHERE id = :id",
        id=uuid.UUID(car),
    )

    response = person.command(inspected, "open")

    assert response.status_code == HTTPStatus.CONFLICT
    assert problem_type(response) == "car-location-unknown"


@pytest.mark.parametrize(
    "case",
    [
        pytest.param(
            (CarCommandRejectedError("ignition_on"), HTTPStatus.CONFLICT, "car-command-rejected"),
            id="rejected",
        ),
        pytest.param(
            (
                TelematicsUnavailableError(),
                HTTPStatus.SERVICE_UNAVAILABLE,
                "telematics-unavailable",
            ),
            id="unavailable",
        ),
        pytest.param(
            (TelematicsTimeoutError(), HTTPStatus.GATEWAY_TIMEOUT, "telematics-timeout"),
            id="timeout",
        ),
        pytest.param(
            (CarCommandDuplicateError(), HTTPStatus.CONFLICT, "car-command-duplicate"),
            id="duplicate",
        ),
    ],
)
def test_a_failed_command_leaves_the_rental_as_it_was(
    integration_settings: Settings,
    person: Person,
    inspected: str,
    gateway: FakeGateway,
    case: tuple[Exception, int, str],
) -> None:
    gateway.outcome, status, slug = case

    response = person.command(inspected, "open")

    assert response.status_code == status
    assert problem_type(response) == slug
    assert person.get(inspected).json()["status"] == "reserved"
    car = person.get(inspected).json()["car"]["id"]
    assert car_status(integration_settings, car) == CarStatus.RESERVED
    # The client may try again right away.
    assert _command_until(integration_settings, inspected) is None
    [event] = _client_events(integration_settings, person.id)
    assert event.details["outcome"] != "done"


def test_one_command_at_a_time(
    integration_settings: Settings, person: Person, inspected: str, gateway: FakeGateway
) -> None:
    execute(
        integration_settings,
        "UPDATE rentals SET command_until = now() + interval '10 seconds' WHERE id = :id",
        id=uuid.UUID(inspected),
    )

    response = person.command(inspected, "open")

    assert response.status_code == HTTPStatus.CONFLICT
    assert problem_type(response) == "rental-command-in-progress"
    assert gateway.calls == []


def _use_gateway(app_client: TestClient, gateway: FakeGateway) -> None:
    app = app_client.app
    assert isinstance(app, FastAPI)
    app.dependency_overrides[api.get_telematics_gateway] = lambda: gateway


@dataclass
class TimeoutJobDuringCommand(FakeGateway):
    """The car answers only after the reservation's end, and the timeout job runs meanwhile."""

    settings: Settings | None = None
    expired_meanwhile: int | None = None

    async def send(
        self, telematics_id: uuid.UUID, command_id: uuid.UUID, command: CarCommand
    ) -> CarState | None:
        await asyncio.sleep(0.3)
        assert self.settings is not None
        engine = create_engine(self.settings)
        try:
            async with create_session_factory(engine)() as db:
                self.expired_meanwhile = await service.expire_reservations(db, datetime.now(UTC))
        finally:
            await engine.dispose()
        return await super().send(telematics_id, command_id, command)


def test_a_command_holds_the_reservation_while_the_car_thinks(
    integration_settings: Settings, app_client: TestClient, person: Person, inspected: str
) -> None:
    gateway = TimeoutJobDuringCommand(outcome=opened_state(), settings=integration_settings)
    _use_gateway(app_client, gateway)
    execute(
        integration_settings,
        "UPDATE rentals SET reserved_at = now() - interval '45 minutes', "
        "free_wait_until = now() - interval '30 minutes', "
        "expires_at = now() + interval '100 milliseconds' WHERE id = :id",
        id=uuid.UUID(inspected),
    )

    response = person.command(inspected, "open")

    assert gateway.expired_meanwhile == 0
    assert response.status_code == HTTPStatus.OK
    assert response.json()["status"] == "in_trip"


def test_a_blocked_client_cannot_open(person: Person, inspected: str, gateway: FakeGateway) -> None:
    person.block()

    response = person.command(inspected, "open")

    assert response.status_code == HTTPStatus.FORBIDDEN
    assert problem_type(response) == "client-blocked"
    assert gateway.calls == []


def test_a_client_whose_documents_were_revoked_cannot_open(
    integration_settings: Settings, person: Person, inspected: str
) -> None:
    execute(
        integration_settings,
        "UPDATE kyc_applications SET status = 'pending' WHERE user_id = :id",
        id=person.id,
    )

    response = person.command(inspected, "open")

    assert response.status_code == HTTPStatus.FORBIDDEN
    assert problem_type(response) == "kyc-not-approved"


def test_a_cancelled_rental_takes_no_commands(
    person: Person, inspected: str, gateway: FakeGateway
) -> None:
    person.cancel(inspected)

    response = person.command(inspected, "open")

    assert response.status_code == HTTPStatus.CONFLICT
    assert problem_type(response) == "rental-transition-not-allowed"
    assert gateway.calls == []


def test_a_started_trip_cannot_be_cancelled(person: Person, inspected: str) -> None:
    person.command(inspected, "open")

    response = person.cancel(inspected)

    assert response.status_code == HTTPStatus.CONFLICT
    assert problem_type(response) == "rental-transition-not-allowed"


@pytest.mark.parametrize(
    "body",
    [
        {"command": "lock_engine", "lat": 53.9, "lon": 27.5},
        {"command": "open", "lat": 91, "lon": 27.5},
        {"command": "open"},
        {"command": "open", "lat": 53.9, "lon": 27.5, "force": True},
    ],
)
def test_commands_are_checked(person: Person, inspected: str, body: dict[str, Any]) -> None:
    response = person.client.post(
        f"/rentals/{inspected}/commands", json=body, headers=person.headers
    )

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY


def test_commands_are_rate_limited(person: Person, inspected: str) -> None:
    person.command(inspected, "open")
    responses = [person.command(inspected, "close") for _ in range(10)]

    assert responses[-1].status_code == HTTPStatus.TOO_MANY_REQUESTS
    assert "Retry-After" in responses[-1].headers


@dataclass
class AnswersAfterTheHold(FakeGateway):
    """The car answers once the hold and the reservation have both run out."""

    settings: Settings | None = None
    rental_id: uuid.UUID | None = None

    async def send(
        self, telematics_id: uuid.UUID, command_id: uuid.UUID, command: CarCommand
    ) -> CarState | None:
        assert self.settings is not None
        engine = create_engine(self.settings)
        try:
            async with create_session_factory(engine)() as db, db.begin():
                await db.execute(
                    text(
                        "UPDATE rentals SET command_until = now() - interval '1 second', "
                        "reserved_at = now() - interval '46 minutes', "
                        "free_wait_until = now() - interval '31 minutes', "
                        "expires_at = now() - interval '1 second' WHERE id = :id"
                    ),
                    {"id": self.rental_id},
                )
        finally:
            await engine.dispose()
        return await super().send(telematics_id, command_id, command)


def test_an_opened_car_starts_the_trip_even_after_the_hold_ran_out(
    integration_settings: Settings,
    app_client: TestClient,
    person: Person,
    inspected: str,
    car: str,
) -> None:
    gateway = AnswersAfterTheHold(
        outcome=opened_state(), settings=integration_settings, rental_id=uuid.UUID(inspected)
    )
    _use_gateway(app_client, gateway)

    response = person.command(inspected, "open")

    assert response.json()["status"] == "in_trip"
    assert car_status(integration_settings, car) == CarStatus.IN_RENTAL


def _north_of_car(settings: Settings, meters: float) -> GeoPoint:
    """A point `meters` north of the car as PostGIS measures it, the measure the API uses."""

    async def measure(db: AsyncSession) -> float:
        return await geo.distance_m(db, CAR_POINT, GeoPoint(CAR_POINT.lat + 0.001, CAR_POINT.lon))

    per_millidegree = run_in_db(settings, measure)
    return GeoPoint(lat=CAR_POINT.lat + 0.001 * meters / per_millidegree, lon=CAR_POINT.lon)


@pytest.mark.parametrize(
    ("meters", "opens"), [(49.5, True), (50.5, False)], ids=["inside", "outside"]
)
def test_the_reach_of_the_car_is_fifty_meters(
    integration_settings: Settings, person: Person, inspected: str, meters: float, opens: bool
) -> None:
    response = person.command(inspected, "open", at=_north_of_car(integration_settings, meters))

    if opens:
        assert response.status_code == HTTPStatus.OK
    else:
        assert response.status_code == HTTPStatus.CONFLICT
        assert problem_type(response) == "too-far-from-car"


def test_the_clients_location_stays_out_of_the_logs(person: Person, inspected: str) -> None:
    with capture_logs() as logs:
        assert person.command(inspected, "open").status_code == HTTPStatus.OK
        person.command(inspected, "close", at=FAR_FROM_CAR)

    written = repr(logs)
    for point in (NEAR_CAR, FAR_FROM_CAR):
        assert str(point.lat)[:7] not in written
        assert str(point.lon)[:7] not in written
