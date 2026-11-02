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
from flagman.payments.domain import DeclineReason, OperationStatus
from flagman.payments.fake_provider import FakeProvider
from flagman.payments.ports import OperationRequest, OperationResult, PaymentProviderError
from flagman.pricing.seed import DEMO_PRICES
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
    # The steps of one settlement share its `now`, so a tie is put in the order it takes them.
    return query(
        settings,
        "SELECT idempotency_key, kind, amount, status FROM payment_operations "
        "WHERE idempotency_key LIKE :prefix ORDER BY created_at, "
        "array_position(ARRAY['hold', 'capture', 'void', 'charge'], kind)",
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


_CHARGE_RUNS = 20


def _retry_charges(settings: Settings, at: datetime) -> None:
    """Runs the charge job until nothing is due at `at`: other tests' rentals may be due as well
    and fill a batch before this test's one, so the tests check their own rental after it."""
    gateway = payments.gateway(settings)
    rules = service.settlement_rules(settings)
    # Every rental it tries is put off past `at`, so the runs end; a bug that keeps one due
    # must fail the test, not hang it.
    for _ in range(_CHARGE_RUNS):
        if not _run(settings, lambda db: service.retry_charges(db, gateway, rules, at)):
            return
    pytest.fail(f"the charge job still finds rentals due after {_CHARGE_RUNS} runs")


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


def _hold_and_fail_to_open(person: Person, gateway: FakeGateway, rental_id: str) -> None:
    """The money is held, then the car refuses to open: the reservation goes on with the hold."""
    person.inspect(rental_id)
    gateway.outcome = CarCommandRejectedError("engine_fault")
    assert person.command(rental_id, "open").status_code != 200
    assert person.get(rental_id).json()["status"] == "reserved"


def _kinds(settings: Settings, rental_id: str) -> list[tuple[str, str]]:
    return [(op.kind, op.status) for op in _operations(settings, rental_id)]


def test_a_cancel_after_a_failed_opening_releases_the_hold(
    integration_settings: Settings, person: Person, gateway: FakeGateway, car: str
) -> None:
    rental_id = str(person.reserve(car).json()["id"])
    _hold_and_fail_to_open(person, gateway, rental_id)

    body = person.cancel(rental_id).json()

    assert (body["status"], body["charge_status"], body["receipt"]) == ("cancelled", "paid", None)
    assert _kinds(integration_settings, rental_id) == [
        ("hold", "succeeded"),
        ("void", "succeeded"),
    ]


def _expire_reservations(settings: Settings) -> int:
    return _run(settings, lambda db: service.expire_reservations(db, datetime.now(UTC)))


def _paid_wait(settings: Settings) -> int:
    """Minutes of a whole reservation past the free wait of the demo tariff (`add_priced_car`)."""
    return settings.rental_max_reservation_minutes - DEMO_PRICES.free_waiting_minutes


def _free_wait(settings: Settings, car: str) -> None:
    """A new version of the tariff of the car's own model (`add_priced_car`) where the whole
    reservation is free; a version a rental took is never changed (docs/adr/0018)."""
    execute(
        settings,
        "INSERT INTO tariff_versions (id, tariff_id, number, driving_per_minute, "
        "parked_per_minute, waiting_per_minute, free_waiting_minutes, hold_amount, valid_from) "
        "SELECT gen_random_uuid(), v.tariff_id, v.number + 1, v.driving_per_minute, "
        "v.parked_per_minute, v.waiting_per_minute, :minutes, v.hold_amount, now() "
        "FROM tariff_versions v JOIN tariffs t ON t.id = v.tariff_id "
        "JOIN cars c ON c.model_id = t.model_id WHERE c.id = :car",
        car=uuid.UUID(car),
        minutes=settings.rental_max_reservation_minutes,
    )


def test_the_timeout_job_charges_the_paid_wait_until_the_end_of_the_reservation(
    integration_settings: Settings, person: Person, car: str
) -> None:
    rental_id = str(person.reserve(car).json()["id"])
    # The reservation ended 15 minutes before the job got to it.
    _shift(integration_settings, rental_id, 60)

    _expire_reservations(integration_settings)

    # Checked before any request to the rental: a request would expire it by itself.
    row = _rental_row(integration_settings, rental_id)
    assert (row.status, row.charge_status) == ("cancelled", "pending")
    # The lateness of the job is not billed.
    total = _paid_wait(integration_settings) * DEMO_PRICES.waiting_per_minute
    assert _queued(integration_settings, rental_id) == [("reservation_expired", {"amount": total})]
    body = person.get(rental_id).json()
    assert body["cancel_reason"] == "expired"
    [line] = body["receipt"]["cost"]["lines"]
    assert (line["kind"], line["minutes"], line["free_minutes"]) == (
        "waiting",
        _paid_wait(integration_settings),
        DEMO_PRICES.free_waiting_minutes,
    )
    assert body["receipt"]["cost"]["total"] == line["amount"] == total

    _retry_charges(integration_settings, datetime.now(UTC))

    assert _rental_row(integration_settings, rental_id).charge_status == "paid"
    [charge] = _operations(integration_settings, rental_id)
    assert (charge.kind, charge.amount, charge.status) == ("charge", total, "succeeded")


def test_a_reservation_expired_on_access_charges_the_paid_wait(
    integration_settings: Settings, person: Person, car: str
) -> None:
    rental_id = str(person.reserve(car).json()["id"])
    _shift(integration_settings, rental_id, 60)

    body = person.get(rental_id).json()

    assert body["status"] == "cancelled"
    assert body["charge_status"] == "pending"
    [line] = body["receipt"]["cost"]["lines"]
    assert line["minutes"] == _paid_wait(integration_settings)
    _retry_charges(integration_settings, datetime.now(UTC))
    assert person.get(rental_id).json()["charge_status"] == "paid"


def test_an_expired_reservation_takes_the_paid_wait_from_the_hold(
    integration_settings: Settings, person: Person, gateway: FakeGateway, car: str
) -> None:
    rental_id = str(person.reserve(car).json()["id"])
    _hold_and_fail_to_open(person, gateway, rental_id)
    _shift(integration_settings, rental_id, 60)

    total = person.get(rental_id).json()["receipt"]["cost"]["total"]
    _retry_charges(integration_settings, datetime.now(UTC))

    assert _rental_row(integration_settings, rental_id).charge_status == "paid"
    operations = _operations(integration_settings, rental_id)
    assert [(op.kind, op.amount) for op in operations] == [("hold", DEMO_HOLD), ("capture", total)]


def test_a_declined_charge_of_an_expired_reservation_is_a_debt(
    integration_settings: Settings, person: Person, car: str
) -> None:
    rental_id = str(person.reserve(car).json()["id"])
    person.bind_card(DECLINED_CARD)
    _make_default(person, "9995")
    _shift(integration_settings, rental_id, 60)
    assert person.get(rental_id).json()["status"] == "cancelled"

    _retry_charges(integration_settings, datetime.now(UTC))

    body = person.get(rental_id).json()
    assert (body["status"], body["charge_status"]) == ("cancelled", "failed")
    total = body["receipt"]["cost"]["total"]
    debts = person.client.get("/rentals/debts", headers=person.headers).json()["debts"]
    assert [(debt["rental_id"], debt["amount"]) for debt in debts] == [(rental_id, total)]
    assert _queued(integration_settings, rental_id) == [
        ("reservation_expired", {"amount": total}),
        ("payment_failed", {"amount": total}),
    ]
    blocked = person.reserve(add_priced_car(integration_settings, CAR_POINT))
    assert problem_type(blocked) == "outstanding-debt"
    _make_default(person, "4242")
    assert _post(person, rental_id, "pay").json()["charge_status"] == "paid"


def test_a_free_expiry_charges_nothing(
    integration_settings: Settings, person: Person, car: str
) -> None:
    _free_wait(integration_settings, car)
    rental_id = str(person.reserve(car).json()["id"])
    _shift(integration_settings, rental_id, 60)

    body = person.get(rental_id).json()

    assert (body["status"], body["charge_status"], body["receipt"]) == ("cancelled", None, None)
    assert _queued(integration_settings, rental_id) == [("reservation_expired", {"amount": 0})]
    assert _operations(integration_settings, rental_id) == []


def test_a_free_expiry_releases_the_hold_of_a_failed_opening(
    integration_settings: Settings, person: Person, gateway: FakeGateway, car: str
) -> None:
    _free_wait(integration_settings, car)
    rental_id = str(person.reserve(car).json()["id"])
    _hold_and_fail_to_open(person, gateway, rental_id)
    _shift(integration_settings, rental_id, 60)

    body = person.get(rental_id).json()
    assert (body["status"], body["receipt"]) == ("cancelled", None)
    assert _rental_row(integration_settings, rental_id).charge_status == "pending"
    _retry_charges(integration_settings, datetime.now(UTC))

    assert _rental_row(integration_settings, rental_id).charge_status == "paid"
    assert _kinds(integration_settings, rental_id) == [
        ("hold", "succeeded"),
        ("void", "succeeded"),
    ]


def test_a_cancel_in_the_paid_wait_takes_it_from_the_hold(
    integration_settings: Settings, person: Person, gateway: FakeGateway, car: str
) -> None:
    rental_id = str(person.reserve(car).json()["id"])
    _shift(integration_settings, rental_id, 20)
    _hold_and_fail_to_open(person, gateway, rental_id)

    body = person.cancel(rental_id).json()

    assert body["charge_status"] == "paid"
    total = body["receipt"]["cost"]["total"]
    assert total > 0
    operations = _operations(integration_settings, rental_id)
    assert [(op.kind, op.amount) for op in operations] == [("hold", DEMO_HOLD), ("capture", total)]


def test_a_declined_release_of_the_hold_is_no_debt(
    integration_settings: Settings,
    person: Person,
    gateway: FakeGateway,
    car: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def void(self: FakeProvider, request: OperationRequest) -> OperationResult:
        return OperationResult(OperationStatus.DECLINED, decline_reason=DeclineReason.DECLINED)

    monkeypatch.setattr(FakeProvider, "void", void)
    rental_id = str(person.reserve(car).json()["id"])
    _hold_and_fail_to_open(person, gateway, rental_id)

    body = person.cancel(rental_id).json()

    assert body["charge_status"] == "paid"
    assert ("payment_failed", {"amount": 0}) not in _queued(integration_settings, rental_id)
    assert person.client.get("/rentals/debts", headers=person.headers).json()["debts"] == []


@pytest.fixture
def lost_hold(monkeypatch: pytest.MonkeyPatch) -> None:
    """The provider holds the money but its answer to the first hold is lost."""
    original = FakeProvider.hold
    failures = [1]

    async def hold(self: FakeProvider, request: OperationRequest) -> OperationResult:
        result = await original(self, request)
        if failures[0]:
            failures[0] -= 1
            raise PaymentProviderError("timeout")
        return result

    monkeypatch.setattr(FakeProvider, "hold", hold)


def test_a_cancel_after_an_unknown_hold_releases_it(
    integration_settings: Settings, person: Person, car: str, lost_hold: None
) -> None:
    rental_id = str(person.reserve(car).json()["id"])
    person.inspect(rental_id)
    response = person.command(rental_id, "open")
    assert problem_type(response) == "payment-outcome-unknown"

    body = person.cancel(rental_id).json()

    assert (body["status"], body["charge_status"]) == ("cancelled", "paid")
    # The same key asked again finds the hold the provider made, and it is released.
    operations = _operations(integration_settings, rental_id)
    assert [(op.idempotency_key, op.kind, op.status) for op in operations] == [
        (f"rental:{rental_id}:hold:1", "hold", "succeeded"),
        (f"rental:{rental_id}:void", "void", "succeeded"),
    ]


def test_an_unknown_hold_is_asked_again_by_the_next_opening(
    integration_settings: Settings, person: Person, car: str, lost_hold: None
) -> None:
    rental_id = str(person.reserve(car).json()["id"])
    person.inspect(rental_id)
    assert problem_type(person.command(rental_id, "open")) == "payment-outcome-unknown"

    assert person.command(rental_id, "open").json()["status"] == "in_trip"

    assert _kinds(integration_settings, rental_id) == [("hold", "succeeded")]


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


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        (
            "UPDATE cars SET state_at = now() - interval '5 minutes' WHERE id = :car",
            "car-state-stale",
        ),
        ("UPDATE cars SET ignition_on = true WHERE id = :car", "ignition-on"),
    ],
)
def test_a_car_on_the_move_does_not_get_the_parked_price(
    integration_settings: Settings, person: Person, car: str, change: str, reason: str
) -> None:
    rental_id = _start(person, car)
    execute(integration_settings, change, car=uuid.UUID(car))

    response = _post(person, rental_id, "park")

    assert response.status_code == 409
    assert problem_type(response) == reason
    assert person.get(rental_id).json()["status"] == "in_trip"
    kinds = query(
        integration_settings,
        "SELECT kind FROM rental_intervals WHERE rental_id = :id",
        id=uuid.UUID(rental_id),
    )
    assert [row.kind for row in kinds] == ["driving"]


