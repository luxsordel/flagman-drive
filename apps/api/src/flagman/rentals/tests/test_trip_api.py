import asyncio
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from flagman.core.config import Settings
from flagman.core.db import create_engine, create_session_factory
from flagman.fleet.domain import CarCommandRejectedError
from flagman.fleet.tests.conftest import FakeGateway, run_in_db
from flagman.geo import service as geo
from flagman.notifications import service as notifications
from flagman.notifications.domain import PushPlatform
from flagman.notifications.fake_provider import FakePushProvider
from flagman.notifications.tests.conftest import push_token, sign_in
from flagman.payments import service as payments
from flagman.payments.fake_provider import FakeProvider
from flagman.payments.ports import OperationRequest, OperationResult, PaymentProviderError
from flagman.rentals import service
from flagman.rentals.tests.conftest import (
    CAR_POINT,
    DECLINED_CARD,
    GOOD_CARD,
    Person,
    add_priced_car,
    car_status,
    execute,
    photo,
    problem_type,
    query,
)

pytestmark = pytest.mark.integration

DEMO_HOLD = 3000


@pytest.fixture(autouse=True)
def demo_zone(integration_settings: Settings) -> None:
    """The demo zone covers the cars of these tests; seeding again changes nothing."""
    run_in_db(integration_settings, lambda db: geo.seed_demo_zone(db, datetime.now(UTC)))


@pytest.fixture
def person(integration_settings: Settings, app_client: TestClient) -> Person:
    return Person(integration_settings, app_client)


@pytest.fixture
def car(integration_settings: Settings) -> str:
    return add_priced_car(integration_settings, CAR_POINT)


@dataclass(frozen=True)
class Trip:
    person: Person
    car: str
    rental_id: str


@pytest.fixture
def trip(person: Person, car: str) -> Trip:
    """A trip on the road, with the inspection after it done."""
    rental_id = _start(person, car)
    _inspect_after(person, rental_id)
    return Trip(person, car, rental_id)


def _start(person: Person, car: str) -> str:
    response = person.reserve(car)
    assert response.status_code == 201, response.text
    rental_id = str(response.json()["id"])
    person.inspect(rental_id)
    opened = person.command(rental_id, "open")
    assert opened.status_code == 200, opened.text
    assert opened.json()["status"] == "in_trip"
    return rental_id


def _inspect_after(person: Person, rental_id: str) -> None:
    for index in range(4):
        assert person.upload(rental_id, photo(index), stage="after").status_code == 200
    assert person.submit(rental_id, stage="after").status_code == 200


def _post(person: Person, rental_id: str, action: str) -> Any:
    return person.client.post(f"/rentals/{rental_id}/{action}", headers=person.headers)


def _shift(settings: Settings, rental_id: str, minutes: int) -> None:
    """Moves the whole rental into the past, as if it had lasted `minutes` longer."""
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
        "WHERE idempotency_key LIKE :prefix ORDER BY created_at",
        prefix=f"rental:{rental_id}:%",
    )


def _rental_row(settings: Settings, rental_id: str) -> Any:
    [row] = query(
        settings,
        "SELECT status, charge_status, charge_attempts, charge_next_at FROM rentals WHERE id = :id",
        id=uuid.UUID(rental_id),
    )
    return row


def _queued(settings: Settings, rental_id: str) -> list[tuple[str, dict[str, Any]]]:
    rows = query(
        settings,
        "SELECT event, params FROM notification_outbox WHERE rental_id = :id ORDER BY created_at",
        id=uuid.UUID(rental_id),
    )
    return [(row.event, row.params) for row in rows]


def _make_default(person: Person, last4: str) -> None:
    cards = person.client.get("/payment-cards", headers=person.headers).json()["items"]
    [card] = [card for card in cards if card["last4"] == last4]
    response = person.client.post(f"/payment-cards/{card['id']}/default", headers=person.headers)
    assert response.status_code == 200, response.text


def _run[T](settings: Settings, job: Callable[[AsyncSession], Awaitable[T]]) -> T:
    async def run() -> T:
        engine = create_engine(settings)
        try:
            async with create_session_factory(engine)() as db:
                return await job(db)
        finally:
            await engine.dispose()

    return asyncio.run(run())


