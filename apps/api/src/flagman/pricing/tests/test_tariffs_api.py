import asyncio
import uuid
from collections.abc import Awaitable, Callable, Iterator
from datetime import UTC, datetime, timedelta
from http import HTTPStatus
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from flagman.audit.models import AuditEvent
from flagman.core.app import create_app
from flagman.core.config import Settings
from flagman.core.db import create_engine, create_session_factory
from flagman.fleet import repository as fleet_repository
from flagman.fleet.domain import CarModelSpec
from flagman.identity.domain import (
    AccessClaims,
    StaffAccessClaims,
    StaffRole,
    issue_access_token,
    issue_staff_access_token,
)
from flagman.pricing import service
from flagman.pricing.domain import (
    Interval,
    IntervalKind,
    TariffNotFoundError,
    TariffVersionNotFoundError,
)
from flagman.pricing.seed import DEMO_PRICES

pytestmark = pytest.mark.integration

URL = "/admin/tariffs"
PRICES = {
    "driving_per_minute": 39,
    "parked_per_minute": 10,
    "waiting_per_minute": 12,
    "free_waiting_minutes": 15,
    "hold_amount": 3000,
}


@pytest.fixture
def client(integration_settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(integration_settings), base_url="https://testserver") as client:
        yield client


class Caller:
    def __init__(self, settings: Settings, role: StaffRole | None) -> None:
        self.id = uuid.uuid4()
        secret = settings.api_jwt_secret.get_secret_value()
        now = datetime.now(UTC)
        if role is None:
            token = issue_access_token(AccessClaims(self.id, uuid.uuid4()), secret, now)
        else:
            token = issue_staff_access_token(
                StaffAccessClaims(self.id, uuid.uuid4(), role), secret, now
            )
        self.headers = {"Authorization": f"Bearer {token}"}


@pytest.fixture
def admin(integration_settings: Settings) -> Caller:
    return Caller(integration_settings, StaffRole.ADMIN)


@pytest.fixture
def operator(integration_settings: Settings) -> Caller:
    return Caller(integration_settings, StaffRole.OPERATOR)


def _run[T](settings: Settings, work: Callable[[AsyncSession], Awaitable[T]]) -> T:
    async def run() -> T:
        engine = create_engine(settings)
        try:
            async with create_session_factory(engine)() as db:
                return await work(db)
        finally:
            await engine.dispose()

    return asyncio.run(run())


@pytest.fixture
def model_id(integration_settings: Settings) -> uuid.UUID:
    """A car model of its own for each test: a model has one tariff at most."""
    spec = CarModelSpec("Test", f"Model {uuid.uuid4().hex[:8]}", 5, 50_000, 400, None)

    async def add(db: AsyncSession) -> uuid.UUID:
        async with db.begin():
            created, _ = await fleet_repository.ensure_model(db, spec, datetime.now(UTC))
            return created

    return _run(integration_settings, add)


def _create(client: TestClient, admin: Caller, model_id: uuid.UUID, **prices: int) -> Any:
    body = {"model_id": str(model_id), **PRICES, **prices}
    response = client.post(URL, json=body, headers=admin.headers)
    assert response.status_code == HTTPStatus.CREATED, response.text
    return response.json()


def _update(client: TestClient, admin: Caller, tariff_id: str, based_on: int, **prices: int) -> Any:
    body = {"based_on_version": based_on, **PRICES, **prices}
    return client.put(f"{URL}/{tariff_id}", json=body, headers=admin.headers)


def _audit_events(settings: Settings, tariff_id: str) -> list[tuple[str, uuid.UUID | None, Any]]:
    async def read(db: AsyncSession) -> list[tuple[str, uuid.UUID | None, Any]]:
        async with db.begin():
            rows = await db.execute(
                select(AuditEvent.action, AuditEvent.actor_id, AuditEvent.details)
                .where(AuditEvent.target_id == uuid.UUID(tariff_id))
                .order_by(AuditEvent.occurred_at, AuditEvent.id)
            )
            return [(action, actor, details) for action, actor, details in rows]

    return _run(settings, read)