def test_a_parked_car_that_drives_off_is_back_on_the_driving_price(
    integration_settings: Settings, person: Person, car: str
) -> None:
    rental_id = _start(person, car)
    assert _post(person, rental_id, "park").json()["status"] == "parked"
    standing = _run(
        integration_settings, lambda db: service.resume_driven_off(db, datetime.now(UTC))
    )
    assert person.get(rental_id).json()["status"] == "parked"

    execute(
        integration_settings,
        "UPDATE cars SET ignition_on = true WHERE id = :car",
        car=uuid.UUID(car),
    )
    resumed = _run(
        integration_settings, lambda db: service.resume_driven_off(db, datetime.now(UTC))
    )

    assert (standing, resumed) == (0, 1)
    assert person.get(rental_id).json()["status"] == "in_trip"
    kinds = query(
        integration_settings,
        "SELECT kind FROM rental_intervals WHERE rental_id = :id ORDER BY started_at",
        id=uuid.UUID(rental_id),
    )
    assert [row.kind for row in kinds] == ["driving", "parked", "driving"]


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


def test_a_repeated_finish_leaves_a_debt_to_the_debt_payments(
    integration_settings: Settings, person: Person, car: str
) -> None:
    rental_id = _start(person, car)
    _shift(integration_settings, rental_id, 120)
    _inspect_after(person, rental_id)
    person.bind_card(DECLINED_CARD)
    _make_default(person, "9995")
    assert _post(person, rental_id, "finish").json()["status"] == "payment_failed"
    operations = len(_operations(integration_settings, rental_id))

    again = _post(person, rental_id, "finish")

    assert again.json()["status"] == "payment_failed"
    assert len(_operations(integration_settings, rental_id)) == operations
    assert _rental_row(integration_settings, rental_id).charge_attempts == 0
    # Paid here, so the charge job of the other tests finds no debt of this one.
    _make_default(person, "4242")
    assert _post(person, rental_id, "pay").json()["status"] == "paid"


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
    _retry_charges(integration_settings, row.charge_next_at)
    assert _rental_row(integration_settings, rental_id).charge_attempts == 1
    _make_default(person, "4242")
    later = _rental_row(integration_settings, rental_id).charge_next_at
    _retry_charges(integration_settings, later)

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
    _retry_charges(integration_settings, row.charge_next_at)
    assert _rental_row(integration_settings, rental_id).status == "paid"
    captures = [op for op in _operations(integration_settings, rental_id) if op.kind == "capture"]
    assert [(op.idempotency_key, op.status) for op in captures] == [
        (f"rental:{rental_id}:capture", "succeeded")
    ]


