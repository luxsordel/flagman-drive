"""How many pool checkouts opening and finishing a rental take.

They are the slowest requests under load and every checkout is a pre-ping plus a transaction, so
a change that adds one must show up here (docs/09-load-test.md).
"""

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from conftest import database_round_trips
from flagman.core.config import Settings
from flagman.fleet.tests.conftest import run_in_db
from flagman.geo import service as geo
from flagman.rentals.tests.conftest import CAR_POINT, Person, add_priced_car, photo

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def demo_zone(integration_settings: Settings) -> None:
    run_in_db(integration_settings, lambda db: geo.seed_demo_zone(db, datetime.now(UTC)))


@pytest.fixture
def person(integration_settings: Settings, app_client: TestClient) -> Person:
    return Person(integration_settings, app_client)


@pytest.fixture
def inspected(integration_settings: Settings, person: Person) -> str:
    response = person.reserve(add_priced_car(integration_settings, CAR_POINT))
    assert response.status_code == 201, response.text
    rental_id = str(response.json()["id"])
    person.inspect(rental_id)
    return rental_id


def _inspect_after(person: Person, rental_id: str) -> None:
    for index in range(4):
        assert person.upload(rental_id, photo(index), stage="after").status_code == 200
    assert person.submit(rental_id, stage="after").status_code == 200


def _checkouts(person: Person, action: str, rental_id: str) -> int:
    with database_round_trips(person.client) as counts:
        if action == "finish":
            response = person.client.post(f"/rentals/{rental_id}/finish", headers=person.headers)
        else:
            response = person.command(rental_id, action)
        assert response.status_code == 200, response.text
    # A transaction is never opened without its own checkout: the session keeps no connection
    # between them.
    assert counts["begin"] == counts["checkout"]
    return counts["checkout"]


def test_the_first_opening(person: Person, inspected: str) -> None:
    # Before the hold, the hold and its outcome, the hold on the rental, the command's record and
    # the trip's start.
    assert _checkouts(person, "open", inspected) == 6


def test_a_command_in_the_trip(person: Person, inspected: str) -> None:
    assert person.command(inspected, "open").status_code == 200

    # The checks, the command's record and the rental after it.
    assert _checkouts(person, "close", inspected) == 3


def test_the_finish(person: Person, inspected: str) -> None:
    assert person.command(inspected, "open").status_code == 200
    _inspect_after(person, inspected)

    # The checks, the command's record, the end of the trip, the capture and its outcome, and the
    # charge recorded on the rental.
    assert _checkouts(person, "finish", inspected) == 6
