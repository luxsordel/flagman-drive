import uuid
from datetime import UTC, datetime, timedelta
from http import HTTPStatus
from typing import Any

import pytest
from fastapi.testclient import TestClient

from flagman.core.config import Settings
from flagman.fleet.domain import TelematicsTimeoutError
from flagman.fleet.tests.conftest import FakeGateway, Staff, run_in_db
from flagman.geo import service as geo
from flagman.identity.domain import StaffRole
from flagman.payments.fake_provider import FakeProvider
from flagman.payments.ports import OperationRequest, OperationResult, PaymentProviderError
from flagman.rentals.tests.conftest import (
    CAR_POINT,
    DECLINED_CARD,
    FakeStorage,
    Person,
    add_priced_car,
    car_status,
    execute,
    photo,
    problem_type,
    query,
)

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def demo_zone(integration_settings: Settings) -> None:
    run_in_db(integration_settings, lambda db: geo.seed_demo_zone(db, datetime.now(UTC)))


@pytest.fixture
def person(integration_settings: Settings, app_client: TestClient) -> Person:
    return Person(integration_settings, app_client)


@pytest.fixture
def car(integration_settings: Settings) -> str:
    return add_priced_car(integration_settings, CAR_POINT)


@pytest.fixture
def operator(integration_settings: Settings) -> Staff:
    return Staff(integration_settings, StaffRole.OPERATOR)


def _start(person: Person, car: str) -> str:
    response = person.reserve(car)
    assert response.status_code == HTTPStatus.CREATED, response.text
    rental_id = str(response.json()["id"])
    person.inspect(rental_id)
    opened = person.command(rental_id, "open")
    assert opened.status_code == HTTPStatus.OK, opened.text
    return rental_id


def _finish(person: Person, rental_id: str) -> dict[str, Any]:
    for index in range(4):
        assert person.upload(rental_id, photo(index), stage="after").status_code == HTTPStatus.OK
    assert person.submit(rental_id, stage="after").status_code == HTTPStatus.OK
    response = person.client.post(f"/rentals/{rental_id}/finish", headers=person.headers)
    assert response.status_code == HTTPStatus.OK, response.text
    body: dict[str, Any] = response.json()
    return body


def _paid_trip(settings: Settings, person: Person, car: str, minutes: int = 30) -> str:
    """A finished and paid trip that cost something: `minutes` of driving."""
    rental_id = _start(person, car)
    _shift(settings, rental_id, minutes)
    assert _finish(person, rental_id)["status"] == "paid"
    return rental_id


def _shift(settings: Settings, rental_id: str, minutes: int) -> None:
    params = {"id": uuid.UUID(rental_id), "minutes": minutes}
    execute(
        settings,
        "UPDATE rentals SET reserved_at = reserved_at - make_interval(mins => :minutes), "
        "free_wait_until = free_wait_until - make_interval(mins => :minutes), "
        "expires_at = expires_at - make_interval(mins => :minutes), "
        "trip_started_at = trip_started_at - make_interval(mins => :minutes) WHERE id = :id",
        **params,
    )
    execute(
        settings,
        "UPDATE rental_intervals SET started_at = started_at - make_interval(mins => :minutes), "
        "ended_at = ended_at - make_interval(mins => :minutes) WHERE rental_id = :id",
        **params,
    )


def _operations(settings: Settings, rental_id: str) -> list[Any]:
    return query(
        settings,
        "SELECT idempotency_key, kind, amount, status FROM payment_operations "
        "WHERE idempotency_key LIKE :prefix ORDER BY created_at, id",
        prefix=f"rental:{rental_id}:%",
    )


def _audit(settings: Settings, target_id: str) -> list[Any]:
    return query(
        settings,
        "SELECT action, actor_id, details FROM audit_events WHERE target_id = :id "
        "ORDER BY occurred_at, id",
        id=uuid.UUID(target_id),
    )


def _make_default(person: Person, last4: str) -> None:
    cards = person.client.get("/payment-cards", headers=person.headers).json()["items"]
    [card] = [card for card in cards if card["last4"] == last4]
    response = person.client.post(f"/payment-cards/{card['id']}/default", headers=person.headers)
    assert response.status_code == HTTPStatus.OK, response.text