def _retry_charges(settings: Settings, at: datetime) -> int:
    gateway = payments.gateway(settings)
    rules = service.settlement_rules(settings)
    return _run(settings, lambda db: service.retry_charges(db, gateway, rules, at))


# Reservation and the hold


def test_a_rental_needs_a_card(integration_settings: Settings, app_client: TestClient) -> None:
    client = Person(integration_settings, app_client, card=None)

    response = client.reserve(add_priced_car(integration_settings, CAR_POINT))

    assert response.status_code == 409
    assert problem_type(response) == "card-required"


def test_the_first_opening_holds_the_tariffs_amount(
    integration_settings: Settings, person: Person, car: str
) -> None:
    rental_id = _start(person, car)

    [hold] = _operations(integration_settings, rental_id)
    assert (hold.kind, hold.amount, hold.status) == ("hold", DEMO_HOLD, "succeeded")
    # Opening again during the trip holds nothing more.
    assert person.command(rental_id, "open").status_code == 200
    assert len(_operations(integration_settings, rental_id)) == 1


def test_a_declined_hold_keeps_the_car_closed(
    integration_settings: Settings, app_client: TestClient, gateway: FakeGateway, car: str
) -> None:
    client = Person(integration_settings, app_client, card=DECLINED_CARD)
    rental_id = str(client.reserve(car).json()["id"])
    client.inspect(rental_id)

    response = client.command(rental_id, "open")

    assert response.status_code == 402
    assert problem_type(response) == "payment-hold-declined"
    assert gateway.calls == []
    assert client.get(rental_id).json()["status"] == "reserved"
    # Another card takes the next hold: the declined one stays final.
    client.bind_card(GOOD_CARD)
    _make_default(client, "4242")
    assert client.command(rental_id, "open").json()["status"] == "in_trip"
    keys = [row.idempotency_key for row in _operations(integration_settings, rental_id)]
    assert keys == [f"rental:{rental_id}:hold:1", f"rental:{rental_id}:hold:2"]


def test_a_cancel_in_the_paid_wait_charges_it(
    integration_settings: Settings, person: Person, car: str
) -> None:
    rental_id = str(person.reserve(car).json()["id"])
    _shift(integration_settings, rental_id, 20)

    response = person.cancel(rental_id)

    body = response.json()
    assert body["status"] == "cancelled"
    assert body["charge_status"] == "paid"
    [line] = body["receipt"]["cost"]["lines"]
    # 20 minutes waited, 15 free: 5 paid at the waiting price, a sixth begun while the test ran.
    assert (line["kind"], line["free_minutes"]) == ("waiting", 15)
    assert line["minutes"] in {5, 6}
    [charge] = _operations(integration_settings, rental_id)
    assert (charge.kind, charge.amount, charge.status) == ("charge", line["amount"], "succeeded")


def test_a_free_cancel_charges_nothing(
    integration_settings: Settings, person: Person, car: str
) -> None:
    rental_id = str(person.reserve(car).json()["id"])

    body = person.cancel(rental_id).json()

    assert (body["charge_status"], body["receipt"]) == (None, None)
    assert _operations(integration_settings, rental_id) == []


# The trip


def test_parking_switches_the_price_and_back(
    integration_settings: Settings, person: Person, car: str
) -> None:
    rental_id = _start(person, car)
    _shift(integration_settings, rental_id, 10)

    parked = _post(person, rental_id, "park")
    assert parked.json()["status"] == "parked"
    assert _post(person, rental_id, "park").json()["status"] == "parked"
    _shift(integration_settings, rental_id, 5)
    resumed = _post(person, rental_id, "resume").json()

    assert resumed["status"] == "in_trip"
    lines = {line["kind"]: line["minutes"] for line in resumed["cost"]["lines"]}
    # A started minute counts in full: the seconds the test runs may add one.
    assert lines["driving"] in {10, 11}
    assert lines["parked"] in {5, 6}
    assert resumed["cost"]["total"] == sum(line["amount"] for line in resumed["cost"]["lines"])
    kinds = query(
        integration_settings,
        "SELECT kind FROM rental_intervals WHERE rental_id = :id ORDER BY started_at",
        id=uuid.UUID(rental_id),
    )
    assert [row.kind for row in kinds] == ["driving", "parked", "driving"]


