import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from flagman.core.config import Settings
from flagman.core.db import create_engine, create_session_factory
from flagman.fleet.tests.conftest import add_car
from flagman.rentals import service
from flagman.rentals.tests.conftest import CAR_POINT, Person, execute, query

pytestmark = pytest.mark.integration

REMIND_AHEAD = timedelta(minutes=5)


@pytest.fixture
def person(integration_settings: Settings, app_client: TestClient) -> Person:
    return Person(integration_settings, app_client)


@pytest.fixture
def car(integration_settings: Settings) -> str:
    return add_car(integration_settings, CAR_POINT)


def _ends_in(settings: Settings, rental_id: str, seconds: int) -> None:
    # The whole reservation moves, so its deadlines stay in order (a check of the table).
    execute(
        settings,
        "UPDATE rentals SET reserved_at = now() + make_interval(secs => :seconds) - interval "
        "'45 minutes', free_wait_until = now() + make_interval(secs => :seconds) - interval "
        "'15 minutes', expires_at = now() + make_interval(secs => :seconds) WHERE id = :id",
        seconds=seconds,
        id=uuid.UUID(rental_id),
    )


def _queued(settings: Settings, rental_id: str) -> list[Any]:
    return query(
        settings,
        "SELECT user_id, event, params, status, expires_at FROM notification_outbox "
        "WHERE rental_id = :id ORDER BY created_at",
        id=uuid.UUID(rental_id),
    )


def _run(settings: Settings, job: str) -> int:
    async def run() -> int:
        engine = create_engine(settings)
        try:
            async with create_session_factory(engine)() as db:
                now = datetime.now(UTC)
                if job == "expire":
                    return await service.expire_reservations(db, now)
                return await service.remind_expiring_reservations(db, now, REMIND_AHEAD)
        finally:
            await engine.dispose()

    return asyncio.run(run())


def test_a_reservation_ending_soon_is_reminded_once(
    integration_settings: Settings, person: Person, car: str
) -> None:
    rental = person.reserve(car).json()
    _ends_in(integration_settings, rental["id"], 150)

    _run(integration_settings, "remind")
    _run(integration_settings, "remind")

    [row] = _queued(integration_settings, rental["id"])
    assert (row.user_id, row.event) == (person.id, "reservation_expiring")
    # 2.5 minutes left: the client is told 3, never less than is left.
    assert row.params == {"minutes_left": 3}
    # But it is not sent after the reservation is over, rounding or not.
    [ends] = query(
        integration_settings,
        "SELECT expires_at FROM rentals WHERE id = :id",
        id=uuid.UUID(rental["id"]),
    )
    assert row.expires_at == ends.expires_at


def test_a_reservation_far_from_its_end_is_not_reminded(
    integration_settings: Settings, person: Person, car: str
) -> None:
    rental = person.reserve(car).json()

    _run(integration_settings, "remind")

    assert _queued(integration_settings, rental["id"]) == []


def test_the_timeout_job_tells_the_client_the_reservation_expired(
    integration_settings: Settings, person: Person, car: str
) -> None:
    rental = person.reserve(car).json()
    _ends_in(integration_settings, rental["id"], -1)

    _run(integration_settings, "expire")

    [row] = _queued(integration_settings, rental["id"])
    assert (row.user_id, row.event) == (person.id, "reservation_expired")
    # The paid half of the 45 minutes, the same amount as in the receipt.
    receipt = person.get(rental["id"]).json()["receipt"]
    assert row.params == {"amount": receipt["cost"]["total"]}


def test_a_reservation_expired_on_access_is_told_once(
    integration_settings: Settings, person: Person, car: str
) -> None:
    rental = person.reserve(car).json()
    _ends_in(integration_settings, rental["id"], -1)

    person.get(rental["id"])
    person.get(rental["id"])
    _run(integration_settings, "expire")

    assert [row.event for row in _queued(integration_settings, rental["id"])] == [
        "reservation_expired"
    ]


def test_a_reservation_cancelled_by_the_client_is_not_told_it_expired(
    integration_settings: Settings, person: Person, car: str
) -> None:
    rental = person.reserve(car).json()

    person.cancel(rental["id"])
    _ends_in(integration_settings, rental["id"], -1)
    _run(integration_settings, "expire")

    assert _queued(integration_settings, rental["id"]) == []


def test_a_reminder_not_sent_yet_is_withdrawn_when_the_client_cancels(
    integration_settings: Settings, person: Person, car: str
) -> None:
    rental = person.reserve(car).json()
    _ends_in(integration_settings, rental["id"], 150)
    _run(integration_settings, "remind")

    person.cancel(rental["id"])

    [row] = _queued(integration_settings, rental["id"])
    assert (row.event, row.status) == ("reservation_expiring", "undelivered")


def test_a_reminder_not_sent_yet_is_withdrawn_when_the_trip_starts(
    integration_settings: Settings, person: Person, car: str
) -> None:
    rental = person.reserve(car).json()
    person.inspect(rental["id"])
    _ends_in(integration_settings, rental["id"], 150)
    _run(integration_settings, "remind")

    assert person.command(rental["id"], "open").json()["status"] == "in_trip"

    [row] = _queued(integration_settings, rental["id"])
    assert (row.event, row.status) == ("reservation_expiring", "undelivered")


def test_an_expired_reservation_withdraws_its_reminder(
    integration_settings: Settings, person: Person, car: str
) -> None:
    rental = person.reserve(car).json()
    _ends_in(integration_settings, rental["id"], 150)
    _run(integration_settings, "remind")
    _ends_in(integration_settings, rental["id"], -1)

    _run(integration_settings, "expire")

    rows = _queued(integration_settings, rental["id"])
    assert [(row.event, row.status) for row in rows] == [
        ("reservation_expiring", "undelivered"),
        ("reservation_expired", "pending"),
    ]