def _card(client: TestClient, staff: Staff, rental_id: str) -> Any:
    return client.get(f"/admin/rentals/{rental_id}", headers=staff.headers)


def _adjust(  # noqa: PLR0913, PLR0917 - the request and who sends it
    client: TestClient,
    staff: Staff,
    rental_id: str,
    amount: int,
    comment: str,
    key: uuid.UUID | None = None,
) -> Any:
    return client.post(
        f"/admin/rentals/{rental_id}/adjustments",
        json={"amount": amount, "comment": comment},
        headers={**staff.headers, "Idempotency-Key": str(key or uuid.uuid4())},
    )


def _audited(settings: Settings, rental_id: str, action: str) -> list[Any]:
    return [e for e in _audit(settings, rental_id) if e.action == action]


def _fail_charges_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """The provider charges, but its answer to the next charge is lost."""
    original = FakeProvider.charge
    failures = [1]

    async def charge(self: FakeProvider, request: OperationRequest) -> OperationResult:
        result = await original(self, request)
        if failures[0]:
            failures[0] -= 1
            raise PaymentProviderError("timeout")
        return result

    monkeypatch.setattr(FakeProvider, "charge", charge)


def _force(client: TestClient, staff: Staff, rental_id: str, reason: str = "Stuck") -> Any:
    return client.post(
        f"/admin/rentals/{rental_id}/force-finish", json={"reason": reason}, headers=staff.headers
    )


# The client's history


def test_the_client_sees_their_rentals_newest_first_page_by_page(
    integration_settings: Settings, person: Person, app_client: TestClient
) -> None:
    first = _paid_trip(
        integration_settings, person, add_priced_car(integration_settings, CAR_POINT)
    )
    second = _paid_trip(
        integration_settings, person, add_priced_car(integration_settings, CAR_POINT), minutes=10
    )
    stranger = Person(integration_settings, app_client)
    _paid_trip(integration_settings, stranger, add_priced_car(integration_settings, CAR_POINT))

    page = app_client.get("/rentals/history?limit=1", headers=person.headers)
    assert page.status_code == HTTPStatus.OK, page.text
    rest = app_client.get(
        f"/rentals/history?limit=1&cursor={page.json()['next_cursor']}", headers=person.headers
    )

    # The second trip was shifted less into the past: it is the newer one.
    [newest] = page.json()["items"]
    [older] = rest.json()["items"]
    assert (newest["id"], older["id"]) == (second, first)
    assert rest.json()["next_cursor"] is None
    assert newest["charge_status"] == "paid"
    assert newest["total"] == person.get(second).json()["receipt"]["cost"]["total"]
    assert newest["car"]["plate"]


def test_a_broken_cursor_is_refused(person: Person, app_client: TestClient) -> None:
    response = app_client.get("/rentals/history?cursor=nope", headers=person.headers)

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY


def test_the_rental_tells_where_the_trip_started_and_ended(
    integration_settings: Settings, person: Person, car: str
) -> None:
    rental_id = _paid_trip(integration_settings, person, car)

    body = person.get(rental_id).json()

    assert body["start"] == pytest.approx({"lat": CAR_POINT.lat, "lon": CAR_POINT.lon})
    assert body["end"] == pytest.approx({"lat": CAR_POINT.lat, "lon": CAR_POINT.lon})
    assert body["adjustments"] == []
    assert body["total"] == body["receipt"]["cost"]["total"]


def test_another_clients_rental_is_not_found(
    integration_settings: Settings, person: Person, car: str, app_client: TestClient
) -> None:
    rental_id = _paid_trip(integration_settings, person, car)
    stranger = Person(integration_settings, app_client)

    assert stranger.get(rental_id).status_code == HTTPStatus.NOT_FOUND
    history = app_client.get("/rentals/history", headers=stranger.headers).json()
    assert history["items"] == []


# Who may see and act


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", ""),
        ("GET", "/{id}"),
        ("POST", "/{id}/force-finish"),
        ("POST", "/{id}/adjustments"),
        ("POST", "/{id}/refunds/retry"),
        ("GET", "/{id}/inspections/before/photos/{id}/url"),
    ],
)
def test_a_technician_has_no_business_in_rentals(
    integration_settings: Settings, app_client: TestClient, method: str, path: str
) -> None:
    technician = Staff(integration_settings, StaffRole.TECHNICIAN)
    url = "/admin/rentals" + path.format(id=uuid.uuid4())

    response = app_client.request(
        method, url, json={"reason": "x", "amount": 1, "comment": "x"}, headers=technician.headers
    )

    assert response.status_code == HTTPStatus.FORBIDDEN


