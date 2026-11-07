import uuid
from http import HTTPStatus
from typing import Any

import pytest
from fastapi.testclient import TestClient

from flagman.core.config import Settings
from flagman.fleet.tests.conftest import Staff
from flagman.identity.domain import StaffRole
from flagman.kyc.domain import KycStatus
from flagman.rentals.tests.conftest import (
    CAR_POINT,
    Person,
    add_priced_car,
    execute,
    problem_type,
    query,
)

pytestmark = pytest.mark.integration


@pytest.fixture
def person(integration_settings: Settings, app_client: TestClient) -> Person:
    return Person(integration_settings, app_client)


@pytest.fixture
def operator(integration_settings: Settings) -> Staff:
    return Staff(integration_settings, StaffRole.OPERATOR)


def _name(settings: Settings, person: Person, first: str, last: str) -> None:
    execute(
        settings,
        "UPDATE users SET first_name = :first, last_name = :last WHERE id = :id",
        first=first,
        last=last,
        id=person.id,
    )


def _block(client: TestClient, staff: Staff, person: Person, reason: str = "Fraud") -> Any:
    return client.post(
        f"/admin/clients/{person.id}/block", json={"reason": reason}, headers=staff.headers
    )


def _unblock(client: TestClient, staff: Staff, person: Person, reason: str = "Sorted") -> Any:
    return client.post(
        f"/admin/clients/{person.id}/unblock", json={"reason": reason}, headers=staff.headers
    )


def test_staff_find_a_client_by_a_part_of_the_name(
    integration_settings: Settings, person: Person, operator: Staff, app_client: TestClient
) -> None:
    surname = f"Zz{uuid.uuid4().hex[:8]}"
    _name(integration_settings, person, "Анна", surname)
    Person(integration_settings, app_client)

    response = app_client.get(
        "/admin/clients", params={"search": surname.lower()}, headers=operator.headers
    )

    assert response.status_code == HTTPStatus.OK, response.text
    [found] = response.json()["items"]
    assert (found["id"], found["last_name"], found["blocked"]) == (str(person.id), surname, False)


def test_a_search_with_like_wildcards_matches_them_literally(
    operator: Staff, app_client: TestClient
) -> None:
    response = app_client.get("/admin/clients", params={"search": "%_"}, headers=operator.headers)

    assert response.status_code == HTTPStatus.OK
    assert response.json()["items"] == []


def test_clients_come_page_by_page(
    integration_settings: Settings, operator: Staff, app_client: TestClient
) -> None:
    surname = f"Zz{uuid.uuid4().hex[:8]}"
    people = [Person(integration_settings, app_client) for _ in range(3)]
    for person in people:
        _name(integration_settings, person, "Анна", surname)

    def page(cursor: str | None = None) -> Any:
        params = {"search": surname, "limit": "2", **({"cursor": cursor} if cursor else {})}
        return app_client.get("/admin/clients", params=params, headers=operator.headers).json()

    first = page()
    rest = page(first["next_cursor"])

    ids = [item["id"] for item in first["items"] + rest["items"]]
    assert sorted(ids) == sorted(str(person.id) for person in people)
    assert (len(first["items"]), rest["next_cursor"]) == (2, None)


def test_a_client_token_is_not_a_staff_token(person: Person, app_client: TestClient) -> None:
    for method, path in (("GET", ""), ("GET", f"/{person.id}"), ("POST", f"/{person.id}/block")):
        response = app_client.request(
            method, "/admin/clients" + path, json={"reason": "x"}, headers=person.headers
        )
        assert response.status_code == HTTPStatus.UNAUTHORIZED


def test_the_card_tells_kyc_and_the_debt(
    integration_settings: Settings, operator: Staff, app_client: TestClient
) -> None:
    pending = Person(integration_settings, app_client, kyc=KycStatus.PENDING)

    response = app_client.get(f"/admin/clients/{pending.id}", headers=operator.headers)

    assert response.status_code == HTTPStatus.OK, response.text
    body = response.json()
    assert (body["kyc_status"], body["debt"], body["block"]) == ("pending", 0, None)
    assert body["block_history"] == []


def test_an_unknown_client_is_not_found(operator: Staff, app_client: TestClient) -> None:
    response = app_client.get(f"/admin/clients/{uuid.uuid4()}", headers=operator.headers)

    assert response.status_code == HTTPStatus.NOT_FOUND
    assert problem_type(response) == "client-not-found"


