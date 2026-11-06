import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from http import HTTPStatus
from typing import Any, cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from flagman.core.config import Settings
from flagman.core.ratelimit import Limit
from flagman.fleet.tests.conftest import FakeGateway, Staff, run_in_db
from flagman.geo import service as geo
from flagman.identity.domain import StaffRole
from flagman.payments import service as payments
from flagman.payments.domain import DeclineReason, OperationStatus
from flagman.payments.fake_provider import FakeProvider
from flagman.payments.ports import OperationRequest, OperationResult
from flagman.pricing import service as pricing
from flagman.pricing.domain import LOW_BATTERY_FINE_CODE
from flagman.pricing.seed import DEMO_FINES
from flagman.rentals import api as rentals_api
from flagman.rentals.tests.conftest import (
    CAR_POINT,
    FakeStorage,
    Person,
    add_priced_car,
    opened_state,
    photo,
    problem_type,
    query,
)
from flagman.support import service
from flagman.support.domain import FINE_COMMENT_MAX_LENGTH
from flagman.support.tests.conftest import audit_actions

pytestmark = pytest.mark.integration

STAFF_URL = "/admin/support/fines"


@pytest.fixture(autouse=True)
def demo_zone_and_fines(integration_settings: Settings) -> None:
    now = datetime.now(UTC)
    run_in_db(integration_settings, lambda db: geo.seed_demo_zone(db, now))
    run_in_db(integration_settings, lambda db: pricing.seed_demo_fines(db, now))


@pytest.fixture
def car(integration_settings: Settings) -> str:
    return add_priced_car(integration_settings, CAR_POINT)