def test_a_client_token_is_not_a_staff_token(person: Person, app_client: TestClient) -> None:
    response = app_client.get("/admin/rentals", headers=person.headers)

    assert response.status_code == HTTPStatus.UNAUTHORIZED


def test_money_needs_a_live_session(
    integration_settings: Settings,
    person: Person,
    car: str,
    operator: Staff,
    app_client: TestClient,
) -> None:
    rental_id = _paid_trip(integration_settings, person, car)
    operator.revoke_session()

    response = _adjust(app_client, operator, rental_id, -100, "Late unlock")

    assert response.status_code == HTTPStatus.UNAUTHORIZED
    assert _operations(integration_settings, rental_id)[-1].kind == "capture"


# The list and the card


def test_staff_filter_rentals_by_client_car_status_and_period(
    integration_settings: Settings,
    person: Person,
    car: str,
    operator: Staff,
    app_client: TestClient,
) -> None:
    paid = _paid_trip(integration_settings, person, car)
    other_car = add_priced_car(integration_settings, CAR_POINT)
    driving = _start(person, other_car)

    def ids(**params: str | list[str]) -> list[str]:
        response = app_client.get("/admin/rentals", params=params, headers=operator.headers)
        assert response.status_code == HTTPStatus.OK, response.text
        return [item["id"] for item in response.json()["items"]]

    assert ids(user_id=str(person.id)) == [driving, paid]
    assert ids(car_id=car) == [paid]
    assert ids(user_id=str(person.id), status="in_trip") == [driving]
    assert set(ids(user_id=str(person.id), status=["in_trip", "paid"])) == {driving, paid}
    tomorrow = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    assert ids(user_id=str(person.id), reserved_from=tomorrow) == []
    item = app_client.get(
        "/admin/rentals", params={"user_id": str(person.id)}, headers=operator.headers
    ).json()["items"][0]
    assert item["client"]["id"] == str(person.id)


def test_an_empty_period_is_a_mistake(operator: Staff, app_client: TestClient) -> None:
    now = datetime.now(UTC).isoformat()
    params = {"reserved_from": now, "reserved_to": now}

    response = app_client.get("/admin/rentals", params=params, headers=operator.headers)

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY


def test_the_card_shows_the_intervals_the_money_and_the_inspections(
    integration_settings: Settings,
    person: Person,
    car: str,
    operator: Staff,
    app_client: TestClient,
) -> None:
    rental_id = _paid_trip(integration_settings, person, car)

    response = _card(app_client, operator, rental_id)

    assert response.status_code == HTTPStatus.OK, response.text
    body = response.json()
    assert body["rental"]["status"] == "paid"
    assert [i["kind"] for i in body["intervals"]] == ["driving"]
    assert [(op["kind"], op["status"]) for op in body["operations"]] == [
        ("hold", "succeeded"),
        ("capture", "succeeded"),
    ]
    assert [i["stage"] for i in body["inspections"]] == ["before", "after"]
    assert body["owed"] == 0
    assert body["forced_finish"] is None


def test_a_photo_link_for_staff_is_in_the_audit_log(  # noqa: PLR0913, PLR0917 - the fixtures of the test
    integration_settings: Settings,
    person: Person,
    car: str,
    operator: Staff,
    app_client: TestClient,
    storage: FakeStorage,
) -> None:
    rental_id = _paid_trip(integration_settings, person, car)
    [before, _] = _card(app_client, operator, rental_id).json()["inspections"]
    photo_id = before["photos"][0]["id"]

    response = app_client.get(
        f"/admin/rentals/{rental_id}/inspections/before/photos/{photo_id}/url",
        headers=operator.headers,
    )

    assert response.status_code == HTTPStatus.OK, response.text
    assert response.headers["Cache-Control"] == "no-store"
    [event] = [
        e for e in _audit(integration_settings, rental_id) if e.action == "rental.photo_viewed"
    ]
    assert event.actor_id == operator.id
    assert event.details["photo_id"] == photo_id


# The forced finish


