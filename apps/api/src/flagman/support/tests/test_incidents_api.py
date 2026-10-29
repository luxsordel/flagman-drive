import asyncio
import uuid
from http import HTTPStatus
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from flagman.core.config import Settings
from flagman.core.db import create_engine
from flagman.fleet.tests.conftest import Staff, add_car
from flagman.identity.domain import StaffRole
from flagman.rentals.tests.conftest import (
    CAR_POINT,
    FakeStorage,
    Person,
    car_status,
    execute,
    photo,
    problem_type,
)
from flagman.support.domain import MAX_INCIDENT_PHOTOS, MAX_OPEN_INCIDENTS_PER_CLIENT
from flagman.support.service import INCIDENT_LIMIT
from flagman.support.tests.conftest import audit_actions, exhaust

pytestmark = pytest.mark.integration

_LOCATION = {"lat": CAR_POINT.lat, "lon": CAR_POINT.lon}


def _report(person: Person, type_: str = "damage", **extra: Any) -> Any:
    body = {"type": type_, "description": "Scratch on the rear door", **extra}
    return person.client.post("/support/incidents", json=body, headers=person.headers)


def _staff_post(client: TestClient, staff: Staff, incident: str, action: str, **body: Any) -> Any:
    return client.post(
        f"/admin/support/incidents/{incident}/{action}",
        json=body or None,
        headers=staff.headers,
    )


def _walk(client: TestClient, url: str, headers: dict[str, str]) -> list[dict[str, Any]]:
    """Every page by its cursor: the list does not depend on what other tests left behind."""
    items: list[dict[str, Any]] = []
    cursor: str | None = None
    while True:
        separator = "&" if "?" in url else "?"
        page_url = f"{url}{separator}limit=100" + (f"&cursor={cursor}" if cursor else "")
        page = client.get(page_url, headers=headers).json()
        items.extend(page["items"])
        cursor = page["next_cursor"]
        if cursor is None:
            return items


def _ids(items: list[dict[str, Any]]) -> list[str]:
    return [str(item["id"]) for item in items]


@pytest.fixture
def rented_car(integration_settings: Settings, person: Person) -> tuple[str, str]:
    car = add_car(integration_settings, CAR_POINT)
    rental = person.reserve(car).json()["id"]
    return car, rental


def test_the_active_rental_is_attached_by_the_server(
    person: Person, rented_car: tuple[str, str]
) -> None:
    car, rental = rented_car

    response = _report(person, location=_LOCATION)

    assert response.status_code == HTTPStatus.CREATED
    incident = response.json()
    assert (incident["rental_id"], incident["car_id"]) == (rental, car)
    assert incident["status"] == "new"
    assert incident["location"] == _LOCATION


def test_an_accident_during_a_trip_comes_first_and_does_not_end_it(
    integration_settings: Settings,
    app_client: TestClient,
    operator: Staff,
    rented_car: tuple[str, str],
    person: Person,
) -> None:
    rental = rented_car[1]
    person.inspect(rental)
    assert person.command(rental, "open").json()["status"] == "in_trip"
    calm = Person(integration_settings, app_client)
    earlier = _report(calm, "other").json()["id"]

    accident = _report(person, "accident").json()

    assert accident["rental_id"] == rental
    assert person.get(rental).json()["status"] == "in_trip"
    queue = _walk(app_client, "/admin/support/incidents?status=new", operator.headers)
    ids = _ids(queue)
    assert ids.index(accident["id"]) < ids.index(earlier)
    assert queue[ids.index(accident["id"])]["priority"] == 0


def test_an_accident_before_the_trip_is_not_urgent(
    person: Person, operator: Staff, app_client: TestClient, rented_car: tuple[str, str]
) -> None:
    accident = _report(person, "accident").json()

    staff_view = app_client.get(
        f"/admin/support/incidents/{accident['id']}", headers=operator.headers
    ).json()

    assert accident["rental_id"] == rented_car[1]
    assert staff_view["priority"] == 1


def test_an_expired_reservation_is_not_attached(
    integration_settings: Settings, person: Person, rented_car: tuple[str, str]
) -> None:
    car, rental = rented_car
    execute(
        integration_settings,
        "UPDATE rentals SET reserved_at = now() - interval '46 minutes', "
        "free_wait_until = now() - interval '31 minutes', expires_at = now() - interval '1 second' "
        "WHERE id = :id",
        id=uuid.UUID(rental),
    )

    incident = _report(person, "accident").json()

    assert (incident["rental_id"], incident["car_id"]) == (None, None)
    assert person.get(rental).json()["cancel_reason"] == "expired"
    assert car_status(integration_settings, car) == "available"


