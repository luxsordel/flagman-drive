import asyncio
import json
import random
import uuid
from collections.abc import Awaitable, Callable, Iterator
from datetime import UTC, datetime, timedelta
from http import HTTPStatus
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from conftest import SettingsFactory
from flagman.core.app import create_app
from flagman.core.config import Settings
from flagman.core.db import create_engine, create_session_factory
from flagman.core.geo import GeoPoint
from flagman.core.signing import SIGNATURE_HEADER, TIMESTAMP_HEADER, sign
from flagman.fleet import repository, service
from flagman.fleet.domain import CarState, CarStatus, Gear
from flagman.fleet.schemas import MAX_TELEMETRY_BODY_BYTES, MAX_TELEMETRY_CARS
from flagman.fleet.seed import DEMO_MODELS

pytestmark = pytest.mark.integration

SECRET = "telemetry-secret-" + "t" * 32
PATH = "/fleet/telemetry"
CREATED_AT = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)
_PLATE_LETTERS = "ABEIKMHOPCTX"
_VIN_CHARS = "ABCDEFGHJKLMNPRSTUVWXYZ0123456789"


@pytest.fixture
def settings(
    integration_env: dict[str, str], migrated_database: None, make_settings: SettingsFactory
) -> Settings:
    return make_settings(
        **{name.lower(): value for name, value in integration_env.items()},
        emulator_telemetry_secret=SECRET,
    )


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings), base_url="https://testserver") as client:
        yield client


def _run[T](settings: Settings, action: Callable[[AsyncSession], Awaitable[T]]) -> T:
    async def run() -> T:
        engine = create_engine(settings)
        try:
            async with create_session_factory(engine)() as db:
                return await action(db)
        finally:
            await engine.dispose()

    return asyncio.run(run())


def add_car(settings: Settings) -> uuid.UUID:
    """A car of its own per test, returns its telematics id: rows stay between tests."""
    rng = random.SystemRandom()
    telematics_id = uuid.uuid4()

    async def add(db: AsyncSession) -> None:
        async with db.begin():
            model_id, _ = await repository.ensure_model(db, DEMO_MODELS[0].spec, CREATED_AT)
            car = repository.NewCar(
                model_id=model_id,
                # Region 7 is left to the demo seed, so the plates never collide with it.
                plate=f"{rng.randrange(10_000):04d} {''.join(rng.choices(_PLATE_LETTERS, k=2))}"
                f"-{rng.randint(1, 6)}",
                vin="".join(rng.choices(_VIN_CHARS, k=17)),
                telematics_id=telematics_id,
                status=CarStatus.AVAILABLE,
            )
            assert await repository.add_car_if_absent(db, car, CREATED_AT)

    _run(settings, add)
    return telematics_id


def state_of(settings: Settings, telematics_id: uuid.UUID) -> CarState | None:
    async def find(db: AsyncSession) -> CarState | None:
        cars = await service.list_cars(db)
        return next(car.state for car in cars if car.telematics_id == telematics_id)

    return _run(settings, find)


def snapshot(
    telematics_id: uuid.UUID, at: datetime, battery_pct: int = 80, lat: float = 53.902284
) -> dict[str, Any]:
    return {
        "id": str(telematics_id),
        "lat": lat,
        "lon": 27.561831,
        "battery_pct": battery_pct,
        "doors_locked": True,
        "ignition_on": False,
        "engine_locked": False,
        "headlights_on": False,
        "gear": "P",
        "updated_at": at.isoformat(),
    }


def batch(*cars: dict[str, Any]) -> bytes:
    return json.dumps({"sent_at": datetime.now(UTC).isoformat(), "cars": list(cars)}).encode()


def signed_headers(
    body: bytes, target: str = PATH, *, secret: str = SECRET, skew: timedelta = timedelta(0)
) -> dict[str, str]:
    timestamp = int((datetime.now(UTC) + skew).timestamp())
    return {
        "Content-Type": "application/json",
        TIMESTAMP_HEADER: str(timestamp),
        SIGNATURE_HEADER: sign(secret.encode(), timestamp, "POST", target.encode(), body),
    }


def post(client: TestClient, body: bytes, path: str = PATH, **signing: Any) -> Any:
    """Signed for `path` unless `target` says otherwise."""
    signing.setdefault("target", path)
    return client.post(path, content=body, headers=signed_headers(body, **signing))


def counts(applied: int = 0, stale: int = 0, unknown_car: int = 0, from_future: int = 0) -> Any:
    return {
        "applied": applied,
        "stale": stale,
        "unknown_car": unknown_car,
        "from_future": from_future,
    }


def problem_type(response: Any) -> str:
    return str(response.json()["type"]).removeprefix("urn:flagman:problem:")


def test_signed_batch_updates_the_car(client: TestClient, settings: Settings) -> None:
    telematics_id = add_car(settings)
    at = datetime.now(UTC).replace(microsecond=0) - timedelta(seconds=1)

    response = post(client, batch(snapshot(telematics_id, at)))

    assert response.status_code == HTTPStatus.OK, response.text
    assert response.json() == counts(applied=1)
    assert state_of(settings, telematics_id) == CarState(
        location=GeoPoint(lat=53.902284, lon=27.561831),
        battery_pct=80,
        doors_locked=True,
        ignition_on=False,
        engine_locked=False,
        recorded_at=at,
        headlights_on=False,
        gear=Gear.PARK,
    )