def test_an_operator_ends_a_stuck_trip_without_the_clients_checks(  # noqa: PLR0913, PLR0917 - the fixtures of the test
    integration_settings: Settings,
    person: Person,
    car: str,
    operator: Staff,
    app_client: TestClient,
    gateway: FakeGateway,
) -> None:
    rental_id = _start(person, car)
    _shift(integration_settings, rental_id, 20)

    # No inspection after the trip: the client's finish would be refused.
    response = _force(app_client, operator, rental_id, "  Client  left the car ")

    assert response.status_code == HTTPStatus.OK, response.text
    body = response.json()
    assert body["locked"] is True
    assert body["rental"]["rental"]["status"] == "paid"
    assert body["rental"]["forced_finish"]["reason"] == "Client left the car"
    assert body["rental"]["forced_finish"]["staff_id"] == str(operator.id)
    assert gateway.calls[-1][2] == "close"
    assert car_status(integration_settings, car) == "available"
    [event] = [
        e for e in _audit(integration_settings, rental_id) if e.action == "rental.force_finished"
    ]
    assert event.details == {"locked": True, "status_before": "in_trip"}
    assert "Client" not in str(event.details)


def test_a_trip_ends_even_if_the_car_does_not_answer(  # noqa: PLR0913, PLR0917 - the fixtures of the test
    integration_settings: Settings,
    person: Person,
    car: str,
    operator: Staff,
    app_client: TestClient,
    gateway: FakeGateway,
) -> None:
    rental_id = _start(person, car)
    gateway.outcome = TelematicsTimeoutError()

    response = _force(app_client, operator, rental_id)

    assert response.status_code == HTTPStatus.OK, response.text
    assert response.json()["locked"] is False
    assert response.json()["rental"]["rental"]["ended_at"] is not None
    [event] = _audited(integration_settings, rental_id, "rental.force_finished")
    assert event.details["locked"] is False


def test_a_parked_trip_is_force_finished_too(
    integration_settings: Settings,
    person: Person,
    car: str,
    operator: Staff,
    app_client: TestClient,
) -> None:
    rental_id = _start(person, car)
    parked = person.client.post(f"/rentals/{rental_id}/park", headers=person.headers)
    assert parked.json()["status"] == "parked", parked.text

    response = _force(app_client, operator, rental_id)

    assert response.status_code == HTTPStatus.OK, response.text
    [event] = _audited(integration_settings, rental_id, "rental.force_finished")
    assert event.details["status_before"] == "parked"


@pytest.mark.parametrize("stage", ["reserved", "paid"])
def test_only_a_trip_is_force_finished(  # noqa: PLR0913, PLR0917 - the fixtures of the test
    integration_settings: Settings,
    person: Person,
    car: str,
    operator: Staff,
    app_client: TestClient,
    stage: str,
) -> None:
    if stage == "reserved":
        rental_id = str(person.reserve(car).json()["id"])
    else:
        rental_id = _paid_trip(integration_settings, person, car)

    response = _force(app_client, operator, rental_id)

    assert response.status_code == HTTPStatus.CONFLICT
    assert problem_type(response) == "rental-transition-not-allowed"


def test_a_forced_finish_needs_a_reason(
    integration_settings: Settings,
    person: Person,
    car: str,
    operator: Staff,
    app_client: TestClient,
) -> None:
    rental_id = _start(person, car)

    response = _force(app_client, operator, rental_id, "   ")

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert person.get(rental_id).json()["status"] == "in_trip"


# Adjustments


def test_a_decrease_refunds_the_paid_rental_and_keeps_the_receipt(
    integration_settings: Settings,
    person: Person,
    car: str,
    operator: Staff,
    app_client: TestClient,
) -> None:
    rental_id = _paid_trip(integration_settings, person, car)
    receipt = person.get(rental_id).json()["receipt"]
    total = receipt["cost"]["total"]

    response = _adjust(app_client, operator, rental_id, -100, "Waited for the unlock")

    assert response.status_code == HTTPStatus.CREATED, response.text
    body = response.json()
    assert body["owed"] == 0
    assert [(a["amount"], a["comment"]) for a in body["adjustments"]] == [
        (-100, "Waited for the unlock")
    ]
    refund = _operations(integration_settings, rental_id)[-1]
    assert (refund.kind, refund.amount, refund.status) == ("refund", 100, "succeeded")
    assert refund.idempotency_key.startswith(f"rental:{rental_id}:refund:1:")
    client_view = person.get(rental_id).json()
    assert client_view["receipt"] == receipt
    assert client_view["total"] == total - 100
    assert [a["amount"] for a in client_view["adjustments"]] == [-100]
    assert "comment" not in client_view["adjustments"][0]
    [event] = [e for e in _audit(integration_settings, rental_id) if e.action == "rental.adjusted"]
    assert event.details["amount"] == -100
    assert event.details["total_after"] == total - 100
    assert "Waited" not in str(event.details)