def test_parking_is_for_a_trip_only(person: Person, car: str) -> None:
    rental_id = str(person.reserve(car).json()["id"])

    response = _post(person, rental_id, "park")

    assert response.status_code == 409
    assert problem_type(response) == "rental-transition-not-allowed"


def test_the_trip_warns_about_low_charge(
    integration_settings: Settings, person: Person, car: str
) -> None:
    rental_id = _start(person, car)
    execute(
        integration_settings,
        "UPDATE cars SET battery_pct = 3 WHERE id = :car",
        car=uuid.UUID(car),
    )

    body = person.get(rental_id).json()

    assert body["warnings"] == ["low_battery", "low_range"]


def test_low_charge_is_pushed_once_per_trip(
    integration_settings: Settings, person: Person, car: str
) -> None:
    rental_id = _start(person, car)
    execute(
        integration_settings,
        "UPDATE cars SET battery_pct = 12 WHERE id = :car",
        car=uuid.UUID(car),
    )

    for _ in range(2):
        _run(integration_settings, lambda db: service.watch_trip_battery(db, 15, datetime.now(UTC)))

    low = [
        params
        for event, params in _queued(integration_settings, rental_id)
        if event == "trip_low_battery"
    ]
    assert low == [{"battery_pct": 12}]


# The finish


def test_a_trip_finishes_with_a_receipt_and_is_paid(
    integration_settings: Settings, person: Person, car: str
) -> None:
    rental_id = _start(person, car)
    _shift(integration_settings, rental_id, 30)
    _inspect_after(person, rental_id)

    response = _post(person, rental_id, "finish")

    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["status"], body["charge_status"]) == ("paid", "paid")
    receipt = body["receipt"]
    assert receipt["card_last4"] == "4242"
    assert [line["kind"] for line in receipt["cost"]["lines"]] == ["waiting", "driving"]
    assert body["cost"] == receipt["cost"]
    assert car_status(integration_settings, car) == "available"
    operations = _operations(integration_settings, rental_id)
    assert [(op.kind, op.amount) for op in operations] == [
        ("hold", DEMO_HOLD),
        ("capture", receipt["cost"]["total"]),
    ]
    assert ("trip_finished", {"total": receipt["cost"]["total"]}) in _queued(
        integration_settings, rental_id
    )


def test_a_repeated_finish_charges_nothing_more(
    integration_settings: Settings, person: Person, car: str
) -> None:
    rental_id = _start(person, car)
    _inspect_after(person, rental_id)
    first = _post(person, rental_id, "finish").json()

    again = _post(person, rental_id, "finish")

    assert again.status_code == 200
    assert again.json()["receipt"] == first["receipt"]
    assert len(_operations(integration_settings, rental_id)) == 2


def test_above_the_hold_the_rest_is_charged(
    integration_settings: Settings, person: Person, car: str
) -> None:
    rental_id = _start(person, car)
    _shift(integration_settings, rental_id, 120)
    _inspect_after(person, rental_id)

    body = _post(person, rental_id, "finish").json()

    total = body["receipt"]["cost"]["total"]
    assert total > DEMO_HOLD
    assert body["status"] == "paid"
    assert [(op.kind, op.amount) for op in _operations(integration_settings, rental_id)] == [
        ("hold", DEMO_HOLD),
        ("capture", DEMO_HOLD),
        ("charge", total - DEMO_HOLD),
    ]