def test_without_a_rental_nothing_is_attached(person: Person) -> None:
    incident = _report(person, "accident").json()

    assert (incident["rental_id"], incident["car_id"]) == (None, None)


def test_a_client_sees_only_their_own_incidents(
    integration_settings: Settings, person: Person, app_client: TestClient
) -> None:
    incident = _report(person).json()["id"]
    added = person.client.post(
        f"/support/incidents/{incident}/photos",
        content=photo(),
        headers={**person.headers, "Content-Type": "image/jpeg"},
    ).json()
    photo_id = added["photos"][0]["id"]
    other = Person(integration_settings, app_client)

    stolen = other.client.get(f"/support/incidents/{incident}", headers=other.headers)
    upload = other.client.post(
        f"/support/incidents/{incident}/photos",
        content=photo(1),
        headers={**other.headers, "Content-Type": "image/jpeg"},
    )
    link = other.client.get(
        f"/support/incidents/{incident}/photos/{photo_id}/url", headers=other.headers
    )

    assert stolen.status_code == HTTPStatus.NOT_FOUND
    assert problem_type(stolen) == "incident-not-found"
    assert upload.status_code == HTTPStatus.NOT_FOUND
    assert link.status_code == HTTPStatus.NOT_FOUND
    assert problem_type(link) == "incident-not-found"
    assert other.client.get("/support/incidents", headers=other.headers).json() == {
        "items": [],
        "next_cursor": None,
    }


def test_photos_are_limited(person: Person, storage: FakeStorage) -> None:
    incident = _report(person).json()["id"]
    url = f"/support/incidents/{incident}/photos"
    headers = {**person.headers, "Content-Type": "image/jpeg"}
    for index in range(MAX_INCIDENT_PHOTOS):
        assert person.client.post(url, content=photo(index), headers=headers).status_code == 201

    response = person.client.post(url, content=photo(99), headers=headers)

    assert response.status_code == HTTPStatus.CONFLICT
    assert problem_type(response) == "incident-photo-limit"
    assert len(storage.objects) == MAX_INCIDENT_PHOTOS
    assert all(key.startswith(f"incidents/{incident}/") for key in storage.objects)


def test_open_incidents_per_client_are_limited(person: Person) -> None:
    for _ in range(MAX_OPEN_INCIDENTS_PER_CLIENT):
        assert _report(person).status_code == HTTPStatus.CREATED

    response = _report(person)

    assert response.status_code == HTTPStatus.CONFLICT
    assert problem_type(response) == "too-many-open-incidents"


async def _wait_for_a_lock_waiter(watcher: AsyncConnection) -> None:
    for _ in range(200):
        waiting = await watcher.scalar(
            text(
                "SELECT count(*) FROM pg_stat_activity "
                "WHERE datname = current_database() AND wait_event_type = 'Lock'"
            )
        )
        await watcher.rollback()
        if waiting:
            return
        await asyncio.sleep(0.05)
    raise AssertionError("the report never waited for the client's other reports")


def test_parallel_reports_do_not_pass_the_open_incident_limit(
    integration_settings: Settings, person: Person
) -> None:
    async def race() -> Any:
        engine = create_engine(integration_settings)
        try:
            async with engine.connect() as other, engine.connect() as watcher:
                # Other reports of the same client, stopped just before their commit.
                await other.execute(
                    text("SELECT pg_advisory_xact_lock(4600201, :key)"),
                    {"key": (person.id.int >> 96) - 2**31},
                )
                for _ in range(MAX_OPEN_INCIDENTS_PER_CLIENT):
                    await other.execute(
                        text(
                            "INSERT INTO support_incidents (id, user_id, type, status, priority, "
                            "description, sent_to_service, created_at, updated_at) VALUES "
                            "(:id, :user, 'other', 'new', 1, 'Parallel', false, now(), now())"
                        ),
                        {"id": uuid.uuid4(), "user": person.id},
                    )
                reporting = asyncio.create_task(asyncio.to_thread(_report, person))
                await _wait_for_a_lock_waiter(watcher)
                await other.commit()
                return await reporting
        finally:
            await engine.dispose()

    response = asyncio.run(race())

    assert response.status_code == HTTPStatus.CONFLICT
    assert problem_type(response) == "too-many-open-incidents"