def test_an_increase_is_charged_at_once(
    integration_settings: Settings,
    person: Person,
    car: str,
    operator: Staff,
    app_client: TestClient,
) -> None:
    rental_id = _paid_trip(integration_settings, person, car)

    response = _adjust(app_client, operator, rental_id, 250, "Parking fee of the city")

    assert response.status_code == HTTPStatus.CREATED, response.text
    assert response.json()["rental"]["status"] == "paid"
    assert response.json()["owed"] == 0
    charge = _operations(integration_settings, rental_id)[-1]
    assert (charge.kind, charge.amount, charge.status) == ("charge", 250, "succeeded")
    assert charge.idempotency_key == f"rental:{rental_id}:debt:1"


def test_a_second_increase_takes_a_key_of_its_own(
    integration_settings: Settings,
    person: Person,
    car: str,
    operator: Staff,
    app_client: TestClient,
) -> None:
    rental_id = _paid_trip(integration_settings, person, car)
    _adjust(app_client, operator, rental_id, 100, "First")

    response = _adjust(app_client, operator, rental_id, 50, "Second")

    assert response.json()["owed"] == 0
    charges = [op for op in _operations(integration_settings, rental_id) if op.kind == "charge"]
    assert [(op.idempotency_key[-6:], op.amount) for op in charges] == [
        ("debt:1", 100),
        ("debt:2", 50),
    ]


def test_a_declined_increase_is_a_debt_the_client_pays_later(
    integration_settings: Settings,
    person: Person,
    car: str,
    operator: Staff,
    app_client: TestClient,
) -> None:
    rental_id = _paid_trip(integration_settings, person, car)
    person.bind_card(DECLINED_CARD)
    _make_default(person, "9995")

    response = _adjust(app_client, operator, rental_id, 300, "Smoking in the car")

    assert response.status_code == HTTPStatus.CREATED, response.text
    assert response.json()["rental"]["status"] == "payment_failed"
    assert response.json()["owed"] == 300
    debts = app_client.get("/rentals/debts", headers=person.headers).json()["debts"]
    assert debts == [{"rental_id": rental_id, "amount": 300, "ended_at": debts[0]["ended_at"]}]
    queued = query(
        integration_settings,
        "SELECT params FROM notification_outbox WHERE rental_id = :id AND event = 'payment_failed'",
        id=uuid.UUID(rental_id),
    )
    assert [row.params for row in queued] == [{"amount": 300}]
    another = add_priced_car(integration_settings, CAR_POINT)
    assert problem_type(person.reserve(another)) == "outstanding-debt"
    _make_default(person, "4242")
    paid = app_client.post(f"/rentals/{rental_id}/pay", headers=person.headers)
    assert paid.json()["status"] == "paid"


def test_a_decrease_of_a_debt_lowers_it_and_may_clear_it(
    integration_settings: Settings,
    person: Person,
    car: str,
    operator: Staff,
    app_client: TestClient,
) -> None:
    rental_id = _paid_trip(integration_settings, person, car)
    person.bind_card(DECLINED_CARD)
    _make_default(person, "9995")
    _adjust(app_client, operator, rental_id, 300, "Smoking in the car")

    lowered = _adjust(app_client, operator, rental_id, -100, "Partly disputed")
    cleared = _adjust(app_client, operator, rental_id, -200, "Disputed in full")

    assert (lowered.json()["owed"], lowered.json()["rental"]["status"]) == (200, "payment_failed")
    assert (cleared.json()["owed"], cleared.json()["rental"]["status"]) == (0, "paid")
    assert all(op.kind != "refund" for op in _operations(integration_settings, rental_id))


