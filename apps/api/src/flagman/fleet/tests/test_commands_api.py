import asyncio
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from http import HTTPStatus
from typing import Any, NamedTuple

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from flagman.audit import service as audit
from flagman.audit.domain import AuditAction, AuditEventFilter
from flagman.core.app import create_app
from flagman.core.config import Settings
from flagman.core.db import create_engine, create_session_factory
from flagman.core.geo import GeoPoint
from flagman.core.ratelimit import Limit
from flagman.fleet import api
from flagman.fleet.domain import (
    CarCommand,
    CarCommandDuplicateError,
    CarCommandRejectedError,
    CarState,
    CarStatus,
    TelematicsTimeoutError,
    TelematicsUnavailableError,
)
from flagman.fleet.tests.conftest import FakeGateway, add_car, bearer, client_token, staff_token
from flagman.identity.domain import StaffRole

pytestmark = pytest.mark.integration

POINT = GeoPoint(lat=53.9023, lon=27.5615)
COMMENT = "Client called: cannot open the car, rental R-1042"


def _opened() -> CarState:
    return CarState(POINT, 77, False, False, False, datetime.now(UTC))


@pytest.fixture
def gateway() -> FakeGateway:
    return FakeGateway(outcome=_opened())


@pytest.fixture
def client(integration_settings: Settings, gateway: FakeGateway) -> Iterator[TestClient]:
    app = create_app(integration_settings)
    app.dependency_overrides[api.get_telematics_gateway] = lambda: gateway
    with TestClient(app, base_url="https://testserver") as client:
        yield client


class Caller:
    def __init__(self, settings: Settings, role: StaffRole) -> None:
        self.id = uuid.uuid4()
        self.headers = bearer(staff_token(settings, role, staff_id=self.id))


@pytest.fixture
def operator(integration_settings: Settings) -> Caller:
    return Caller(integration_settings, StaffRole.OPERATOR)


def _url(car_id: str) -> str:
    return f"/admin/fleet/cars/{car_id}/commands"


def _body(**overrides: Any) -> dict[str, Any]:
    return {"command": "open", "comment": COMMENT, **overrides}


def _events(settings: Settings, staff_id: uuid.UUID) -> list[Any]:
    async def read() -> list[Any]:
        engine = create_engine(settings)
        try:
            async with create_session_factory(engine)() as db, db.begin():
                event_filter = AuditEventFilter(
                    actor_id=staff_id, action=AuditAction.CAR_COMMAND_SENT
                )
                return list((await audit.list_events(db, event_filter, None, 10)).items)
        finally:
            await engine.dispose()

    return asyncio.run(read())


@pytest.mark.parametrize("role", list(StaffRole))
def test_every_staff_role_sends_commands(
    client: TestClient, integration_settings: Settings, gateway: FakeGateway, role: StaffRole
) -> None:
    car_id = add_car(integration_settings, POINT)
    caller = Caller(integration_settings, role)

    response = client.post(_url(car_id), json=_body(), headers=caller.headers)

    assert response.status_code == HTTPStatus.OK, response.text
    assert len(gateway.calls) == 1


def test_a_done_command_returns_the_car_after_it(
    client: TestClient, integration_settings: Settings, gateway: FakeGateway, operator: Caller
) -> None:
    car_id = add_car(integration_settings, POINT, battery_pct=80)

    response = client.post(_url(car_id), json=_body(), headers=operator.headers)

    assert response.status_code == HTTPStatus.OK, response.text
    body = response.json()
    assert body["car"]["id"] == car_id
    assert body["car"]["state"]["doors_locked"] is False
    assert body["car"]["state"]["battery_pct"] == 77
    telematics_id, command_id, command = gateway.calls[0]
    assert str(command_id) == body["command_id"]
    assert command is CarCommand.OPEN
    assert telematics_id != uuid.UUID(car_id)


def test_every_command_gets_a_fresh_id(
    client: TestClient, integration_settings: Settings, gateway: FakeGateway, operator: Caller
) -> None:
    car_id = add_car(integration_settings, POINT)

    for _ in range(2):
        client.post(_url(car_id), json=_body(), headers=operator.headers)

    assert gateway.calls[0][1] != gateway.calls[1][1]


def test_a_done_command_is_audited(
    client: TestClient, integration_settings: Settings, operator: Caller
) -> None:
    car_id = add_car(integration_settings, POINT)

    response = client.post(
        _url(car_id), json=_body(command="lock_engine"), headers=operator.headers
    )

    [event] = _events(integration_settings, operator.id)
    assert str(event.target.id) == car_id
    assert event.target.type == "car"
    assert event.details == {
        "command": "lock_engine",
        "outcome": "done",
        "reason": None,
        "command_id": response.json()["command_id"],
        "car_status": "available",
        "during_active_rental": False,
        "comment": COMMENT,
    }


class Failure(NamedTuple):
    error: Exception
    status: HTTPStatus
    problem: str
    outcome: str


@pytest.mark.parametrize(
    "failure",
    [
        Failure(
            CarCommandRejectedError("ignition_on"),
            HTTPStatus.CONFLICT,
            "car-command-rejected",
            "rejected",
        ),
        Failure(
            CarCommandDuplicateError(), HTTPStatus.CONFLICT, "car-command-duplicate", "duplicate"
        ),
        Failure(
            TelematicsUnavailableError(),
            HTTPStatus.SERVICE_UNAVAILABLE,
            "telematics-unavailable",
            "unavailable",
        ),
        Failure(
            TelematicsTimeoutError(), HTTPStatus.GATEWAY_TIMEOUT, "telematics-timeout", "timed_out"
        ),
    ],
    ids=["rejected", "duplicate", "unavailable", "timed-out"],
)
def test_a_failed_command_is_a_problem_and_still_audited(
    client: TestClient,
    integration_settings: Settings,
    gateway: FakeGateway,
    operator: Caller,
    failure: Failure,
) -> None:
    car_id = add_car(integration_settings, POINT)
    gateway.outcome = failure.error

    response = client.post(_url(car_id), json=_body(command="close"), headers=operator.headers)

    assert response.status_code == failure.status
    assert response.json()["type"] == f"urn:flagman:problem:{failure.problem}"
    [event] = _events(integration_settings, operator.id)
    assert event.details["outcome"] == failure.outcome
    assert event.details["command"] == "close"


