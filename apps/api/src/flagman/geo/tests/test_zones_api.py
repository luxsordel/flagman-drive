import asyncio
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from http import HTTPStatus
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from flagman.audit.models import AuditEvent
from flagman.core.app import create_app
from flagman.core.config import Settings
from flagman.core.db import create_engine, create_session_factory
from flagman.core.ratelimit import Limit
from flagman.geo import api
from flagman.identity.domain import (
    AccessClaims,
    StaffAccessClaims,
    StaffRole,
    issue_access_token,
    issue_staff_access_token,
)

pytestmark = pytest.mark.integration

URL = "/admin/zones"
# [lon, lat]: a square of about 1.3 x 2.2 km in the center of Minsk.
SQUARE = [[27.55, 53.89], [27.57, 53.89], [27.57, 53.91], [27.55, 53.91], [27.55, 53.89]]
OTHER = [[27.60, 53.92], [27.61, 53.92], [27.61, 53.93], [27.60, 53.92]]


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


def _body(ring: list[list[float]] = SQUARE, **overrides: Any) -> dict[str, Any]:
    return {
        "name": "Center",
        "kind": "allowed",
        "polygon": {"type": "Polygon", "coordinates": [ring]},
        **overrides,
    }


def _create(client: TestClient, admin: Caller, **overrides: Any) -> dict[str, Any]:
    response = client.post(URL, json=_body(**overrides), headers=admin.headers)
    assert response.status_code == HTTPStatus.CREATED, response.text
    zone: dict[str, Any] = response.json()
    return zone


def _audit_events(settings: Settings, zone_id: str) -> list[tuple[str, uuid.UUID | None, Any]]:
    async def read() -> list[tuple[str, uuid.UUID | None, Any]]:
        engine = create_engine(settings)
        try:
            async with create_session_factory(engine)() as db, db.begin():
                rows = await db.execute(
                    select(AuditEvent.action, AuditEvent.actor_id, AuditEvent.details)
                    .where(AuditEvent.target_id == uuid.UUID(zone_id))
                    .order_by(AuditEvent.occurred_at, AuditEvent.id)
                )
                return [(action, actor, details) for action, actor, details in rows]
        finally:
            await engine.dispose()

    return asyncio.run(read())


def test_admin_draws_a_zone(client: TestClient, admin: Caller) -> None:
    zone = _create(client, admin, name="  Center  ")

    assert zone["name"] == "Center"
    assert zone["kind"] == "allowed"
    assert zone["polygon"] == {"type": "Polygon", "coordinates": [SQUARE]}
    assert zone["disabled_at"] is None
    assert client.get(f"{URL}/{zone['id']}", headers=admin.headers).json() == zone


def test_every_change_is_in_the_audit_log(
    client: TestClient, admin: Caller, integration_settings: Settings
) -> None:
    zone = _create(client, admin)
    updated = client.put(
        f"{URL}/{zone['id']}", json=_body(OTHER, kind="forbidden"), headers=admin.headers
    )
    disabled = client.post(f"{URL}/{zone['id']}/disable", headers=admin.headers)

    assert updated.status_code == HTTPStatus.OK
    assert disabled.status_code == HTTPStatus.OK
    assert _audit_events(integration_settings, zone["id"]) == [
        ("zone.created", admin.id, {"kind": "allowed", "vertex_count": 4}),
        ("zone.updated", admin.id, {"kind": "forbidden", "vertex_count": 3}),
        ("zone.disabled", admin.id, {"kind": "forbidden", "vertex_count": 3}),
    ]


def test_update_replaces_the_zone(client: TestClient, admin: Caller) -> None:
    zone = _create(client, admin)

    response = client.put(
        f"{URL}/{zone['id']}",
        json=_body(OTHER, name="North", kind="forbidden"),
        headers=admin.headers,
    )

    assert response.status_code == HTTPStatus.OK
    updated = response.json()
    assert (updated["name"], updated["kind"]) == ("North", "forbidden")
    assert updated["polygon"]["coordinates"] == [OTHER]
    assert updated["created_at"] == zone["created_at"]
    assert updated["updated_at"] > zone["updated_at"]


