import asyncio
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from http import HTTPStatus

import pytest
from fastapi.testclient import TestClient

from flagman.core.config import Settings
from flagman.core.db import create_engine, create_session_factory
from flagman.fleet.domain import CarStatus
from flagman.fleet.tests.conftest import add_car
from flagman.kyc.domain import KycStatus
from flagman.pricing.seed import DEMO_PRICES
from flagman.rentals import service
from flagman.rentals.tests.conftest import (
    CAR_POINT,
    Person,
    car_status,
    execute,
    problem_type,
    query,
)

pytestmark = pytest.mark.integration


@pytest.fixture
def person(integration_settings: Settings, app_client: TestClient) -> Person:
    return Person(integration_settings, app_client)


@pytest.fixture
def car(integration_settings: Settings) -> str:
    return add_car(integration_settings, CAR_POINT)


def test_a_reservation_holds_the_car_and_starts_the_free_wait(
    integration_settings: Settings, person: Person, car: str
) -> None:
    before = datetime.now(UTC)

    response = person.reserve(car)

    assert response.status_code == HTTPStatus.CREATED
    rental = response.json()
    assert rental["status"] == "reserved"
    assert rental["car"]["id"] == car
    # The plate is shown once the car is the client's: it is how they find it.
    assert rental["car"]["plate"]
    assert rental["inspection_submitted"] is False
    reserved_at = datetime.fromisoformat(rental["reserved_at"])
    assert reserved_at >= before
    assert datetime.fromisoformat(rental["expires_at"]) - reserved_at == timedelta(minutes=45)
    assert car_status(integration_settings, car) == CarStatus.RESERVED
    # The demo tariff's free minutes and prices, and its version fixed for the whole rental.
    assert rental["tariff"] == {
        "driving_per_minute": DEMO_PRICES.driving_per_minute,
        "parked_per_minute": DEMO_PRICES.parked_per_minute,
        "waiting_per_minute": DEMO_PRICES.waiting_per_minute,
    }
    assert datetime.fromisoformat(rental["free_wait_until"]) - reserved_at == timedelta(
        minutes=DEMO_PRICES.free_waiting_minutes
    )
    rows = query(
        integration_settings,
        "SELECT t.model_id, c.model_id AS car_model FROM rentals r "
        "JOIN tariff_versions v ON v.id = r.tariff_version_id JOIN tariffs t ON t.id = v.tariff_id "
        "JOIN cars c ON c.id = r.car_id "
        "WHERE r.id = :id",
        id=uuid.UUID(rental["id"]),
    )
    assert rows[0].model_id == rows[0].car_model


def test_a_tariff_changed_later_does_not_touch_the_reservation(
    integration_settings: Settings, person: Person, car: str
) -> None:
    rental = person.reserve(car).json()
    before = query(
        integration_settings,
        "SELECT tariff_version_id FROM rentals WHERE id = :id",
        id=uuid.UUID(rental["id"]),
    )[0].tariff_version_id
    execute(
        integration_settings,
        "INSERT INTO tariff_versions (id, tariff_id, number, driving_per_minute, "
        "parked_per_minute, waiting_per_minute, free_waiting_minutes, hold_amount, valid_from) "
        "SELECT gen_random_uuid(), tariff_id, number + 1, 99, 99, 99, 0, 9900, now() "
        "FROM tariff_versions WHERE id = :id",
        id=before,
    )

    after = query(
        integration_settings,
        "SELECT tariff_version_id FROM rentals WHERE id = :id",
        id=uuid.UUID(rental["id"]),
    )[0].tariff_version_id
    assert after == before
    assert person.get(rental["id"]).json()["free_wait_until"] == rental["free_wait_until"]


def test_a_car_of_a_model_without_a_tariff_is_not_found(
    integration_settings: Settings, person: Person
) -> None:
    car = add_car(integration_settings, CAR_POINT)
    execute(
        integration_settings,
        "UPDATE cars SET model_id = :model WHERE id = :id",
        model=_model_without_tariff(integration_settings),
        id=uuid.UUID(car),
    )

    response = person.reserve(car)

    assert response.status_code == HTTPStatus.NOT_FOUND
    assert problem_type(response) == "car-not-found"
    assert car_status(integration_settings, car) == CarStatus.AVAILABLE


def _model_without_tariff(settings: Settings) -> uuid.UUID:
    model_id = uuid.uuid4()
    execute(
        settings,
        "INSERT INTO car_models (id, brand, name, seats, battery_wh, range_km, created_at) "
        "VALUES (:id, 'Test', :name, 5, 50000, 300, now())",
        id=model_id,
        name=f"No tariff {model_id.hex[:8]}",
    )
    return model_id


def test_the_reserved_car_leaves_the_map(person: Person, car: str) -> None:
    person.reserve(car)

    response = person.client.get(f"/map/cars/{car}", headers=person.headers)

    assert response.status_code == HTTPStatus.NOT_FOUND