def test_the_staff_list_keeps_photos_and_events_with_their_incident(
    person: Person, operator: Staff, app_client: TestClient
) -> None:
    with_photo = _report(person).json()["id"]
    taken = _report(person, "malfunction").json()["id"]
    person.client.post(
        f"/support/incidents/{with_photo}/photos",
        content=photo(),
        headers={**person.headers, "Content-Type": "image/jpeg"},
    )
    _staff_post(app_client, operator, taken, "take")

    listed = _walk(
        app_client, "/admin/support/incidents?status=new&status=in_progress", operator.headers
    )
    by_id = {item["id"]: item for item in listed}

    assert len(by_id[with_photo]["photos"]) == 1
    assert by_id[with_photo]["events"] == []
    assert by_id[taken]["photos"] == []
    assert [event["action"] for event in by_id[taken]["events"]] == ["take"]


def test_incident_reports_are_rate_limited(person: Person) -> None:
    exhaust(person.settings, INCIDENT_LIMIT, str(person.id))

    response = _report(person)

    assert response.status_code == HTTPStatus.TOO_MANY_REQUESTS
    assert person.client.get("/support/incidents", headers=person.headers).json()["items"] == []


def _resolve(client: TestClient, staff: Staff, incident: str) -> None:
    _staff_post(client, staff, incident, "take")
    assert _staff_post(client, staff, incident, "resolve", comment="Done").status_code == 200


def test_closed_incidents_go_from_the_newest_page_by_page(
    person: Person, operator: Staff, app_client: TestClient
) -> None:
    older, newer = _report(person).json()["id"], _report(person).json()["id"]
    for incident in (older, newer):
        _resolve(app_client, operator, incident)
    url = "/admin/support/incidents?status=resolved&limit=1"

    first = app_client.get(url, headers=operator.headers).json()
    second = app_client.get(f"{url}&cursor={first['next_cursor']}", headers=operator.headers).json()

    # Nothing is resolved after these two, so they are the newest.
    assert _ids(first["items"]) == [newer]
    assert _ids(second["items"]) == [older]


def test_the_queue_goes_page_by_page_without_gaps(
    person: Person, operator: Staff, app_client: TestClient
) -> None:
    reported = [_report(person).json()["id"] for _ in range(3)]

    queue = _walk(app_client, "/admin/support/incidents?status=new&limit=1", operator.headers)

    ids = _ids(queue)
    assert len(ids) == len(set(ids))
    assert [ids.index(incident) for incident in reported] == sorted(
        ids.index(incident) for incident in reported
    )


def test_a_client_pages_through_their_incidents(person: Person) -> None:
    reported = [_report(person).json()["id"] for _ in range(3)]

    first = person.client.get("/support/incidents?limit=2", headers=person.headers).json()
    rest = person.client.get(
        f"/support/incidents?limit=2&cursor={first['next_cursor']}", headers=person.headers
    ).json()

    assert _ids(first["items"]) + _ids(rest["items"]) == reported[::-1]
    assert rest["next_cursor"] is None


@pytest.mark.parametrize("cursor", ["not-a-cursor", "MXxub3QgYSBkYXRlfHg"])
def test_a_broken_cursor_is_refused(
    person: Person, operator: Staff, app_client: TestClient, cursor: str
) -> None:
    mine = person.client.get(f"/support/incidents?cursor={cursor}", headers=person.headers)
    staff = app_client.get(f"/admin/support/incidents?cursor={cursor}", headers=operator.headers)

    assert mine.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert staff.status_code == HTTPStatus.UNPROCESSABLE_ENTITY


