import uuid
from datetime import UTC, datetime
from http import HTTPStatus
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from flagman.audit.models import AuditEvent
from flagman.core.config import Settings
from flagman.identity.domain import StaffRole
from flagman.pricing import service
from flagman.pricing.domain import LOW_BATTERY_FINE_CODE, FineKind, FineTypeVersionNotFoundError
from flagman.pricing.seed import DEMO_FINES
from flagman.pricing.tests.conftest import Caller, _run

pytestmark = pytest.mark.integration

URL = "/admin/fine-types"
TERMS = {"name_ru": "Животные в салоне", "name_en": "Pets in the car", "amount": 4000}


def _code() -> str:
    # Codes are unique in the whole test database, which the tests share.
    return f"pets_{uuid.uuid4().hex[:8]}"


def _create(client: TestClient, admin: Caller, **body: Any) -> Any:
    response = client.post(URL, json={"code": _code(), **TERMS, **body}, headers=admin.headers)
    assert response.status_code == HTTPStatus.CREATED, response.text
    return response.json()


def _update(
    client: TestClient, caller: Caller, fine_type_id: str, based_on: int, **body: Any
) -> Any:
    return client.put(
        f"{URL}/{fine_type_id}",
        json={"based_on_version": based_on, **TERMS, **body},
        headers=caller.headers,
    )


def _audit(settings: Settings, fine_type_id: str) -> list[tuple[str, Any]]:
    async def read(db: AsyncSession) -> list[tuple[str, Any]]:
        async with db.begin():
            rows = await db.execute(
                select(AuditEvent.action, AuditEvent.details)
                .where(AuditEvent.target_id == uuid.UUID(fine_type_id))
                .order_by(AuditEvent.occurred_at, AuditEvent.id)
            )
            return [(action, details) for action, details in rows]

    return _run(settings, read)


def test_admin_adds_a_manual_fine_type(client: TestClient, admin: Caller) -> None:
    created = _create(client, admin)

    assert created["kind"] == "manual"
    assert created["number"] == 1
    assert created["created_by"] == str(admin.id)
    assert created in client.get(URL, headers=admin.headers).json()["items"]


def test_a_taken_code_is_refused(client: TestClient, admin: Caller) -> None:
    created = _create(client, admin)

    again = client.post(URL, json={"code": created["code"], **TERMS}, headers=admin.headers)

    assert again.status_code == HTTPStatus.CONFLICT
    assert again.json()["type"] == "urn:flagman:problem:fine-type-exists"


def test_a_change_adds_a_version_and_keeps_the_old_one(
    client: TestClient, admin: Caller, integration_settings: Settings
) -> None:
    created = _create(client, admin)

    updated = _update(client, admin, created["fine_type_id"], 1, amount=6000)

    assert updated.status_code == HTTPStatus.OK
    assert (updated.json()["number"], updated.json()["amount"]) == (2, 6000)
    versions = client.get(
        f"{URL}/{created['fine_type_id']}/versions", headers=admin.headers
    ).json()["items"]
    assert [(item["number"], item["amount"]) for item in versions] == [(2, 6000), (1, 4000)]
    # A fine charged by the first version keeps its amount.
    first = _run(
        integration_settings, lambda db: service.get_fine_version(db, uuid.UUID(created["id"]))
    )
    assert first.terms.amount == 4000


def test_a_change_based_on_an_old_version_is_refused(client: TestClient, admin: Caller) -> None:
    created = _create(client, admin)
    assert _update(client, admin, created["fine_type_id"], 1).status_code == HTTPStatus.OK

    stale = _update(client, admin, created["fine_type_id"], 1, amount=1)

    assert stale.status_code == HTTPStatus.CONFLICT
    assert stale.json()["type"] == "urn:flagman:problem:fine-type-changed"


def test_an_unknown_type_is_not_found(client: TestClient, admin: Caller) -> None:
    response = _update(client, admin, str(uuid.uuid4()), 1)

    assert response.status_code == HTTPStatus.NOT_FOUND
    assert response.json()["type"] == "urn:flagman:problem:fine-type-not-found"


@pytest.mark.parametrize(
    "body",
    [{"amount": 0}, {"amount": "100"}, {"name_ru": ""}, {"code": "Bad Code"}, {"total": 1}],
)
def test_bad_bodies_are_refused(client: TestClient, admin: Caller, body: dict[str, Any]) -> None:
    response = client.post(URL, json={"code": _code(), **TERMS, **body}, headers=admin.headers)

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY


def test_every_change_is_in_the_audit_log(
    client: TestClient, admin: Caller, integration_settings: Settings
) -> None:
    created = _create(client, admin)
    _update(client, admin, created["fine_type_id"], 1, amount=6000)

    events = _audit(integration_settings, created["fine_type_id"])

    assert [action for action, _ in events] == ["fine_type.created", "fine_type.updated"]
    assert events[1][1] == {"code": created["code"], "version": 2, **TERMS, "amount": 6000}


def test_operator_reads_but_does_not_change(
    client: TestClient, admin: Caller, operator: Caller
) -> None:
    created = _create(client, admin)

    reads = [
        client.get(URL, headers=operator.headers),
        client.get(f"{URL}/{created['fine_type_id']}/versions", headers=operator.headers),
    ]
    added = client.post(URL, json={"code": _code(), **TERMS}, headers=operator.headers)
    changed = _update(client, operator, created["fine_type_id"], 1)

    assert [response.status_code for response in reads] == [HTTPStatus.OK] * 2
    assert added.status_code == HTTPStatus.FORBIDDEN
    assert changed.status_code == HTTPStatus.FORBIDDEN


@pytest.mark.parametrize("role", [StaffRole.TECHNICIAN, None])
def test_technicians_and_clients_see_nothing(
    client: TestClient, integration_settings: Settings, role: StaffRole | None
) -> None:
    response = client.get(URL, headers=Caller(integration_settings, role).headers)

    assert response.status_code in {HTTPStatus.FORBIDDEN, HTTPStatus.UNAUTHORIZED}


def test_seed_adds_the_demo_fines_once(integration_settings: Settings) -> None:
    def seed() -> int:
        return _run(integration_settings, lambda db: service.seed_demo_fines(db, datetime.now(UTC)))

    seed()
    assert seed() == 0

    low_battery = _run(
        integration_settings, lambda db: service.current_fine(db, LOW_BATTERY_FINE_CODE)
    )
    assert low_battery is not None
    assert low_battery.kind is FineKind.AUTOMATIC
    assert low_battery.terms == DEMO_FINES[LOW_BATTERY_FINE_CODE]


def test_a_missing_version_raises(integration_settings: Settings) -> None:
    with pytest.raises(FineTypeVersionNotFoundError):
        _run(integration_settings, lambda db: service.get_fine_version(db, uuid.uuid4()))