def test_a_snapshot_without_headlights_and_gear_keeps_them_unknown(
    client: TestClient, settings: Settings
) -> None:
    telematics_id = add_car(settings)
    at = datetime.now(UTC) - timedelta(seconds=1)
    told = post(client, batch(snapshot(telematics_id, at)))
    # An emulator older than #144: the next snapshot must not keep the last known values.
    old = snapshot(telematics_id, at + timedelta(milliseconds=500))
    del old["headlights_on"], old["gear"]

    response = post(client, batch(old))

    assert (told.status_code, response.status_code) == (HTTPStatus.OK, HTTPStatus.OK)
    state = state_of(settings, telematics_id)
    assert state is not None
    assert (state.headlights_on, state.gear) == (None, None)


def test_query_string_is_part_of_the_signed_target(client: TestClient, settings: Settings) -> None:
    telematics_id = add_car(settings)
    body = batch(snapshot(telematics_id, datetime.now(UTC)))

    signed = post(client, body, path=f"{PATH}?source=emulator")
    moved = post(client, body, path=f"{PATH}?source=other", target=f"{PATH}?source=emulator")

    assert signed.status_code == HTTPStatus.OK, signed.text
    assert moved.status_code == HTTPStatus.UNAUTHORIZED


@pytest.mark.parametrize(
    "kwargs",
    [
        {"secret": "wrong-secret-" + "w" * 32},
        # A few seconds past the window, which test_signing checks to the second.
        {"skew": timedelta(seconds=-305)},
        {"skew": timedelta(seconds=305)},
        {"target": "/fleet/other"},
    ],
    ids=["wrong-secret", "expired", "from-future", "other-path"],
)
def test_bad_signature_changes_nothing(
    client: TestClient, settings: Settings, kwargs: dict[str, Any]
) -> None:
    telematics_id = add_car(settings)

    response = post(client, batch(snapshot(telematics_id, datetime.now(UTC))), **kwargs)

    assert response.status_code == HTTPStatus.UNAUTHORIZED
    assert problem_type(response) == "invalid-signature"
    # The reason stays in the server log.
    assert "detail" not in response.json()
    assert state_of(settings, telematics_id) is None


def test_missing_signature_is_refused(client: TestClient, settings: Settings) -> None:
    telematics_id = add_car(settings)
    body = batch(snapshot(telematics_id, datetime.now(UTC)))

    response = client.post(PATH, content=body, headers={"Content-Type": "application/json"})

    assert response.status_code == HTTPStatus.UNAUTHORIZED
    assert state_of(settings, telematics_id) is None


def test_repeated_batch_is_ignored(client: TestClient, settings: Settings) -> None:
    telematics_id = add_car(settings)
    body = batch(snapshot(telematics_id, datetime.now(UTC)))
    post(client, body)

    response = post(client, body)

    assert response.json() == counts(stale=1)


def test_older_snapshot_after_a_newer_one_is_ignored(
    client: TestClient, settings: Settings
) -> None:
    telematics_id = add_car(settings)
    newer = datetime.now(UTC).replace(microsecond=0)
    post(client, batch(snapshot(telematics_id, newer, battery_pct=70)))

    late = post(client, batch(snapshot(telematics_id, newer - timedelta(seconds=5), 90, 53.85)))

    assert late.json() == counts(stale=1)
    state = state_of(settings, telematics_id)
    assert state is not None
    assert (state.recorded_at, state.battery_pct, state.location.lat) == (newer, 70, 53.902284)


def test_unknown_and_future_cars_do_not_hold_back_the_rest(
    client: TestClient, settings: Settings
) -> None:
    known = add_car(settings)
    ahead = add_car(settings)
    now = datetime.now(UTC)

    response = post(
        client,
        batch(
            snapshot(uuid.uuid4(), now),
            snapshot(ahead, now + timedelta(minutes=10)),
            snapshot(known, now),
        ),
    )

    assert response.json() == counts(applied=1, unknown_car=1, from_future=1)
    assert state_of(settings, known) is not None
    assert state_of(settings, ahead) is None


def test_invalid_body_is_refused_after_the_signature(client: TestClient) -> None:
    response = post(client, batch(snapshot(uuid.uuid4(), datetime.now(UTC), battery_pct=101)))

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert response.json()["errors"][0]["loc"][:3] == ["body", "cars", 0]


def test_too_many_cars_are_refused(client: TestClient) -> None:
    car = snapshot(uuid.uuid4(), datetime.now(UTC))

    response = post(client, batch(*[car] * (MAX_TELEMETRY_CARS + 1)))

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY


@pytest.mark.parametrize("chunked", [False, True])
def test_too_large_body_is_refused(client: TestClient, chunked: bool) -> None:
    body = b"x" * (MAX_TELEMETRY_BODY_BYTES + 1)
    # A generator has no length up front, so the limit has to hold while reading too.
    content: Any = iter([body]) if chunked else body

    response = client.post(PATH, content=content, headers={"Content-Type": "application/json"})

    assert response.status_code == HTTPStatus.REQUEST_ENTITY_TOO_LARGE
    assert problem_type(response) == "telemetry-too-large"


def test_without_a_secret_telemetry_is_unavailable(
    integration_env: dict[str, str], migrated_database: None, make_settings: SettingsFactory
) -> None:
    settings = make_settings(**{name.lower(): value for name, value in integration_env.items()})
    with TestClient(create_app(settings), base_url="https://testserver") as client:
        response = post(client, batch())

    assert response.status_code == HTTPStatus.SERVICE_UNAVAILABLE
    assert problem_type(response) == "telemetry-unavailable"


def test_endpoint_is_not_in_the_public_schema(client: TestClient) -> None:
    assert PATH not in client.get("/openapi.json").json()["paths"]