@pytest.fixture
def declined_capture(monkeypatch: pytest.MonkeyPatch) -> None:
    async def capture(self: FakeProvider, request: OperationRequest) -> OperationResult:
        return OperationResult(OperationStatus.DECLINED, decline_reason=DeclineReason.DECLINED)

    monkeypatch.setattr(FakeProvider, "capture", capture)


def test_a_declined_capture_releases_the_hold_before_the_debt(
    integration_settings: Settings, person: Person, car: str, declined_capture: None
) -> None:
    rental_id = _start(person, car)
    _shift(integration_settings, rental_id, 10)
    _inspect_after(person, rental_id)

    body = _post(person, rental_id, "finish").json()

    assert (body["status"], body["charge_status"]) == ("payment_failed", "failed")
    assert _kinds(integration_settings, rental_id) == [
        ("hold", "succeeded"),
        ("capture", "declined"),
        ("void", "succeeded"),
    ]
    debts = person.client.get("/rentals/debts", headers=person.headers).json()["debts"]
    assert [debt["amount"] for debt in debts] == [body["receipt"]["cost"]["total"]]


def test_a_rental_the_charge_job_fails_on_waits_like_a_debt(
    integration_settings: Settings,
    person: Person,
    car: str,
    flaky_capture: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rental_id = _start(person, car)
    _inspect_after(person, rental_id)
    assert _post(person, rental_id, "finish").json()["charge_status"] == "pending"
    at = _rental_row(integration_settings, rental_id).charge_next_at

    async def broken(*_: object) -> None:
        raise RuntimeError("a bug")

    monkeypatch.setattr(service, "settle", broken)
    _retry_charges(integration_settings, at)

    row = _rental_row(integration_settings, rental_id)
    assert row.charge_status == "pending"
    retry = service.settlement_rules(integration_settings).retry.interval
    assert row.charge_next_at == at + retry


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
        ("UPDATE cars SET gear = 'D' WHERE id = :car", "not-in-park"),
        ("UPDATE cars SET gear = 'N' WHERE id = :car", "not-in-park"),
        ("UPDATE cars SET gear = 'R' WHERE id = :car", "not-in-park"),
        ("UPDATE cars SET ignition_on = true WHERE id = :car", "ignition-on"),
        ("UPDATE cars SET headlights_on = true WHERE id = :car", "headlights-on"),
        # A snapshot from before the car told these: unknown is not "off" or "P".
        ("UPDATE cars SET gear = NULL, headlights_on = NULL WHERE id = :car", "not-in-park"),
        ("UPDATE cars SET headlights_on = NULL WHERE id = :car", "headlights-on"),
    ],
    ids=[
        "stale",
        "outside-zone",
        "in-drive",
        "in-neutral",
        "in-reverse",
        "ignition-on",
        "headlights-on",
        "gear-unknown",
        "headlights-unknown",
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