def test_a_declined_charge_is_a_debt_until_paid(
    integration_settings: Settings, person: Person, car: str
) -> None:
    rental_id = _start(person, car)
    _shift(integration_settings, rental_id, 120)
    _inspect_after(person, rental_id)
    person.bind_card(DECLINED_CARD)
    _make_default(person, "9995")

    body = _post(person, rental_id, "finish").json()

    assert (body["status"], body["charge_status"]) == ("payment_failed", "failed")
    owed = body["receipt"]["cost"]["total"] - DEMO_HOLD
    debts = person.client.get("/rentals/debts", headers=person.headers).json()["debts"]
    assert [(debt["rental_id"], debt["amount"]) for debt in debts] == [(rental_id, owed)]
    assert ("payment_failed", {"amount": owed}) in _queued(integration_settings, rental_id)
    blocked = person.reserve(add_priced_car(integration_settings, CAR_POINT))
    assert problem_type(blocked) == "outstanding-debt"

    # The worker tries again when the time comes, with a key of its own each time.
    row = _rental_row(integration_settings, rental_id)
    assert row.charge_next_at is not None
    assert _retry_charges(integration_settings, row.charge_next_at) == 1
    assert _rental_row(integration_settings, rental_id).charge_attempts == 1
    _make_default(person, "4242")
    later = _rental_row(integration_settings, rental_id).charge_next_at
    assert _retry_charges(integration_settings, later) == 1

    paid = person.get(rental_id).json()
    assert (paid["status"], paid["charge_status"]) == ("paid", "paid")
    assert person.client.get("/rentals/debts", headers=person.headers).json()["debts"] == []
    keys = [row.idempotency_key for row in _operations(integration_settings, rental_id)]
    assert keys[-2:] == [f"rental:{rental_id}:debt:1", f"rental:{rental_id}:debt:2"]


def test_the_client_may_pay_a_debt_with_another_card(
    integration_settings: Settings, person: Person, car: str
) -> None:
    rental_id = _start(person, car)
    _shift(integration_settings, rental_id, 120)
    _inspect_after(person, rental_id)
    person.bind_card(DECLINED_CARD)
    _make_default(person, "9995")
    assert _post(person, rental_id, "finish").json()["charge_status"] == "failed"
    _make_default(person, "4242")

    body = _post(person, rental_id, "pay").json()

    assert body["status"] == "paid"
    # Paying a paid rental again does nothing.
    assert _post(person, rental_id, "pay").json()["status"] == "paid"
    assert len(_operations(integration_settings, rental_id)) == 4


@pytest.fixture
def flaky_capture(monkeypatch: pytest.MonkeyPatch) -> None:
    """The provider does not answer the first capture: its outcome is unknown."""
    original = FakeProvider.capture
    failures = [1]

    async def capture(self: FakeProvider, request: OperationRequest) -> OperationResult:
        if failures[0]:
            failures[0] -= 1
            raise PaymentProviderError("timeout")
        return await original(self, request)

    monkeypatch.setattr(FakeProvider, "capture", capture)


def test_an_unknown_outcome_is_retried_with_the_same_key(
    integration_settings: Settings, person: Person, car: str, flaky_capture: None
) -> None:
    rental_id = _start(person, car)
    _inspect_after(person, rental_id)

    body = _post(person, rental_id, "finish").json()

    assert (body["status"], body["charge_status"]) == ("finished", "pending")
    row = _rental_row(integration_settings, rental_id)
    assert _retry_charges(integration_settings, row.charge_next_at) == 1
    assert _rental_row(integration_settings, rental_id).status == "paid"
    captures = [op for op in _operations(integration_settings, rental_id) if op.kind == "capture"]
    assert [(op.idempotency_key, op.status) for op in captures] == [
        (f"rental:{rental_id}:capture", "succeeded")
    ]


def test_the_finish_needs_the_inspection_after(person: Person, car: str) -> None:
    rental_id = _start(person, car)

    response = _post(person, rental_id, "finish")

    assert problem_type(response) == "inspection-required"


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        (
            "UPDATE cars SET state_at = now() - interval '5 minutes' WHERE id = :car",
            "car-state-stale",
        ),
        (
            "UPDATE cars SET location = ST_SetSRID(ST_MakePoint(27.5615, 54.5), 4326)::geography "
            "WHERE id = :car",
            "outside-end-zone",
        ),
        ("UPDATE cars SET ignition_on = true WHERE id = :car", "ignition-on"),
    ],
)
def test_the_finish_is_refused_by_the_cars_telemetry(
    integration_settings: Settings, trip: Trip, gateway: FakeGateway, change: str, reason: str
) -> None:
    execute(integration_settings, change, car=uuid.UUID(trip.car))
    commands = len(gateway.calls)

    response = _post(trip.person, trip.rental_id, "finish")

    assert response.status_code == 409
    assert problem_type(response) == reason
    assert len(gateway.calls) == commands
    assert trip.person.get(trip.rental_id).json()["status"] == "in_trip"