@pytest.mark.parametrize(
    ("amount", "comment", "status"),
    [
        (0, "Nothing", HTTPStatus.UNPROCESSABLE_ENTITY),
        (100, "  ", HTTPStatus.UNPROCESSABLE_ENTITY),
        (1_000_01, "Too much", HTTPStatus.UNPROCESSABLE_ENTITY),
        (-10_000, "Below zero", HTTPStatus.CONFLICT),
    ],
)
def test_an_adjustment_is_checked(  # noqa: PLR0913, PLR0917 - the case and the fixtures
    integration_settings: Settings,
    person: Person,
    car: str,
    operator: Staff,
    app_client: TestClient,
    amount: int,
    comment: str,
    status: HTTPStatus,
) -> None:
    rental_id = _paid_trip(integration_settings, person, car, minutes=1)

    response = _adjust(app_client, operator, rental_id, amount, comment)

    assert response.status_code == status, response.text
    assert _card(app_client, operator, rental_id).json()["adjustments"] == []


def test_a_rental_on_the_road_is_not_adjusted(
    integration_settings: Settings,
    person: Person,
    car: str,
    operator: Staff,
    app_client: TestClient,
) -> None:
    rental_id = _start(person, car)

    response = _adjust(app_client, operator, rental_id, 100, "Early")

    assert response.status_code == HTTPStatus.CONFLICT
    assert problem_type(response) == "rental-transition-not-allowed"


def test_refunds_without_a_decrease_have_nothing_to_retry(
    integration_settings: Settings,
    person: Person,
    car: str,
    operator: Staff,
    app_client: TestClient,
) -> None:
    rental_id = _paid_trip(integration_settings, person, car)

    response = app_client.post(
        f"/admin/rentals/{rental_id}/refunds/retry", headers=operator.headers
    )

    assert response.status_code == HTTPStatus.CONFLICT