def test_admin_creates_a_tariff(client: TestClient, admin: Caller, model_id: uuid.UUID) -> None:
    tariff = _create(client, admin, model_id)

    assert tariff["model"]["id"] == str(model_id)
    assert tariff["model"]["brand"] == "Test"
    assert tariff["current"]["number"] == 1
    assert tariff["current"]["prices"] == PRICES
    assert tariff["current"]["created_by"] == str(admin.id)
    assert client.get(f"{URL}/{tariff['tariff_id']}", headers=admin.headers).json() == tariff


def test_list_has_every_model_with_or_without_a_tariff(
    client: TestClient, admin: Caller, operator: Caller, model_id: uuid.UUID
) -> None:
    def row() -> Any:
        items = client.get(URL, headers=operator.headers).json()["items"]
        return next(item for item in items if item["model"]["id"] == str(model_id))

    assert row()["tariff_id"] is None
    assert row()["current"] is None

    tariff = _create(client, admin, model_id)

    assert row() == tariff


def test_a_change_adds_a_version_and_keeps_the_old_one(
    client: TestClient, admin: Caller, model_id: uuid.UUID
) -> None:
    tariff = _create(client, admin, model_id)

    response = _update(client, admin, tariff["tariff_id"], 1, driving_per_minute=45)

    assert response.status_code == HTTPStatus.OK, response.text
    changed = response.json()
    assert changed["current"]["number"] == 2
    assert changed["current"]["prices"]["driving_per_minute"] == 45
    assert changed["current"]["valid_from"] > tariff["current"]["valid_from"]
    versions = client.get(f"{URL}/{tariff['tariff_id']}/versions", headers=admin.headers).json()
    assert [version["number"] for version in versions["items"]] == [2, 1]
    assert versions["items"][1] == tariff["current"]


def test_a_change_from_a_stale_form_is_refused(
    client: TestClient, admin: Caller, model_id: uuid.UUID
) -> None:
    tariff = _create(client, admin, model_id)
    assert _update(client, admin, tariff["tariff_id"], 1, driving_per_minute=45).is_success

    response = _update(client, admin, tariff["tariff_id"], 1, driving_per_minute=50)

    assert response.status_code == HTTPStatus.CONFLICT
    assert response.json()["type"] == "urn:flagman:problem:tariff-changed"


def test_every_change_is_in_the_audit_log(
    client: TestClient, admin: Caller, model_id: uuid.UUID, integration_settings: Settings
) -> None:
    tariff = _create(client, admin, model_id)
    _update(client, admin, tariff["tariff_id"], 1, hold_amount=5000)

    assert _audit_events(integration_settings, tariff["tariff_id"]) == [
        ("tariff.created", admin.id, {"version": 1, **PRICES}),
        ("tariff.updated", admin.id, {"version": 2, **PRICES, "hold_amount": 5000}),
    ]


def test_a_model_gets_one_tariff(client: TestClient, admin: Caller, model_id: uuid.UUID) -> None:
    _create(client, admin, model_id)

    response = client.post(URL, json={"model_id": str(model_id), **PRICES}, headers=admin.headers)

    assert response.status_code == HTTPStatus.CONFLICT
    assert response.json()["type"] == "urn:flagman:problem:tariff-exists"


def test_unknown_model_and_tariff(client: TestClient, admin: Caller) -> None:
    created = client.post(
        URL, json={"model_id": str(uuid.uuid4()), **PRICES}, headers=admin.headers
    )
    updated = _update(client, admin, str(uuid.uuid4()), 1)
    versions = client.get(f"{URL}/{uuid.uuid4()}/versions", headers=admin.headers)

    assert created.json()["type"] == "urn:flagman:problem:car-model-not-found"
    assert updated.json()["type"] == "urn:flagman:problem:tariff-not-found"
    assert versions.json()["type"] == "urn:flagman:problem:tariff-not-found"


@pytest.mark.parametrize(
    "prices",
    [
        {"driving_per_minute": -1},
        {"driving_per_minute": 100_001},
        {"hold_amount": 1.5},
        {"hold_amount": "3000"},
        {"free_waiting_minutes": 601},
    ],
)
def test_prices_out_of_range_are_refused(
    client: TestClient, admin: Caller, model_id: uuid.UUID, prices: dict[str, Any]
) -> None:
    response = client.post(
        URL, json={"model_id": str(model_id), **PRICES, **prices}, headers=admin.headers
    )

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert response.json()["type"] == "urn:flagman:problem:validation-error"