def test_a_refusal_tells_the_cars_reason_and_leaves_the_state(
    client: TestClient, integration_settings: Settings, gateway: FakeGateway, operator: Caller
) -> None:
    car_id = add_car(integration_settings, POINT)
    gateway.outcome = CarCommandRejectedError("ignition_on")

    response = client.post(_url(car_id), json=_body(command="close"), headers=operator.headers)

    assert response.json()["detail"] == "ignition_on"
    [event] = _events(integration_settings, operator.id)
    assert event.details["reason"] == "ignition_on"
    car = client.get(f"/admin/fleet/cars/{car_id}", headers=operator.headers).json()
    assert car["state"]["doors_locked"] is True


@pytest.mark.parametrize("status", [CarStatus.RESERVED, CarStatus.IN_RENTAL])
def test_a_car_a_client_holds_needs_confirmation(
    client: TestClient,
    integration_settings: Settings,
    gateway: FakeGateway,
    operator: Caller,
    status: CarStatus,
) -> None:
    car_id = add_car(integration_settings, POINT, status=status)

    refused = client.post(_url(car_id), json=_body(), headers=operator.headers)

    assert refused.status_code == HTTPStatus.CONFLICT
    assert refused.json()["type"] == "urn:flagman:problem:car-in-active-rental"
    assert gateway.calls == []
    assert _events(integration_settings, operator.id) == []

    confirmed = client.post(
        _url(car_id), json=_body(confirm_active_rental=True), headers=operator.headers
    )

    assert confirmed.status_code == HTTPStatus.OK, confirmed.text
    [event] = _events(integration_settings, operator.id)
    assert event.details["during_active_rental"] is True
    assert event.details["car_status"] == status.value


@pytest.mark.parametrize("status", [CarStatus.AVAILABLE, CarStatus.SERVICE, CarStatus.OFFLINE])
def test_other_cars_need_no_confirmation(
    client: TestClient, integration_settings: Settings, operator: Caller, status: CarStatus
) -> None:
    car_id = add_car(integration_settings, POINT, status=status)

    response = client.post(_url(car_id), json=_body(), headers=operator.headers)

    assert response.status_code == HTTPStatus.OK, response.text


def test_an_unknown_car_is_404_and_nothing_is_sent(
    client: TestClient, gateway: FakeGateway, operator: Caller
) -> None:
    response = client.post(_url(str(uuid.uuid4())), json=_body(), headers=operator.headers)

    assert response.status_code == HTTPStatus.NOT_FOUND
    assert gateway.calls == []


@pytest.mark.parametrize(
    "body",
    [
        {"command": "open"},
        _body(comment="  ok  "),
        _body(comment="x" * 301),
        _body(comment="Client +375291234567 called"),
        _body(comment="Call back ivan@example.com"),
        _body(command="self_destruct"),
        _body(price=0),
    ],
    ids=[
        "no-comment",
        "too-short",
        "too-long",
        "phone",
        "email",
        "unknown-command",
        "unknown-field",
    ],
)
def test_a_wrong_command_is_422_and_nothing_is_sent(
    client: TestClient,
    integration_settings: Settings,
    gateway: FakeGateway,
    operator: Caller,
    body: dict[str, Any],
) -> None:
    car_id = add_car(integration_settings, POINT)

    response = client.post(_url(car_id), json=body, headers=operator.headers)

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY, response.text
    assert gateway.calls == []
    assert "375291234567" not in response.text
    assert "ivan@example.com" not in response.text


def test_clients_and_strangers_cannot_send_commands(
    client: TestClient, integration_settings: Settings, gateway: FakeGateway
) -> None:
    car_id = add_car(integration_settings, POINT)

    as_client = client.post(
        _url(car_id), json=_body(), headers=bearer(client_token(integration_settings))
    )
    anonymous = client.post(_url(car_id), json=_body())

    assert as_client.status_code == HTTPStatus.UNAUTHORIZED
    assert anonymous.status_code == HTTPStatus.UNAUTHORIZED
    assert gateway.calls == []


def test_commands_are_rate_limited(
    client: TestClient,
    integration_settings: Settings,
    operator: Caller,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(api, "COMMAND_LIMIT", Limit(f"car-commands-test-{uuid.uuid4()}", 2, 60))
    car_id = add_car(integration_settings, POINT)

    for _ in range(2):
        client.post(_url(car_id), json=_body(), headers=operator.headers)
    limited = client.post(_url(car_id), json=_body(), headers=operator.headers)

    assert limited.status_code == HTTPStatus.TOO_MANY_REQUESTS
    assert "Retry-After" in limited.headers


def test_without_telematics_settings_commands_are_unavailable(
    integration_settings: Settings, operator: Caller
) -> None:
    car_id = add_car(integration_settings, POINT)
    app: FastAPI = create_app(integration_settings)

    with TestClient(app, base_url="https://testserver") as client:
        response = client.post(_url(car_id), json=_body(), headers=operator.headers)

    assert response.status_code == HTTPStatus.SERVICE_UNAVAILABLE
    assert response.json()["type"] == "urn:flagman:problem:telematics-unavailable"
    assert _events(integration_settings, operator.id) == []