def test_an_incident_is_taken_and_resolved_with_a_comment(
    integration_settings: Settings, person: Person, operator: Staff, app_client: TestClient
) -> None:
    incident = _report(person).json()["id"]

    early = _staff_post(app_client, operator, incident, "resolve", comment="Fine")
    taken = _staff_post(app_client, operator, incident, "take")
    no_comment = _staff_post(app_client, operator, incident, "resolve", comment="  ")
    resolved = _staff_post(app_client, operator, incident, "resolve", comment="Polished it")

    assert early.status_code == HTTPStatus.CONFLICT
    assert problem_type(early) == "incident-transition-not-allowed"
    assert taken.json()["assigned_staff_id"] == str(operator.id)
    assert no_comment.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    body = resolved.json()
    assert body["status"] == "resolved"
    assert [(event["action"], event["comment"]) for event in body["events"]] == [
        ("take", None),
        ("resolve", "Polished it"),
    ]
    assert audit_actions(integration_settings, incident) == ["incident.taken", "incident.resolved"]
    client_view = person.client.get(f"/support/incidents/{incident}", headers=person.headers)
    assert client_view.json()["status"] == "resolved"
    assert "events" not in client_view.json()


def test_another_operator_cannot_decide_a_taken_incident(
    integration_settings: Settings, person: Person, operator: Staff, app_client: TestClient
) -> None:
    incident = _report(person).json()["id"]
    _staff_post(app_client, operator, incident, "take")
    colleague = Staff(integration_settings, StaffRole.OPERATOR)

    response = _staff_post(app_client, colleague, incident, "reject", comment="Not real")

    assert response.status_code == HTTPStatus.CONFLICT
    assert problem_type(response) == "incident-not-yours"


def test_a_car_held_by_a_rental_is_not_sent_to_service(
    integration_settings: Settings,
    person: Person,
    operator: Staff,
    app_client: TestClient,
    rented_car: tuple[str, str],
) -> None:
    incident = _report(person, "malfunction").json()["id"]
    _staff_post(app_client, operator, incident, "take")

    response = _staff_post(app_client, operator, incident, "send-to-service")

    assert response.status_code == HTTPStatus.CONFLICT
    assert problem_type(response) == "car-transition-not-allowed"
    assert car_status(integration_settings, rented_car[0]) == "reserved"


def test_a_free_car_is_sent_to_service_from_the_incident(
    integration_settings: Settings,
    person: Person,
    operator: Staff,
    app_client: TestClient,
    rented_car: tuple[str, str],
) -> None:
    car, rental = rented_car
    incident = _report(person, "malfunction").json()["id"]
    assert person.cancel(rental).status_code == HTTPStatus.OK
    _staff_post(app_client, operator, incident, "take")

    response = _staff_post(app_client, operator, incident, "send-to-service")
    again = _staff_post(app_client, operator, incident, "send-to-service")

    assert response.status_code == HTTPStatus.OK
    assert response.json()["sent_to_service"] is True
    assert car_status(integration_settings, car) == "service"
    assert again.status_code == HTTPStatus.CONFLICT
    assert audit_actions(integration_settings, incident) == [
        "incident.taken",
        "incident.car_sent_to_service",
    ]


def test_sending_to_service_needs_a_live_session(
    person: Person, operator: Staff, app_client: TestClient, rented_car: tuple[str, str]
) -> None:
    incident = _report(person, "malfunction").json()["id"]
    _staff_post(app_client, operator, incident, "take")
    operator.revoke_session()

    response = _staff_post(app_client, operator, incident, "send-to-service")

    assert response.status_code == HTTPStatus.UNAUTHORIZED


def test_the_menu_counts_unread_conversations_and_new_incidents(
    person: Person, operator: Staff, app_client: TestClient
) -> None:
    before = app_client.get("/admin/support/unread", headers=operator.headers).json()
    _report(person)
    person.client.post("/support/messages", json={"text": "Hi"}, headers=person.headers)

    after = app_client.get("/admin/support/unread", headers=operator.headers).json()

    assert after["incidents"] == before["incidents"] + 1
    assert after["conversations"] == before["conversations"] + 1


def test_staff_photo_links_are_audited(
    integration_settings: Settings, person: Person, operator: Staff, app_client: TestClient
) -> None:
    incident = _report(person).json()["id"]
    added = person.client.post(
        f"/support/incidents/{incident}/photos",
        content=photo(),
        headers={**person.headers, "Content-Type": "image/jpeg"},
    ).json()
    photo_id = added["photos"][0]["id"]

    response = app_client.get(
        f"/admin/support/incidents/{incident}/photos/{photo_id}/url", headers=operator.headers
    )

    assert response.status_code == HTTPStatus.OK
    assert response.headers["Cache-Control"] == "no-store"
    assert audit_actions(integration_settings, incident) == ["incident.photo_viewed"]