@pytest.mark.parametrize("kyc", [None, KycStatus.NOT_SUBMITTED, KycStatus.PENDING])
def test_only_approved_clients_reserve(
    integration_settings: Settings,
    app_client: TestClient,
    car: str,
    kyc: KycStatus | None,
) -> None:
    client = Person(integration_settings, app_client, kyc=kyc)

    response = client.reserve(car)

    assert response.status_code == HTTPStatus.FORBIDDEN
    assert problem_type(response) == "kyc-not-approved"
    assert car_status(integration_settings, car) == CarStatus.AVAILABLE


def test_a_blocked_client_does_not_reserve(
    integration_settings: Settings, person: Person, car: str
) -> None:
    person.block()

    response = person.reserve(car)

    assert response.status_code == HTTPStatus.FORBIDDEN
    assert problem_type(response) == "client-blocked"
    assert car_status(integration_settings, car) == CarStatus.AVAILABLE


def test_one_active_rental_per_client(
    integration_settings: Settings, person: Person, car: str
) -> None:
    other = add_car(integration_settings, CAR_POINT)
    person.reserve(car)

    response = person.reserve(other)

    assert response.status_code == HTTPStatus.CONFLICT
    assert problem_type(response) == "active-rental-exists"
    assert car_status(integration_settings, other) == CarStatus.AVAILABLE


@pytest.mark.parametrize(
    "setup",
    [
        pytest.param({"status": CarStatus.SERVICE}, id="in-service"),
        pytest.param({"battery_pct": 5}, id="low-battery"),
        pytest.param({"age": timedelta(hours=1)}, id="stale-telemetry"),
        pytest.param({"ignition_on": True}, id="being-driven"),
    ],
)
def test_a_car_that_is_not_rentable_is_not_found(
    integration_settings: Settings, person: Person, setup: dict[str, object]
) -> None:
    car = add_car(integration_settings, CAR_POINT, **setup)  # type: ignore[arg-type]

    response = person.reserve(car)

    assert response.status_code == HTTPStatus.NOT_FOUND
    assert problem_type(response) == "car-not-found"


def test_an_unknown_car_is_not_found(person: Person) -> None:
    response = person.reserve(str(uuid.uuid4()))

    assert response.status_code == HTTPStatus.NOT_FOUND
    assert problem_type(response) == "car-not-found"


def test_the_body_is_checked(person: Person) -> None:
    response = person.client.post(
        "/rentals", json={"car_id": str(uuid.uuid4()), "price": 1}, headers=person.headers
    )

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY


def test_reservations_need_a_client(app_client: TestClient, car: str) -> None:
    assert app_client.post("/rentals", json={"car_id": car}).status_code == HTTPStatus.UNAUTHORIZED


def _parallel(app_client: TestClient, requests: list[tuple[dict[str, str], str]]) -> list[int]:
    # Each thread's request runs on the test client's event loop at the same time as the others.
    barrier = threading.Barrier(len(requests))

    def reserve(headers: dict[str, str], car: str) -> int:
        barrier.wait()
        return app_client.post("/rentals", json={"car_id": car}, headers=headers).status_code

    with ThreadPoolExecutor(max_workers=len(requests)) as pool:
        return sorted(pool.map(lambda request: reserve(*request), requests))


def test_two_clients_racing_for_one_car_get_it_once(
    integration_settings: Settings, app_client: TestClient, car: str
) -> None:
    clients = [Person(integration_settings, app_client) for _ in range(5)]

    codes = _parallel(app_client, [(c.headers, car) for c in clients])

    assert codes == [HTTPStatus.CREATED] + [HTTPStatus.NOT_FOUND] * 4
    rows = query(
        integration_settings,
        "SELECT count(*) AS n FROM rentals WHERE car_id = :c",
        c=uuid.UUID(car),
    )
    assert rows[0].n == 1


def test_one_client_racing_for_two_cars_gets_one(
    integration_settings: Settings, person: Person
) -> None:
    cars = [add_car(integration_settings, CAR_POINT) for _ in range(4)]

    codes = _parallel(person.client, [(person.headers, car) for car in cars])

    assert codes == [HTTPStatus.CREATED] + [HTTPStatus.CONFLICT] * 3
    statuses = sorted(car_status(integration_settings, car) for car in cars)
    assert statuses == [CarStatus.AVAILABLE] * 3 + [CarStatus.RESERVED]


def test_cancelling_frees_the_car(integration_settings: Settings, person: Person, car: str) -> None:
    rental = person.reserve(car).json()

    response = person.cancel(rental["id"])

    assert response.status_code == HTTPStatus.OK
    assert response.json()["status"] == "cancelled"
    assert response.json()["cancel_reason"] == "client"
    assert response.json()["ended_at"] is not None
    assert car_status(integration_settings, car) == CarStatus.AVAILABLE
    assert person.current().json() == {"rental": None}


def test_cancelling_again_answers_the_same(person: Person, car: str) -> None:
    rental = person.reserve(car).json()
    first = person.cancel(rental["id"]).json()

    second = person.cancel(rental["id"])

    assert second.status_code == HTTPStatus.OK
    assert second.json()["ended_at"] == first["ended_at"]