def test_a_blocked_client_neither_reserves_nor_hides_it(
    integration_settings: Settings, person: Person, operator: Staff, app_client: TestClient
) -> None:
    surname = f"Zz{uuid.uuid4().hex[:8]}"
    _name(integration_settings, person, "Борис", surname)

    response = _block(app_client, operator, person, " Left  cars outside the zone ")

    assert response.status_code == HTTPStatus.OK, response.text
    block = response.json()["block"]
    assert (block["reason"], block["staff_id"]) == ("Left cars outside the zone", str(operator.id))
    car = add_priced_car(integration_settings, CAR_POINT)
    assert problem_type(person.reserve(car)) == "client-blocked"
    assert app_client.get("/me", headers=person.headers).json()["blocked"] is True

    def found(blocked: str) -> list[str]:
        params = {"blocked": blocked, "search": surname}
        page = app_client.get("/admin/clients", params=params, headers=operator.headers)
        return [item["id"] for item in page.json()["items"]]

    assert (found("true"), found("false")) == ([str(person.id)], [])


def test_an_unblock_lets_the_client_rent_again_and_both_stay_in_the_history(
    integration_settings: Settings, person: Person, operator: Staff, app_client: TestClient
) -> None:
    _block(app_client, operator, person, "Fraud check")

    response = _unblock(app_client, operator, person, "Documents confirmed")

    assert response.status_code == HTTPStatus.OK, response.text
    body = response.json()
    assert body["block"] is None
    assert [(e["blocked"], e["reason"]) for e in body["block_history"]] == [
        (False, "Documents confirmed"),
        (True, "Fraud check"),
    ]
    car = add_priced_car(integration_settings, CAR_POINT)
    assert person.reserve(car).status_code == HTTPStatus.CREATED
    events = query(
        integration_settings,
        "SELECT action, actor_id, details FROM audit_events WHERE target_id = :id "
        "ORDER BY occurred_at, id",
        id=person.id,
    )
    assert [(e.action, e.actor_id, e.details) for e in events] == [
        ("client.blocked", operator.id, {}),
        ("client.unblocked", operator.id, {}),
    ]


def test_a_repeated_block_changes_nothing(
    integration_settings: Settings, person: Person, operator: Staff, app_client: TestClient
) -> None:
    _block(app_client, operator, person, "First")

    again = _block(app_client, operator, person, "Second")

    assert again.json()["block"]["reason"] == "First"
    assert len(again.json()["block_history"]) == 1


def test_a_block_reason_follows_the_rule_of_every_staff_note(
    person: Person, operator: Staff, app_client: TestClient
) -> None:
    # A private-use character, refused in a rental's comment as well.
    response = _block(app_client, operator, person, "Fraud" + chr(0xE000))

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY


def test_a_block_needs_a_reason(person: Person, operator: Staff, app_client: TestClient) -> None:
    response = _block(app_client, operator, person, "  ")

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY


@pytest.mark.parametrize(
    ("method", "path"),
    [("GET", ""), ("GET", "/{id}"), ("POST", "/{id}/block"), ("POST", "/{id}/unblock")],
)
def test_a_technician_has_no_business_in_clients(
    integration_settings: Settings, app_client: TestClient, method: str, path: str
) -> None:
    technician = Staff(integration_settings, StaffRole.TECHNICIAN)

    response = app_client.request(
        method,
        "/admin/clients" + path.format(id=uuid.uuid4()),
        json={"reason": "x"},
        headers=technician.headers,
    )

    assert response.status_code == HTTPStatus.FORBIDDEN


def test_a_block_needs_a_live_session(
    person: Person, operator: Staff, app_client: TestClient
) -> None:
    operator.revoke_session()

    assert _block(app_client, operator, person).status_code == HTTPStatus.UNAUTHORIZED


def test_an_unblock_needs_a_live_session(
    person: Person, operator: Staff, app_client: TestClient
) -> None:
    assert _block(app_client, operator, person).status_code == HTTPStatus.OK
    operator.revoke_session()

    assert _unblock(app_client, operator, person).status_code == HTTPStatus.UNAUTHORIZED
    assert app_client.get("/me", headers=person.headers).json()["blocked"] is True