def test_disabled_zone_leaves_the_list_and_cannot_change(
    client: TestClient, admin: Caller, integration_settings: Settings
) -> None:
    zone = _create(client, admin)
    disabled = client.post(f"{URL}/{zone['id']}/disable", headers=admin.headers).json()

    again = client.post(f"{URL}/{zone['id']}/disable", headers=admin.headers)
    update = client.put(f"{URL}/{zone['id']}", json=_body(), headers=admin.headers)
    active = client.get(URL, headers=admin.headers).json()["items"]
    everything = client.get(URL, params={"include_disabled": True}, headers=admin.headers)

    assert disabled["disabled_at"] is not None
    assert again.json() == disabled
    assert update.status_code == HTTPStatus.CONFLICT
    assert update.json()["type"] == "urn:flagman:problem:zone-disabled"
    assert zone["id"] not in [item["id"] for item in active]
    assert zone["id"] in [item["id"] for item in everything.json()["items"]]
    # Disabling twice records one event.
    assert [event[0] for event in _audit_events(integration_settings, zone["id"])] == [
        "zone.created",
        "zone.disabled",
    ]


@pytest.mark.parametrize(
    ("ring", "problem"),
    [
        pytest.param(SQUARE[:-1], "zone-polygon-not-closed", id="open"),
        pytest.param(
            [[27.55, 53.89], [27.57, 53.91], [27.57, 53.89], [27.55, 53.91], [27.55, 53.89]],
            "zone-polygon-self-intersecting",
            id="bow-tie",
        ),
        pytest.param(
            [[53.89, 27.55], [53.89, 27.57], [53.91, 27.57], [53.89, 27.55]],
            "zone-polygon-outside-area",
            id="swapped-lat-lon",
        ),
        pytest.param(SQUARE[:2] + SQUARE[:1], "zone-polygon-too-few-vertices", id="line"),
    ],
)
def test_broken_polygon_is_refused_with_its_reason(
    client: TestClient, admin: Caller, ring: list[list[float]], problem: str
) -> None:
    response = client.post(URL, json=_body(ring), headers=admin.headers)

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert response.json()["type"] == f"urn:flagman:problem:{problem}"


def test_too_many_vertices_are_refused_with_their_reason(client: TestClient, admin: Caller) -> None:
    ring = [[27.55 + step * 0.00004, 53.89 + (step % 2) * 0.0001] for step in range(501)]
    ring += [[27.57, 53.85], ring[0]]

    response = client.post(URL, json=_body(ring), headers=admin.headers)

    assert response.json()["type"] == "urn:flagman:problem:zone-polygon-too-many-vertices"


def test_zone_with_holes_is_refused(client: TestClient, admin: Caller) -> None:
    body = _body()
    body["polygon"]["coordinates"].append(OTHER)

    response = client.post(URL, json=body, headers=admin.headers)

    assert response.json()["type"] == "urn:flagman:problem:zone-polygon-holes"


@pytest.mark.parametrize(
    "change",
    [
        pytest.param({"name": "   "}, id="blank-name"),
        pytest.param({"name": "x" * 101}, id="long-name"),
        pytest.param({"kind": "parking"}, id="unknown-kind"),
        pytest.param({"polygon": {"type": "Point", "coordinates": [27.5, 53.9]}}, id="not-polygon"),
        pytest.param({"color": "red"}, id="extra-field"),
    ],
)
def test_malformed_body_is_a_validation_error(
    client: TestClient, admin: Caller, change: dict[str, Any]
) -> None:
    response = client.post(URL, json=_body(**change), headers=admin.headers)

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert response.json()["type"] == "urn:flagman:problem:validation-error"