def test_a_cancel_waits_for_a_command_in_flight(
    integration_settings: Settings, person: Person, car: str
) -> None:
    rental = person.reserve(car).json()
    execute(
        integration_settings,
        "UPDATE rentals SET command_until = now() + interval '10 seconds' WHERE id = :id",
        id=uuid.UUID(rental["id"]),
    )

    response = person.cancel(rental["id"])

    assert response.status_code == HTTPStatus.CONFLICT
    assert problem_type(response) == "rental-command-in-progress"


def test_the_client_comes_back_to_the_active_rental(person: Person, car: str) -> None:
    assert person.current().json() == {"rental": None}
    rental = person.reserve(car).json()

    current = person.current().json()["rental"]

    assert current["id"] == rental["id"]
    assert datetime.fromisoformat(current["as_of"]) <= datetime.now(UTC)


def test_another_clients_rental_is_not_found(
    integration_settings: Settings, app_client: TestClient, person: Person, car: str
) -> None:
    rental = person.reserve(car).json()
    stranger = Person(integration_settings, app_client)

    for response in (
        stranger.get(rental["id"]),
        stranger.cancel(rental["id"]),
        stranger.client.get(
            f"/rentals/{rental['id']}/inspections/before", headers=stranger.headers
        ),
        stranger.upload(rental["id"], b"not even a photo"),
        stranger.damage(rental["id"], "left", "scratch"),
        stranger.submit(rental["id"]),
        stranger.command(rental["id"], "open"),
    ):
        assert response.status_code == HTTPStatus.NOT_FOUND, response.json()
        assert problem_type(response) == "rental-not-found"
    assert stranger.current().json() == {"rental": None}
    assert person.get(rental["id"]).json()["status"] == "reserved"


def _expire_now(settings: Settings, rental_id: str) -> None:
    execute(
        settings,
        "UPDATE rentals SET reserved_at = now() - interval '46 minutes', "
        "free_wait_until = now() - interval '31 minutes', expires_at = now() - interval '1 second' "
        "WHERE id = :id",
        id=uuid.UUID(rental_id),
    )


def test_a_reservation_past_its_end_is_cancelled_when_asked_for(
    integration_settings: Settings, person: Person, car: str
) -> None:
    rental = person.reserve(car).json()
    _expire_now(integration_settings, rental["id"])

    response = person.get(rental["id"])

    assert response.json()["status"] == "cancelled"
    assert response.json()["cancel_reason"] == "expired"
    assert car_status(integration_settings, car) == CarStatus.AVAILABLE
    assert person.current().json() == {"rental": None}


def test_an_expired_reservation_refuses_changes(
    integration_settings: Settings, person: Person, car: str
) -> None:
    rental = person.reserve(car).json()
    _expire_now(integration_settings, rental["id"])

    for response in (
        person.damage(rental["id"], "left", "scratch"),
        person.submit(rental["id"]),
        person.command(rental["id"], "open"),
    ):
        assert response.status_code == HTTPStatus.CONFLICT
        assert problem_type(response) == "reservation-expired"


def test_a_reservation_past_its_end_does_not_hold_the_car(
    integration_settings: Settings, app_client: TestClient, person: Person, car: str
) -> None:
    rental = person.reserve(car).json()
    _expire_now(integration_settings, rental["id"])
    other = Person(integration_settings, app_client)

    assert other.reserve(car).status_code == HTTPStatus.CREATED
    assert person.get(rental["id"]).json()["cancel_reason"] == "expired"
    # And the client is free to reserve again.
    assert person.reserve(add_car(integration_settings, CAR_POINT)).status_code == 201


def _run_expiry(settings: Settings) -> int:
    async def run() -> int:
        engine = create_engine(settings)
        try:
            async with create_session_factory(engine)() as db:
                return await service.expire_reservations(db, datetime.now(UTC))
        finally:
            await engine.dispose()

    return asyncio.run(run())


def test_the_timeout_job_cancels_reservations_past_their_end(
    integration_settings: Settings, app_client: TestClient, car: str
) -> None:
    people = [Person(integration_settings, app_client) for _ in range(3)]
    cars = [car, add_car(integration_settings, CAR_POINT), add_car(integration_settings, CAR_POINT)]
    rentals = [p.reserve(c).json() for p, c in zip(people, cars, strict=True)]
    _expire_now(integration_settings, rentals[0]["id"])
    _expire_now(integration_settings, rentals[1]["id"])
    # A command waits for the car of this one: it is left until the command ends.
    execute(
        integration_settings,
        "UPDATE rentals SET command_until = now() + interval '10 seconds' WHERE id = :id",
        id=uuid.UUID(rentals[1]["id"]),
    )

    _run_expiry(integration_settings)

    statuses = [
        query(
            integration_settings, "SELECT status FROM rentals WHERE id = :id", id=uuid.UUID(r["id"])
        )[0].status
        for r in rentals
    ]
    assert statuses == ["cancelled", "reserved", "reserved"]
    assert [car_status(integration_settings, c) for c in cars] == [
        CarStatus.AVAILABLE,
        CarStatus.RESERVED,
        CarStatus.RESERVED,
    ]