def test_unknown_fields_are_refused(client: TestClient, admin: Caller, model_id: uuid.UUID) -> None:
    body = {"model_id": str(model_id), **PRICES, "total": 1}

    assert client.post(URL, json=body, headers=admin.headers).status_code == 422


def test_operator_reads_but_does_not_change(
    client: TestClient, admin: Caller, operator: Caller, model_id: uuid.UUID
) -> None:
    tariff = _create(client, admin, model_id)

    reads = [
        client.get(URL, headers=operator.headers),
        client.get(f"{URL}/{tariff['tariff_id']}", headers=operator.headers),
        client.get(f"{URL}/{tariff['tariff_id']}/versions", headers=operator.headers),
    ]
    created = client.post(URL, json={"model_id": str(model_id), **PRICES}, headers=operator.headers)
    updated = _update(client, operator, tariff["tariff_id"], 1)

    assert [response.status_code for response in reads] == [HTTPStatus.OK] * 3
    assert created.status_code == HTTPStatus.FORBIDDEN
    assert updated.status_code == HTTPStatus.FORBIDDEN


@pytest.mark.parametrize("role", [StaffRole.TECHNICIAN, None])
def test_technicians_and_clients_see_nothing(
    client: TestClient, integration_settings: Settings, role: StaffRole | None
) -> None:
    caller = Caller(integration_settings, role)

    response = client.get(URL, headers=caller.headers)

    assert response.status_code in {HTTPStatus.FORBIDDEN, HTTPStatus.UNAUTHORIZED}


def test_sign_in_is_required(client: TestClient) -> None:
    assert client.get(URL).status_code == HTTPStatus.UNAUTHORIZED


def test_rentals_quote_by_the_version_they_were_booked_with(
    client: TestClient, admin: Caller, model_id: uuid.UUID, integration_settings: Settings
) -> None:
    _create(client, admin, model_id, driving_per_minute=40)

    async def booked(db: AsyncSession) -> uuid.UUID:
        return (await service.current_version(db, model_id)).id

    version_id = _run(integration_settings, booked)
    items = client.get(URL, headers=admin.headers).json()["items"]
    tariff = next(item for item in items if item["model"]["id"] == str(model_id))
    _update(client, admin, tariff["tariff_id"], 1, driving_per_minute=90)
    start = datetime.now(UTC)
    trip = [Interval(IntervalKind.DRIVING, start, start + timedelta(minutes=10))]

    async def quote(db: AsyncSession) -> tuple[int, int]:
        booked_quote = await service.quote(db, version_id, trip)
        current = await service.current_version(db, model_id)
        return booked_quote.total, current.prices.driving_per_minute

    assert _run(integration_settings, quote) == (400, 90)


def test_public_reads_raise_for_missing_tariffs(integration_settings: Settings) -> None:
    with pytest.raises(TariffNotFoundError):
        _run(integration_settings, lambda db: service.current_version(db, uuid.uuid4()))
    with pytest.raises(TariffVersionNotFoundError):
        _run(integration_settings, lambda db: service.get_version(db, uuid.uuid4()))


def test_current_versions_leave_out_models_without_a_tariff(
    client: TestClient, admin: Caller, model_id: uuid.UUID, integration_settings: Settings
) -> None:
    _create(client, admin, model_id)
    other = uuid.uuid4()

    versions = _run(
        integration_settings, lambda db: service.current_versions(db, [model_id, other])
    )

    assert set(versions) == {model_id}


def test_seed_gives_models_without_a_tariff_the_demo_one(
    client: TestClient, admin: Caller, model_id: uuid.UUID, integration_settings: Settings
) -> None:
    def seed() -> int:
        return _run(
            integration_settings, lambda db: service.seed_demo_tariffs(db, datetime.now(UTC))
        )

    assert seed() >= 1
    assert seed() == 0

    current = _run(integration_settings, lambda db: service.current_version(db, model_id))
    assert current.prices == DEMO_PRICES
    assert current.created_by is None