@pytest.fixture
def declined_charge(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only fines are charged without a hold here: a short trip is captured from its hold."""

    async def charge(self: FakeProvider, request: OperationRequest) -> OperationResult:
        return OperationResult(OperationStatus.DECLINED, decline_reason=DeclineReason.DECLINED)

    monkeypatch.setattr(FakeProvider, "charge", charge)


def _fine_type_id(settings: Settings, code: str) -> str:
    version = run_in_db(settings, lambda db: pricing.current_fine(db, code))
    assert version is not None
    return str(version.fine_type_id)


def _trip(person: Person, car: str, battery_pct: int = 80) -> str:
    """A trip the client has driven and ended; the car reports `battery_pct` when it is locked."""
    response = person.reserve(car)
    assert response.status_code == HTTPStatus.CREATED, response.text
    rental_id = str(response.json()["id"])
    person.inspect(rental_id)
    assert person.command(rental_id, "open").status_code == HTTPStatus.OK
    _report_battery(person.client, battery_pct)
    for index in range(4):
        assert person.upload(rental_id, photo(index), stage="after").status_code == HTTPStatus.OK
    assert person.submit(rental_id, stage="after").status_code == HTTPStatus.OK
    finished = person.client.post(f"/rentals/{rental_id}/finish", headers=person.headers)
    assert finished.status_code == HTTPStatus.OK, finished.text
    return rental_id


def _report_battery(client: TestClient, battery_pct: int) -> None:
    # The car answers every command with its state, and the end of a trip is judged by it.
    state = replace(opened_state(), battery_pct=battery_pct)
    app = cast(FastAPI, client.app)
    app.dependency_overrides[rentals_api.get_telematics_gateway] = lambda: FakeGateway(
        outcome=state
    )


def _settle_due(settings: Settings) -> int:
    gateway = payments.gateway(settings)
    rules = service.fine_rules(settings)
    return run_in_db(
        settings, lambda db: service.settle_due_fines(db, gateway, rules, datetime.now(UTC))
    )


def _my_fines(person: Person, rental_id: str | None = None) -> list[Any]:
    params = {"rental_id": rental_id} if rental_id else {}
    response = person.client.get("/support/fines", params=params, headers=person.headers)
    assert response.status_code == HTTPStatus.OK, response.text
    items: list[Any] = response.json()["items"]
    return items


def _charge(  # noqa: PLR0913 - every field of the operator's form
    client: TestClient,
    staff: Staff,
    rental_id: str,
    fine_type_id: str,
    comment: str = "Smoked",
    *,
    key: uuid.UUID | None = None,
) -> Any:
    return client.post(
        STAFF_URL,
        json={"rental_id": rental_id, "fine_type_id": fine_type_id, "comment": comment},
        headers={**staff.headers, "Idempotency-Key": str(key or uuid.uuid4())},
    )


def _charged(client: TestClient, staff: Staff, rental_id: str, code: str = "smoking") -> Any:
    response = _charge(client, staff, rental_id, _fine_type_id(staff.settings, code))
    assert response.status_code == HTTPStatus.CREATED, response.text
    return response.json()


def _cancel(client: TestClient, staff: Staff, fine_id: str, reason: str = "Mistake") -> Any:
    return client.post(
        f"{STAFF_URL}/{fine_id}/cancel", json={"reason": reason}, headers=staff.headers
    )


def _operations(settings: Settings, fine_id: str) -> list[tuple[str, str, int]]:
    rows = query(
        settings,
        "SELECT kind, status, amount FROM payment_operations "
        "WHERE idempotency_key LIKE :prefix ORDER BY created_at, id",
        prefix=f"fine:{fine_id}:%",
    )
    return [(str(row.kind), str(row.status), int(row.amount)) for row in rows]


def _settle(settings: Settings, fine_id: str, at: datetime | None = None) -> None:
    gateway = payments.gateway(settings)
    rules = service.fine_rules(settings)
    when = at or datetime.now(UTC)
    run_in_db(
        settings, lambda db: service.settle_fine(db, gateway, uuid.UUID(fine_id), rules, when)
    )


def _fine_row(settings: Settings, fine_id: str) -> Any:
    [row] = query(
        settings,
        "SELECT status, charge_attempts, next_at, refund_status, refund_attempts "
        "FROM support_fines WHERE id = :id",
        id=uuid.UUID(fine_id),
    )
    return row


def _answer(monkeypatch: pytest.MonkeyPatch, method: str, status: OperationStatus) -> None:
    result = OperationResult(
        status,
        decline_reason=DeclineReason.DECLINED if status is OperationStatus.DECLINED else None,
    )

    async def answer(self: FakeProvider, request: OperationRequest) -> OperationResult:
        return result

    monkeypatch.setattr(FakeProvider, method, answer)


def _pushes(settings: Settings, user_id: uuid.UUID) -> list[Any]:
    rows = query(
        settings,
        "SELECT params FROM notification_outbox WHERE user_id = :user AND event = 'fine_charged'",
        user=user_id,
    )
    return [row.params for row in rows]


# The automatic fine


def test_a_trip_ended_with_a_low_battery_is_fined_and_charged(
    person: Person, car: str, integration_settings: Settings
) -> None:
    rental_id = _trip(person, car, battery_pct=5)
    [due] = _my_fines(person, rental_id)
    assert due["status"] == "pending"

    _settle(integration_settings, due["id"])

    [fine] = _my_fines(person, rental_id)
    amount = DEMO_FINES[LOW_BATTERY_FINE_CODE].amount
    assert (fine["code"], fine["automatic"], fine["amount"]) == (
        LOW_BATTERY_FINE_CODE,
        True,
        amount,
    )
    assert fine["status"] == "paid"
    assert fine["comment"] is None
    assert _operations(integration_settings, fine["id"]) == [("charge", "succeeded", amount)]
    assert _pushes(integration_settings, person.id) == [{"amount": amount, "number": 1}]
    assert audit_actions(integration_settings, fine["id"]) == ["fine.charged"]


def test_the_job_takes_every_due_fine_in_time(
    person: Person, car: str, integration_settings: Settings
) -> None:
    [fine] = _my_fines(person, _trip(person, car, battery_pct=5))

    # The database is shared by the session: other tests' fines may be due before this one.
    for _ in range(20):
        if _fine_row(integration_settings, fine["id"]).status == "paid":
            break
        assert _settle_due(integration_settings) >= 1

    assert _fine_row(integration_settings, fine["id"]).status == "paid"


def test_a_trip_ends_without_a_fine_when_the_catalog_lost_the_type(
    person: Person, car: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def missing(db: Any, code: str) -> None:
        return None

    monkeypatch.setattr(pricing, "current_fine", missing)

    rental_id = _trip(person, car, battery_pct=5)

    assert _my_fines(person, rental_id) == []


def test_a_trip_ended_with_enough_battery_is_not_fined(person: Person, car: str) -> None:
    rental_id = _trip(person, car, battery_pct=10)

    assert _my_fines(person, rental_id) == []


def test_a_forced_finish_is_not_fined_automatically(
    person: Person, car: str, app_client: TestClient, operator: Staff
) -> None:
    response = person.reserve(car)
    rental_id = str(response.json()["id"])
    person.inspect(rental_id)
    assert person.command(rental_id, "open").status_code == HTTPStatus.OK
    _report_battery(app_client, 3)

    forced = app_client.post(
        f"/admin/rentals/{rental_id}/force-finish",
        json={"reason": "Client left the car"},
        headers=operator.headers,
    )

    assert forced.status_code == HTTPStatus.OK, forced.text
    # The low battery reached the rental: only the forced finish spares it the fine.
    [rental] = query(
        person.settings,
        "SELECT end_battery_pct FROM rentals WHERE id = :id",
        id=uuid.UUID(rental_id),
    )
    assert rental.end_battery_pct == 3
    assert _my_fines(person, rental_id) == []


def test_a_trip_earns_the_automatic_fine_once(
    person: Person, car: str, integration_settings: Settings
) -> None:
    rental_id = _trip(person, car, battery_pct=5)
    rental = run_in_db(
        integration_settings,
        lambda db: _rental(db, uuid.UUID(rental_id)),
    )
    fines = service.TripFines(service.fine_rules(integration_settings))

    async def finish_again(db: Any) -> None:
        async with db.begin():
            await fines.trip_finished(db, rental, datetime.now(UTC))

    run_in_db(integration_settings, finish_again)

    assert len(_my_fines(person, rental_id)) == 1


async def _rental(db: Any, rental_id: uuid.UUID) -> Any:
    from flagman.rentals import service as rentals  # noqa: PLC0415 - only this test reads it

    async with db.begin():
        return (await rentals.get_rentals(db, {rental_id}))[rental_id]


def test_settling_again_charges_nothing_twice(
    person: Person, car: str, integration_settings: Settings
) -> None:
    rental_id = _trip(person, car, battery_pct=5)
    [fine] = _my_fines(person, rental_id)
    gateway = payments.gateway(integration_settings)
    rules = service.fine_rules(integration_settings)

    for _ in range(3):
        run_in_db(
            integration_settings,
            lambda db: service.settle_fine(
                db, gateway, uuid.UUID(fine["id"]), rules, datetime.now(UTC)
            ),
        )

    assert len(_operations(integration_settings, fine["id"])) == 1


# Manual fines


def test_an_operator_fines_an_ended_trip_and_the_card_is_charged(
    person: Person,
    car: str,
    app_client: TestClient,
    operator: Staff,
    integration_settings: Settings,
) -> None:
    rental_id = _trip(person, car)

    fine = _charged(app_client, operator, rental_id)

    amount = DEMO_FINES["smoking"].amount
    assert (fine["code"], fine["automatic"], fine["status"]) == ("smoking", False, "paid")
    assert (fine["comment"], fine["created_by"], fine["amount"]) == (
        "Smoked",
        str(operator.id),
        amount,
    )
    assert [event["action"] for event in fine["events"]] == ["charged", "paid"]
    assert _my_fines(person) == [
        {key: value for key, value in fine.items() if key in _my_fines(person)[0]}
    ]
    assert audit_actions(integration_settings, fine["id"]) == ["fine.charged"]
    assert _pushes(integration_settings, person.id) == [{"amount": amount, "number": 1}]


def test_a_fine_keeps_the_amount_it_was_charged_with(
    person: Person,
    car: str,
    app_client: TestClient,
    operator: Staff,
    integration_settings: Settings,
) -> None:
    rental_id = _trip(person, car)
    admin = Staff(integration_settings, StaffRole.ADMIN)
    # A type of its own: the demo ones are shared by every test of the session.
    code = f"t{uuid.uuid4().hex[:12]}"
    created = app_client.post(
        "/admin/fine-types",
        json={"code": code, "name_ru": "Тест", "name_en": "Test", "amount": 4000},
        headers=admin.headers,
    )
    assert created.status_code == HTTPStatus.CREATED, created.text
    fine = _charged(app_client, operator, rental_id, code)
    changed = app_client.put(
        f"/admin/fine-types/{created.json()['fine_type_id']}",
        json={"based_on_version": 1, "name_ru": "Тест", "name_en": "Test", "amount": 4100},
        headers=admin.headers,
    )
    assert changed.status_code == HTTPStatus.OK, changed.text

    again = app_client.get(f"{STAFF_URL}/{fine['id']}", headers=operator.headers).json()

    assert again["amount"] == fine["amount"]


def test_a_repeated_request_charges_the_fine_once(
    person: Person,
    car: str,
    app_client: TestClient,
    operator: Staff,
    integration_settings: Settings,
) -> None:
    rental_id = _trip(person, car)
    type_id = _fine_type_id(integration_settings, "smoking")
    key = uuid.uuid4()

    first = _charge(app_client, operator, rental_id, type_id, key=key)
    again = _charge(app_client, operator, rental_id, type_id, key=key)

    assert (first.status_code, again.status_code) == (HTTPStatus.CREATED, HTTPStatus.CREATED)
    assert again.json()["id"] == first.json()["id"]
    assert len(_my_fines(person, rental_id)) == 1
    assert len(_operations(integration_settings, first.json()["id"])) == 1


def test_a_key_sent_with_another_fine_is_refused(
    person: Person,
    car: str,
    app_client: TestClient,
    operator: Staff,
    integration_settings: Settings,
) -> None:
    rental_id = _trip(person, car)
    type_id = _fine_type_id(integration_settings, "smoking")
    key = uuid.uuid4()
    first = _charge(app_client, operator, rental_id, type_id, key=key)
    assert first.status_code == HTTPStatus.CREATED

    other = _charge(app_client, operator, rental_id, type_id, "Another comment", key=key)

    assert other.status_code == HTTPStatus.CONFLICT
    assert problem_type(other) == "idempotency-key-reused"
    assert len(_my_fines(person, rental_id)) == 1


def test_a_manual_fine_needs_an_idempotency_key(
    person: Person,
    car: str,
    app_client: TestClient,
    operator: Staff,
    integration_settings: Settings,
) -> None:
    response = app_client.post(
        STAFF_URL,
        json={
            "rental_id": _trip(person, car),
            "fine_type_id": _fine_type_id(integration_settings, "smoking"),
            "comment": "Smoked",
        },
        headers=operator.headers,
    )

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY


def test_the_longest_comment_is_kept_in_the_history(
    person: Person,
    car: str,
    app_client: TestClient,
    operator: Staff,
    integration_settings: Settings,
) -> None:
    comment = "x" * FINE_COMMENT_MAX_LENGTH
    response = _charge(
        app_client,
        operator,
        _trip(person, car),
        _fine_type_id(integration_settings, "smoking"),
        comment,
    )

    assert response.status_code == HTTPStatus.CREATED, response.text
    assert response.json()["events"][0]["comment"] == comment


def test_an_unknown_outcome_is_asked_again_with_the_same_key(
    person: Person,
    car: str,
    app_client: TestClient,
    operator: Staff,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rental_id = _trip(person, car)
    original = FakeProvider.charge
    _answer(monkeypatch, "charge", OperationStatus.FAILED)
    fine = _charged(app_client, operator, rental_id)
    row = _fine_row(person.settings, fine["id"])
    assert (fine["status"], row.charge_attempts, row.next_at is not None) == ("pending", 1, True)
    monkeypatch.setattr(FakeProvider, "charge", original)

    _settle(person.settings, fine["id"], row.next_at)

    assert _fine_row(person.settings, fine["id"]).status == "paid"
    assert _operations(person.settings, fine["id"]) == [("charge", "succeeded", fine["amount"])]


@pytest.mark.usefixtures("declined_charge")
def test_the_worker_retries_a_debt_until_the_attempts_are_spent(
    person: Person,
    car: str,
    app_client: TestClient,
    operator: Staff,
    integration_settings: Settings,
) -> None:
    fine = _charged(app_client, operator, _trip(person, car))
    retry = service.fine_rules(integration_settings).settlement.retry

    for attempt in range(1, retry.max_attempts):
        row = _fine_row(integration_settings, fine["id"])
        assert (row.status, row.charge_attempts, row.next_at is not None) == (
            "unpaid",
            attempt,
            True,
        )
        _settle(integration_settings, fine["id"], row.next_at)

    spent = _fine_row(integration_settings, fine["id"])
    assert (spent.status, spent.charge_attempts, spent.next_at) == (
        "unpaid",
        retry.max_attempts,
        None,
    )
    assert len(_operations(integration_settings, fine["id"])) == retry.max_attempts


@pytest.mark.parametrize("comment", ["", "   "])
def test_a_manual_fine_needs_a_comment(
    person: Person, car: str, app_client: TestClient, operator: Staff, comment: str
) -> None:
    rental_id = _trip(person, car)
    smoking = _fine_type_id(operator.settings, "smoking")

    response = _charge(app_client, operator, rental_id, smoking, comment)

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY


def test_an_automatic_type_is_not_charged_by_hand(
    person: Person, car: str, app_client: TestClient, operator: Staff
) -> None:
    rental_id = _trip(person, car)
    low_battery = _fine_type_id(operator.settings, LOW_BATTERY_FINE_CODE)

    response = _charge(app_client, operator, rental_id, low_battery)

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert problem_type(response) == "fine-type-not-manual"


def test_a_reservation_without_a_trip_is_not_fined(
    person: Person, car: str, app_client: TestClient, operator: Staff
) -> None:
    rental_id = str(person.reserve(car).json()["id"])
    assert person.cancel(rental_id).status_code == HTTPStatus.OK

    response = _charge(app_client, operator, rental_id, _fine_type_id(operator.settings, "dirt"))

    assert response.status_code == HTTPStatus.CONFLICT
    assert problem_type(response) == "fine-rental-not-finished"


def test_unknown_rental_and_type_are_not_found(
    person: Person, car: str, app_client: TestClient, operator: Staff
) -> None:
    rental_id = _trip(person, car)
    dirt = _fine_type_id(operator.settings, "dirt")

    no_rental = _charge(app_client, operator, str(uuid.uuid4()), dirt)
    no_type = _charge(app_client, operator, rental_id, str(uuid.uuid4()))

    assert no_rental.status_code == HTTPStatus.NOT_FOUND
    assert problem_type(no_type) == "fine-type-not-found"


# Debts


@pytest.mark.usefixtures("declined_charge")
def test_a_declined_fine_is_a_debt_that_blocks_a_new_reservation(
    person: Person, car: str, app_client: TestClient, operator: Staff
) -> None:
    rental_id = _trip(person, car)

    fine = _charged(app_client, operator, rental_id)

    assert fine["status"] == "unpaid"
    blocked = person.reserve(car)
    assert blocked.status_code == HTTPStatus.CONFLICT
    assert problem_type(blocked) == "outstanding-debt"


@pytest.mark.usefixtures("declined_charge")
def test_the_client_card_shows_an_unpaid_fine_in_the_debt(
    person: Person, car: str, app_client: TestClient, operator: Staff
) -> None:
    fine = _charged(app_client, operator, _trip(person, car))

    card = app_client.get(f"/admin/clients/{person.id}", headers=operator.headers)

    assert card.status_code == HTTPStatus.OK, card.text
    assert card.json()["debt"] == fine["amount"]


def test_the_client_pays_an_unpaid_fine_and_may_reserve_again(
    person: Person,
    car: str,
    app_client: TestClient,
    operator: Staff,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rental_id = _trip(person, car)
    declined = OperationResult(OperationStatus.DECLINED, decline_reason=DeclineReason.DECLINED)
    original = FakeProvider.charge

    async def decline(self: FakeProvider, request: OperationRequest) -> OperationResult:
        return declined

    monkeypatch.setattr(FakeProvider, "charge", decline)
    fine = _charged(app_client, operator, rental_id)
    monkeypatch.setattr(FakeProvider, "charge", original)

    paid = person.client.post(f"/support/fines/{fine['id']}/pay", headers=person.headers)

    assert paid.status_code == HTTPStatus.OK, paid.text
    assert paid.json()["status"] == "paid"
    assert [op[1] for op in _operations(person.settings, fine["id"])] == ["declined", "succeeded"]
    assert person.reserve(car).status_code == HTTPStatus.CREATED


def test_fine_payments_are_rate_limited(
    person: Person,
    car: str,
    app_client: TestClient,
    operator: Staff,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rental_id = _trip(person, car)
    declined = OperationResult(OperationStatus.DECLINED, decline_reason=DeclineReason.DECLINED)
    original = FakeProvider.charge

    async def decline(self: FakeProvider, request: OperationRequest) -> OperationResult:
        return declined

    monkeypatch.setattr(FakeProvider, "charge", decline)
    fine = _charged(app_client, operator, rental_id)
    monkeypatch.setattr(
        service, "PAY_FINE_LIMIT", Limit(f"support-fine-pay-test-{uuid.uuid4()}", 1, 3600)
    )
    pay_url = f"/support/fines/{fine['id']}/pay"
    assert person.client.post(pay_url, headers=person.headers).json()["status"] == "unpaid"
    monkeypatch.setattr(FakeProvider, "charge", original)
    operations = _operations(person.settings, fine["id"])

    limited = person.client.post(pay_url, headers=person.headers)

    assert limited.status_code == HTTPStatus.TOO_MANY_REQUESTS, limited.text
    assert "Retry-After" in limited.headers
    assert _operations(person.settings, fine["id"]) == operations
    assert [item["status"] for item in _my_fines(person, rental_id)] == ["unpaid"]


# Cancellation


def test_a_cancelled_paid_fine_is_refunded_and_kept(
    person: Person,
    car: str,
    app_client: TestClient,
    operator: Staff,
    integration_settings: Settings,
) -> None:
    rental_id = _trip(person, car)
    fine = _charged(app_client, operator, rental_id)

    response = _cancel(app_client, operator, fine["id"], "  Wrong car  ")

    assert response.status_code == HTTPStatus.OK, response.text
    body = response.json()
    assert (body["status"], body["refund_status"]) == ("cancelled", "succeeded")
    assert (body["cancel_reason"], body["cancelled_by"]) == ("Wrong car", str(operator.id))
    assert [event["action"] for event in body["events"]] == [
        "charged",
        "paid",
        "cancelled",
        "refunded",
    ]
    assert _operations(integration_settings, fine["id"]) == [
        ("charge", "succeeded", fine["amount"]),
        ("refund", "succeeded", fine["amount"]),
    ]
    [mine] = _my_fines(person, rental_id)
    assert (mine["status"], mine["cancel_reason"]) == ("cancelled", "Wrong car")
    assert audit_actions(integration_settings, fine["id"]) == ["fine.charged", "fine.cancelled"]


def test_cancelling_again_changes_nothing(
    person: Person,
    car: str,
    app_client: TestClient,
    operator: Staff,
    integration_settings: Settings,
) -> None:
    fine = _charged(app_client, operator, _trip(person, car))
    first = _cancel(app_client, operator, fine["id"]).json()

    again = _cancel(app_client, operator, fine["id"], "Another reason")

    assert again.status_code == HTTPStatus.OK
    assert again.json()["cancel_reason"] == first["cancel_reason"]
    assert len(_operations(integration_settings, fine["id"])) == 2


def test_fines_share_the_money_action_limit_per_staff_member(  # noqa: PLR0913, PLR0917 - the fixtures of the test
    person: Person,
    car: str,
    app_client: TestClient,
    operator: Staff,
    integration_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        payments, "STAFF_MONEY_LIMIT", Limit(f"money-actions-staff-test-{uuid.uuid4()}", 1, 60)
    )
    rental_id = _trip(person, car)
    key = uuid.uuid4()
    smoking = _fine_type_id(integration_settings, "smoking")
    first = _charge(app_client, operator, rental_id, smoking, key=key)
    assert first.status_code == HTTPStatus.CREATED, first.text
    fine = first.json()

    cancelled = _cancel(app_client, operator, fine["id"])
    charged = _charge(app_client, operator, rental_id, smoking)
    # A repeat of a request that went through is counted too, and still charges nothing.
    repeat = _charge(app_client, operator, rental_id, smoking, key=key)
    # One counter for every money action: a rental adjustment is refused as well.
    adjusted = app_client.post(
        f"/admin/rentals/{rental_id}/adjustments",
        json={"amount": -100, "comment": "Late unlock"},
        headers={**operator.headers, "Idempotency-Key": str(uuid.uuid4())},
    )

    for limited in (cancelled, charged, repeat, adjusted):
        assert limited.status_code == HTTPStatus.TOO_MANY_REQUESTS, limited.text
        assert "Retry-After" in limited.headers
    assert [mine["status"] for mine in _my_fines(person, rental_id)] == ["paid"]
    assert _operations(integration_settings, fine["id"]) == [
        ("charge", "succeeded", fine["amount"])
    ]
    assert audit_actions(integration_settings, fine["id"]) == ["fine.charged"]
    # Counted per staff member: a colleague still cancels the fine and the money goes back.
    colleague = Staff(integration_settings, StaffRole.OPERATOR)
    refunded = _cancel(app_client, colleague, fine["id"])
    assert refunded.status_code == HTTPStatus.OK, refunded.text
    assert refunded.json()["refund_status"] == "succeeded"


@pytest.mark.usefixtures("declined_charge")
def test_a_cancelled_unpaid_fine_is_no_longer_a_debt(
    person: Person, car: str, app_client: TestClient, operator: Staff
) -> None:
    fine = _charged(app_client, operator, _trip(person, car))

    body = _cancel(app_client, operator, fine["id"]).json()

    assert (body["status"], body["refund_status"]) == ("cancelled", None)
    assert person.reserve(car).status_code == HTTPStatus.CREATED


def test_a_cancellation_needs_a_reason(
    person: Person, car: str, app_client: TestClient, operator: Staff
) -> None:
    fine = _charged(app_client, operator, _trip(person, car))

    response = _cancel(app_client, operator, fine["id"], " ")

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY


def test_a_declined_refund_is_tried_again_by_cancelling_again(
    person: Person,
    car: str,
    app_client: TestClient,
    operator: Staff,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fine = _charged(app_client, operator, _trip(person, car))
    original = FakeProvider.refund
    _answer(monkeypatch, "refund", OperationStatus.DECLINED)
    failed = _cancel(app_client, operator, fine["id"]).json()
    assert failed["refund_status"] == "failed"
    assert failed["events"][-1]["action"] == "refund_failed"
    monkeypatch.setattr(FakeProvider, "refund", original)

    again = _cancel(app_client, operator, fine["id"], "Card works again").json()

    assert again["refund_status"] == "succeeded"
    assert (again["cancel_reason"], [event["action"] for event in again["events"][-2:]]) == (
        "Mistake",
        ["refund_retried", "refunded"],
    )
    assert again["events"][-2]["comment"] == "Card works again"
    assert audit_actions(person.settings, fine["id"]) == [
        "fine.charged",
        "fine.cancelled",
        "fine.refund_retried",
    ]
    assert _operations(person.settings, fine["id"]) == [
        ("charge", "succeeded", fine["amount"]),
        ("refund", "declined", fine["amount"]),
        ("refund", "succeeded", fine["amount"]),
    ]
    assert _fine_row(person.settings, fine["id"]).refund_attempts == 2


def test_a_cancelled_fine_of_unknown_charge_is_refunded_once_the_charge_proves_taken(
    person: Person,
    car: str,
    app_client: TestClient,
    operator: Staff,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = FakeProvider.charge
    _answer(monkeypatch, "charge", OperationStatus.FAILED)
    fine = _charged(app_client, operator, _trip(person, car))

    cancelled = _cancel(app_client, operator, fine["id"]).json()

    # Still unknown: nothing is closed until the provider tells.
    assert (cancelled["status"], cancelled["refund_status"]) == ("cancelled", "pending")
    row = _fine_row(person.settings, fine["id"])
    assert row.next_at is not None
    monkeypatch.setattr(FakeProvider, "charge", original)

    _settle(person.settings, fine["id"], row.next_at)

    assert _fine_row(person.settings, fine["id"]).refund_status == "succeeded"
    assert _operations(person.settings, fine["id"]) == [
        ("charge", "succeeded", fine["amount"]),
        ("refund", "succeeded", fine["amount"]),
    ]


def test_a_cancelled_fine_of_a_declined_unknown_charge_is_closed_without_a_refund(
    person: Person,
    car: str,
    app_client: TestClient,
    operator: Staff,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _answer(monkeypatch, "charge", OperationStatus.FAILED)
    fine = _charged(app_client, operator, _trip(person, car))
    _cancel(app_client, operator, fine["id"])
    row = _fine_row(person.settings, fine["id"])
    _answer(monkeypatch, "charge", OperationStatus.DECLINED)

    _settle(person.settings, fine["id"], row.next_at)

    closed = _fine_row(person.settings, fine["id"])
    assert (closed.status, closed.refund_status, closed.next_at) == ("cancelled", None, None)
    assert [op[0] for op in _operations(person.settings, fine["id"])] == ["charge"]


def test_a_charge_that_answers_after_the_cancellation_is_refunded(
    person: Person,
    car: str,
    app_client: TestClient,
    operator: Staff,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rental_id = _trip(person, car, battery_pct=5)
    [fine] = _my_fines(person, rental_id)
    original = FakeProvider.charge

    async def cancelled_meanwhile(self: FakeProvider, request: OperationRequest) -> OperationResult:
        # The operator cancels while the worker waits for the provider, which does not answer.
        assert _cancel(app_client, operator, fine["id"]).status_code == HTTPStatus.OK
        return OperationResult(OperationStatus.FAILED)

    monkeypatch.setattr(FakeProvider, "charge", cancelled_meanwhile)
    _settle(person.settings, fine["id"])
    row = _fine_row(person.settings, fine["id"])
    assert (row.status, row.refund_status, row.next_at is not None) == (
        "cancelled",
        "pending",
        True,
    )
    monkeypatch.setattr(FakeProvider, "charge", original)

    _settle(person.settings, fine["id"], row.next_at)

    assert _fine_row(person.settings, fine["id"]).refund_status == "succeeded"
    assert _operations(person.settings, fine["id"]) == [
        ("charge", "succeeded", fine["amount"]),
        ("refund", "succeeded", fine["amount"]),
    ]


def test_a_charge_never_recorded_is_refunded_by_the_cancellation(
    person: Person,
    car: str,
    app_client: TestClient,
    operator: Staff,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    [fine] = _my_fines(person, _trip(person, car, battery_pct=5))

    async def crash(*args: object) -> None:
        raise RuntimeError("the worker died after the provider answered")

    # The money is taken, but the fine never learns of it: still pending, no attempt recorded.
    with monkeypatch.context() as patched:
        patched.setattr(service, "_record_charge", crash)
        with pytest.raises(RuntimeError):
            _settle(person.settings, fine["id"])
    assert _fine_row(person.settings, fine["id"]).charge_attempts == 0

    cancelled = _cancel(app_client, operator, fine["id"]).json()

    assert (cancelled["status"], cancelled["refund_status"]) == ("cancelled", "succeeded")
    assert _operations(person.settings, fine["id"]) == [
        ("charge", "succeeded", fine["amount"]),
        ("refund", "succeeded", fine["amount"]),
    ]


# Photos


def test_the_client_sees_the_photos_an_operator_added(
    person: Person, car: str, app_client: TestClient, operator: Staff, storage: FakeStorage
) -> None:
    fine = _charged(app_client, operator, _trip(person, car))

    added = app_client.post(
        f"{STAFF_URL}/{fine['id']}/photos",
        content=photo(1),
        headers={**operator.headers, "Content-Type": "image/jpeg"},
    )

    assert added.status_code == HTTPStatus.CREATED, added.text
    [shown] = added.json()["photos"]
    url = person.client.get(
        f"/support/fines/{fine['id']}/photos/{shown['id']}/url", headers=person.headers
    )
    assert url.status_code == HTTPStatus.OK
    assert url.headers["Cache-Control"] == "no-store"
    assert any(key.startswith(f"fines/{fine['id']}/") for key in storage.objects)
    assert audit_actions(operator.settings, fine["id"]) == ["fine.charged", "fine.photo_added"]


def test_every_photo_an_operator_opens_is_audited(
    person: Person, car: str, app_client: TestClient, operator: Staff
) -> None:
    fine = _charged(app_client, operator, _trip(person, car))
    added = app_client.post(
        f"{STAFF_URL}/{fine['id']}/photos",
        content=photo(1),
        headers={**operator.headers, "Content-Type": "image/jpeg"},
    )
    [shown] = added.json()["photos"]

    url = app_client.get(
        f"{STAFF_URL}/{fine['id']}/photos/{shown['id']}/url", headers=operator.headers
    )

    assert url.status_code == HTTPStatus.OK, url.text
    assert url.headers["Cache-Control"] == "no-store"
    assert audit_actions(operator.settings, fine["id"]) == [
        "fine.charged",
        "fine.photo_added",
        "fine.photo_viewed",
    ]


def test_a_cancelled_fine_takes_no_photos(
    person: Person, car: str, app_client: TestClient, operator: Staff
) -> None:
    fine = _charged(app_client, operator, _trip(person, car))
    _cancel(app_client, operator, fine["id"])

    added = app_client.post(
        f"{STAFF_URL}/{fine['id']}/photos",
        content=photo(1),
        headers={**operator.headers, "Content-Type": "image/jpeg"},
    )

    assert added.status_code == HTTPStatus.CONFLICT


def test_photo_uploads_are_limited_per_staff_member(  # noqa: PLR0913, PLR0917 - the fixtures of the test
    person: Person,
    car: str,
    app_client: TestClient,
    operator: Staff,
    storage: FakeStorage,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fine = _charged(app_client, operator, _trip(person, car))
    monkeypatch.setattr(
        service,
        "STAFF_UPLOAD_LIMIT",
        Limit(f"support-fine-photo-upload-staff-test-{uuid.uuid4()}", 1, 3600),
    )
    _add_fine_photo(app_client, operator, fine["id"])

    limited = app_client.post(
        f"{STAFF_URL}/{fine['id']}/photos",
        content=photo(2),
        headers={**operator.headers, "Content-Type": "image/jpeg"},
    )

    assert limited.status_code == HTTPStatus.TOO_MANY_REQUESTS, limited.text
    assert "Retry-After" in limited.headers
    assert len([key for key in storage.objects if key.startswith(f"fines/{fine['id']}/")]) == 1
    assert audit_actions(operator.settings, fine["id"]) == ["fine.charged", "fine.photo_added"]


# Access


def test_a_broken_cursor_of_my_fines_is_refused(person: Person) -> None:
    response = person.client.get("/support/fines", params={"cursor": "abc"}, headers=person.headers)

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY


def test_another_clients_fine_is_not_found(
    person: Person,
    car: str,
    app_client: TestClient,
    operator: Staff,
    integration_settings: Settings,
) -> None:
    fine = _charged(app_client, operator, _trip(person, car))
    added = app_client.post(
        f"{STAFF_URL}/{fine['id']}/photos",
        content=photo(1),
        headers={**operator.headers, "Content-Type": "image/jpeg"},
    )
    photo_id = added.json()["photos"][0]["id"]
    other = Person(integration_settings, app_client)

    responses = [
        other.client.get(f"/support/fines/{fine['id']}", headers=other.headers),
        other.client.post(f"/support/fines/{fine['id']}/pay", headers=other.headers),
        other.client.get(
            f"/support/fines/{fine['id']}/photos/{photo_id}/url", headers=other.headers
        ),
    ]

    assert [response.status_code for response in responses] == [HTTPStatus.NOT_FOUND] * 3
    assert all(problem_type(response) == "fine-not-found" for response in responses)
    assert _my_fines(other) == []
    assert _my_fines(other, fine["rental_id"]) == []


def _add_fine_photo(client: TestClient, staff: Staff, fine_id: str, seed: int = 1) -> str:
    response = client.post(
        f"{STAFF_URL}/{fine_id}/photos",
        content=photo(seed),
        headers={**staff.headers, "Content-Type": "image/jpeg"},
    )
    assert response.status_code == HTTPStatus.CREATED, response.text
    return str(response.json()["photos"][-1]["id"])


def test_a_photo_of_another_fine_is_not_found_under_ones_own(
    person: Person,
    car: str,
    app_client: TestClient,
    operator: Staff,
    integration_settings: Settings,
) -> None:
    own = _charged(app_client, operator, _trip(person, car))["id"]
    _add_fine_photo(app_client, operator, own)
    other = Person(integration_settings, app_client)
    other_car = add_priced_car(integration_settings, CAR_POINT)
    foreign = _charged(app_client, operator, _trip(other, other_car))["id"]
    foreign_photo = _add_fine_photo(app_client, operator, foreign, 2)

    responses = [
        person.client.get(
            f"/support/fines/{own}/photos/{foreign_photo}/url", headers=person.headers
        ),
        app_client.get(f"{STAFF_URL}/{own}/photos/{foreign_photo}/url", headers=operator.headers),
    ]

    assert [response.status_code for response in responses] == [HTTPStatus.NOT_FOUND] * 2
    assert all(problem_type(response) == "fine-photo-not-found" for response in responses)
    assert "fine.photo_viewed" not in audit_actions(integration_settings, own)


@pytest.mark.parametrize(
    ("role", "expected"),
    [(StaffRole.TECHNICIAN, HTTPStatus.FORBIDDEN), (None, HTTPStatus.UNAUTHORIZED)],
)
def test_technicians_and_clients_have_no_staff_fines(
    app_client: TestClient,
    integration_settings: Settings,
    role: StaffRole | None,
    expected: HTTPStatus,
) -> None:
    if role is None:
        headers = Person(integration_settings, app_client).headers
    else:
        headers = Staff(integration_settings, role).headers

    responses = [
        app_client.get(STAFF_URL, headers=headers),
        app_client.post(STAFF_URL, json={}, headers=headers),
        app_client.post(
            f"{STAFF_URL}/{uuid.uuid4()}/cancel", json={"reason": "x"}, headers=headers
        ),
    ]

    assert [response.status_code for response in responses] == [expected] * 3


def test_unknown_fields_are_refused(
    person: Person, car: str, app_client: TestClient, operator: Staff
) -> None:
    rental_id = _trip(person, car)
    body = {
        "rental_id": rental_id,
        "fine_type_id": _fine_type_id(operator.settings, "dirt"),
        "comment": "Dirty",
        "amount": 1,
    }

    response = app_client.post(STAFF_URL, json=body, headers=operator.headers)

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY


# The list


def test_staff_filter_fines_by_status_type_client_and_period(
    person: Person, car: str, app_client: TestClient, operator: Staff
) -> None:
    rental_id = _trip(person, car)
    smoking = _charged(app_client, operator, rental_id, "smoking")
    dirt = _charged(app_client, operator, rental_id, "dirt")
    _cancel(app_client, operator, dirt["id"])

    def ids(**params: Any) -> list[str]:
        response = app_client.get(
            STAFF_URL, params={"user_id": str(person.id), **params}, headers=operator.headers
        )
        assert response.status_code == HTTPStatus.OK, response.text
        return [item["id"] for item in response.json()["items"]]

    soon = (datetime.now(UTC) + timedelta(minutes=1)).isoformat()
    assert ids() == [dirt["id"], smoking["id"]]
    assert ids(status="cancelled") == [dirt["id"]]
    assert ids(fine_type_id=smoking["fine_type_id"]) == [smoking["id"]]
    assert ids(created_from=soon) == []
    assert ids(created_to=soon) == [dirt["id"], smoking["id"]]
    assert ids(user_id=str(uuid.uuid4())) == []


def test_the_list_goes_page_by_page(
    person: Person, car: str, app_client: TestClient, operator: Staff
) -> None:
    rental_id = _trip(person, car)
    charged = [
        _charged(app_client, operator, rental_id, code)["id"] for code in ("dirt", "smoking")
    ]
    params: dict[str, str | int] = {"user_id": str(person.id), "limit": 1}

    first = app_client.get(STAFF_URL, params=params, headers=operator.headers).json()
    second = app_client.get(
        STAFF_URL, params={**params, "cursor": first["next_cursor"]}, headers=operator.headers
    ).json()

    assert [first["items"][0]["id"], second["items"][0]["id"]] == charged[::-1]
    assert second["next_cursor"] is None