def test_a_refund_the_provider_lost_is_sent_again_with_its_key(  # noqa: PLR0913, PLR0917 - the fixtures of the test
    integration_settings: Settings,
    person: Person,
    car: str,
    operator: Staff,
    app_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rental_id = _paid_trip(integration_settings, person, car)
    original = FakeProvider.refund
    failures = [1]

    async def refund(self: FakeProvider, request: OperationRequest) -> OperationResult:
        if failures[0]:
            failures[0] -= 1
            raise PaymentProviderError("timeout")
        return await original(self, request)

    monkeypatch.setattr(FakeProvider, "refund", refund)
    lowered = _adjust(app_client, operator, rental_id, -100, "Waited for the unlock")
    assert lowered.status_code == HTTPStatus.CREATED, lowered.text
    [lost] = [op for op in _operations(integration_settings, rental_id) if op.kind == "refund"]
    assert lost.status != "succeeded"

    response = app_client.post(
        f"/admin/rentals/{rental_id}/refunds/retry", headers=operator.headers
    )

    assert response.status_code == HTTPStatus.OK, response.text
    refunds = [op for op in _operations(integration_settings, rental_id) if op.kind == "refund"]
    assert [(op.idempotency_key, op.amount, op.status) for op in refunds] == [
        (lost.idempotency_key, 100, "succeeded")
    ]
    assert response.json()["owed"] == 0
    assert len(_audited(integration_settings, rental_id, "rental.refund_retried")) == 1


def test_a_repeat_with_the_same_key_moves_no_money_again(
    integration_settings: Settings,
    person: Person,
    car: str,
    operator: Staff,
    app_client: TestClient,
) -> None:
    rental_id = _paid_trip(integration_settings, person, car)
    key = uuid.uuid4()
    first = _adjust(app_client, operator, rental_id, -100, "Late unlock", key)

    again = _adjust(app_client, operator, rental_id, -100, "Late unlock", key)
    other = _adjust(app_client, operator, rental_id, -200, "Late unlock", key)

    assert (first.status_code, again.status_code) == (HTTPStatus.CREATED, HTTPStatus.CREATED)
    assert again.json()["adjustments"] == first.json()["adjustments"]
    assert other.status_code == HTTPStatus.CONFLICT
    assert problem_type(other) == "idempotency-key-reused"
    refunds = [op for op in _operations(integration_settings, rental_id) if op.kind == "refund"]
    assert [op.amount for op in refunds] == [100]


def test_each_decrease_refunds_its_own_share_over_several_payments(
    integration_settings: Settings,
    person: Person,
    car: str,
    operator: Staff,
    app_client: TestClient,
) -> None:
    rental_id = _paid_trip(integration_settings, person, car)
    _adjust(app_client, operator, rental_id, 300, "Parking fee")

    first = _adjust(app_client, operator, rental_id, -350, "Fee disputed")
    second = _adjust(app_client, operator, rental_id, -20, "Goodwill")

    assert (first.json()["owed"], second.json()["owed"]) == (0, 0)
    refunds = [
        (op.idempotency_key.split(":")[3], op.amount)
        for op in _operations(integration_settings, rental_id)
        if op.kind == "refund"
    ]
    # The increase is adjustment 1. The newest payment goes first: the 300 charged as a debt,
    # then the capture of the trip; one moment's refunds come in any order.
    assert sorted(refunds) == [("2", 50), ("2", 300), ("3", 20)]


def test_no_adjustment_while_a_debt_try_has_no_answer(  # noqa: PLR0913, PLR0917 - the fixtures of the test
    integration_settings: Settings,
    person: Person,
    car: str,
    operator: Staff,
    app_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rental_id = _paid_trip(integration_settings, person, car)
    person.bind_card(DECLINED_CARD)
    _make_default(person, "9995")
    _adjust(app_client, operator, rental_id, 300, "Smoking in the car")
    _make_default(person, "4242")
    _fail_charges_once(monkeypatch)
    unknown = app_client.post(f"/rentals/{rental_id}/pay", headers=person.headers)
    assert unknown.json()["status"] == "payment_failed", unknown.text

    response = _adjust(app_client, operator, rental_id, -100, "Partly disputed")

    assert response.status_code == HTTPStatus.CONFLICT
    assert problem_type(response) == "rental-settlement-pending"
    paid = app_client.post(f"/rentals/{rental_id}/pay", headers=person.headers)
    assert paid.json()["status"] == "paid"


def test_an_increase_with_no_answer_does_not_tell_the_client_it_failed(  # noqa: PLR0913, PLR0917 - the fixtures of the test
    integration_settings: Settings,
    person: Person,
    car: str,
    operator: Staff,
    app_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rental_id = _paid_trip(integration_settings, person, car)
    _fail_charges_once(monkeypatch)

    response = _adjust(app_client, operator, rental_id, 250, "Parking fee of the city")

    assert response.status_code == HTTPStatus.CREATED, response.text
    queued = query(
        integration_settings,
        "SELECT event FROM notification_outbox WHERE rental_id = :id AND event = 'payment_failed'",
        id=uuid.UUID(rental_id),
    )
    assert queued == []
    # The lost charge did go through: asking again finds it paid.
    paid = app_client.post(f"/rentals/{rental_id}/pay", headers=person.headers)
    assert paid.json()["status"] == "paid"
    charges = [op for op in _operations(integration_settings, rental_id) if op.kind == "charge"]
    assert [op.amount for op in charges] == [250]


def test_the_paid_wait_of_a_cancelled_reservation_is_adjusted_too(
    integration_settings: Settings,
    person: Person,
    car: str,
    operator: Staff,
    app_client: TestClient,
) -> None:
    rental_id = str(person.reserve(car).json()["id"])
    _shift_reservation(integration_settings, rental_id, 20)
    cancelled = person.cancel(rental_id).json()
    assert cancelled["charge_status"] == "paid"
    paid_wait = cancelled["receipt"]["cost"]["total"]

    response = _adjust(app_client, operator, rental_id, -paid_wait, "The car would not open")

    assert response.status_code == HTTPStatus.CREATED, response.text
    assert response.json()["rental"]["status"] == "cancelled"
    assert response.json()["rental"]["total"] == 0
    refunds = [op for op in _operations(integration_settings, rental_id) if op.kind == "refund"]
    assert [op.amount for op in refunds] == [paid_wait]


def _shift_reservation(settings: Settings, rental_id: str, minutes: int) -> None:
    execute(
        settings,
        "UPDATE rentals SET reserved_at = reserved_at - make_interval(mins => :minutes), "
        "free_wait_until = free_wait_until - make_interval(mins => :minutes), "
        "expires_at = expires_at - make_interval(mins => :minutes) WHERE id = :id",
        id=uuid.UUID(rental_id),
        minutes=minutes,
    )