def test_unknown_zone_is_not_found(client: TestClient, admin: Caller) -> None:
    missing = f"{URL}/{uuid.uuid4()}"

    for response in (
        client.get(missing, headers=admin.headers),
        client.put(missing, json=_body(), headers=admin.headers),
        client.post(f"{missing}/disable", headers=admin.headers),
    ):
        assert response.status_code == HTTPStatus.NOT_FOUND
        assert response.json()["type"] == "urn:flagman:problem:zone-not-found"


@pytest.mark.parametrize("role", [StaffRole.OPERATOR, StaffRole.TECHNICIAN])
def test_other_staff_read_zones_but_do_not_change_them(
    client: TestClient, admin: Caller, integration_settings: Settings, role: StaffRole
) -> None:
    zone = _create(client, admin)
    staff = Caller(integration_settings, role)

    listed = client.get(URL, headers=staff.headers)
    one = client.get(f"{URL}/{zone['id']}", headers=staff.headers)
    changes = [
        client.post(URL, json=_body(), headers=staff.headers),
        client.put(f"{URL}/{zone['id']}", json=_body(OTHER), headers=staff.headers),
        client.post(f"{URL}/{zone['id']}/disable", headers=staff.headers),
    ]

    assert zone["id"] in [item["id"] for item in listed.json()["items"]]
    assert one.json() == zone
    for response in changes:
        assert response.status_code == HTTPStatus.FORBIDDEN
        assert response.json()["type"] == "urn:flagman:problem:forbidden"
    assert client.get(f"{URL}/{zone['id']}", headers=admin.headers).json() == zone


def test_clients_and_strangers_get_nothing(
    client: TestClient, admin: Caller, integration_settings: Settings
) -> None:
    zone = _create(client, admin)
    rider = Caller(integration_settings, None)

    for headers in (rider.headers, {}):
        for response in (
            client.get(URL, headers=headers),
            client.get(f"{URL}/{zone['id']}", headers=headers),
            client.post(URL, json=_body(), headers=headers),
            client.put(f"{URL}/{zone['id']}", json=_body(), headers=headers),
            client.post(f"{URL}/{zone['id']}/disable", headers=headers),
        ):
            assert response.status_code == HTTPStatus.UNAUTHORIZED
            assert response.json()["type"] == "urn:flagman:problem:access-token-invalid"


MAP_URL = "/map/zones"


def test_clients_see_active_zones_without_staff_fields(
    client: TestClient, admin: Caller, integration_settings: Settings
) -> None:
    allowed = _create(client, admin)
    forbidden = _create(client, admin, ring=OTHER, kind="forbidden")
    disabled = _create(client, admin)
    client.post(f"{URL}/{disabled['id']}/disable", headers=admin.headers)
    rider = Caller(integration_settings, None)

    response = client.get(MAP_URL, headers=rider.headers)

    assert response.status_code == HTTPStatus.OK
    zones = {zone["id"]: zone for zone in response.json()["items"]}
    assert zones[allowed["id"]] == {
        "id": allowed["id"],
        "kind": "allowed",
        "polygon": {"type": "Polygon", "coordinates": [SQUARE]},
    }
    assert zones[forbidden["id"]]["kind"] == "forbidden"
    assert disabled["id"] not in zones


def test_map_zones_are_only_for_signed_in_clients(client: TestClient, admin: Caller) -> None:
    assert client.get(MAP_URL).status_code == HTTPStatus.UNAUTHORIZED
    assert client.get(MAP_URL, headers=admin.headers).status_code == HTTPStatus.UNAUTHORIZED


def test_map_zones_are_rate_limited(
    client: TestClient, integration_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(api, "MAP_LIMIT", Limit(f"map-zones-test-{uuid.uuid4()}", 1, 300))
    rider = Caller(integration_settings, None)

    assert client.get(MAP_URL, headers=rider.headers).status_code == HTTPStatus.OK
    limited = client.get(MAP_URL, headers=rider.headers)

    assert limited.status_code == HTTPStatus.TOO_MANY_REQUESTS