@pytest.mark.parametrize(
    ("car_reason", "reason"), [("doors_open", "doors-open"), ("engine_fault", "car-lock-failed")]
)
def test_the_finish_is_refused_by_a_car_that_does_not_lock(
    integration_settings: Settings, trip: Trip, gateway: FakeGateway, car_reason: str, reason: str
) -> None:
    gateway.outcome = CarCommandRejectedError(car_reason)

    response = _post(trip.person, trip.rental_id, "finish")

    assert response.status_code == 409
    assert problem_type(response) == reason
    assert trip.person.get(trip.rental_id).json()["status"] == "in_trip"
    assert len(_operations(integration_settings, trip.rental_id)) == 1


def test_a_receipt_never_changes(integration_settings: Settings, person: Person, car: str) -> None:
    rental_id = _start(person, car)
    _inspect_after(person, rental_id)
    _post(person, rental_id, "finish")

    receipt = person.client.get(f"/rentals/{rental_id}/receipt", headers=person.headers)

    assert receipt.status_code == 200
    with pytest.raises(DBAPIError, match="append-only"):
        execute(
            integration_settings,
            "UPDATE rental_receipts SET total = 0 WHERE rental_id = :id",
            id=uuid.UUID(rental_id),
        )


def test_a_running_rental_has_no_receipt(person: Person, car: str) -> None:
    rental_id = _start(person, car)

    response = person.client.get(f"/rentals/{rental_id}/receipt", headers=person.headers)

    assert response.status_code == 404
    assert problem_type(response) == "receipt-not-found"


@pytest.mark.parametrize("action", ["park", "resume", "finish", "pay", "receipt"])
def test_another_clients_trip_is_not_found(
    integration_settings: Settings, app_client: TestClient, person: Person, car: str, action: str
) -> None:
    rental_id = _start(person, car)
    stranger = Person(integration_settings, app_client)

    if action == "receipt":
        response = stranger.client.get(f"/rentals/{rental_id}/receipt", headers=stranger.headers)
    else:
        response = _post(stranger, rental_id, action)

    assert response.status_code == 404
    assert problem_type(response) == "rental-not-found"
    assert person.get(rental_id).json()["status"] == "in_trip"


def test_the_trips_news_reaches_the_clients_phone(
    integration_settings: Settings, person: Person, car: str
) -> None:
    rental_id = _start(person, car)
    execute(
        integration_settings, "UPDATE cars SET battery_pct = 12 WHERE id = :car", car=uuid.UUID(car)
    )
    _run(integration_settings, lambda db: service.watch_trip_battery(db, 15, datetime.now(UTC)))
    _shift(integration_settings, rental_id, 120)
    _inspect_after(person, rental_id)
    person.bind_card(DECLINED_CARD)
    _make_default(person, "9995")
    assert _post(person, rental_id, "finish").json()["charge_status"] == "failed"
    token = push_token()

    async def deliver() -> FakePushProvider:
        engine = create_engine(integration_settings)
        try:
            factory = create_session_factory(engine)
            phone = await sign_in(factory, integration_settings, user_id=person.id)
            async with factory() as db:
                await notifications.register_device(
                    db,
                    user_id=person.id,
                    session_id=phone.session_id,
                    token=token,
                    platform=PushPlatform.IOS,
                    now=datetime.now(UTC),
                )
            provider = FakePushProvider()
            await notifications.dispatch_due(factory, provider, datetime.now(UTC))
            return provider
        finally:
            await engine.dispose()

    provider = asyncio.run(deliver())

    events = sorted(m.content.data["event"] for m in provider.sent if m.token == token)
    assert events == ["payment_failed", "trip_finished", "trip_low_battery"]
    assert {m.content.data["rental_id"] for m in provider.sent if m.token == token} == {rental_id}
