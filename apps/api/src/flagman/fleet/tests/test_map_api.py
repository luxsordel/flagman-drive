import asyncio
import json
import random
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from http import HTTPStatus
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from flagman.core.app import create_app
from flagman.core.config import Settings
from flagman.core.db import create_engine, create_session_factory
from flagman.core.geo import GeoPoint
from flagman.core.ratelimit import Limit
from flagman.fleet import api, repository
from flagman.fleet.domain import CarState, CarStatus
from flagman.fleet.models import Car as CarRow
from flagman.fleet.seed import DEMO_MODELS
from flagman.identity.domain import (
    AccessClaims,
    StaffAccessClaims,
    StaffRole,
    issue_access_token,
    issue_staff_access_token,
)

pytestmark = pytest.mark.integration

URL = "/map/cars"
# About 111 m per 0.001 degree of latitude.
STEP = 0.001
_PLATE_LETTERS = "ABEIKMHOPCTX"
_VIN_CHARS = "ABCDEFGHJKLMNPRSTUVWXYZ0123456789"
MAP_FIELDS = {
    "id",
    "brand",
    "model",
    "photo_url",
    "lat",
    "lon",
    "battery_pct",
    "range_km",
    "distance_m",
}


@pytest.fixture
def client(integration_settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(integration_settings), base_url="https://testserver") as client:
        yield client


def _client_headers(settings: Settings) -> dict[str, str]:
    secret = settings.api_jwt_secret.get_secret_value()
    token = issue_access_token(AccessClaims(uuid.uuid4(), uuid.uuid4()), secret, datetime.now(UTC))
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def headers(integration_settings: Settings) -> dict[str, str]:
    return _client_headers(integration_settings)


@pytest.fixture
def spot() -> GeoPoint:
    """A random place in Minsk per test: rows stay in the shared database between tests."""
    rng = random.SystemRandom()
    return GeoPoint(
        lat=round(rng.uniform(53.80, 54.00), 4), lon=round(rng.uniform(27.40, 27.70), 4)
    )


def _area(center: GeoPoint, half: float = 5 * STEP) -> dict[str, float]:
    return {
        "min_lat": center.lat - half,
        "min_lon": center.lon - half,
        "max_lat": center.lat + half,
        "max_lon": center.lon + half,
    }


def _add_car(  # noqa: PLR0913
    settings: Settings,
    point: GeoPoint | None,
    *,
    status: CarStatus = CarStatus.AVAILABLE,
    battery_pct: int = 80,
    age: timedelta = timedelta(seconds=5),
    model_index: int = 0,
) -> str:
    async def add() -> uuid.UUID:
        rng = random.SystemRandom()
        engine = create_engine(settings)
        now = datetime.now(UTC)
        try:
            async with create_session_factory(engine)() as db, db.begin():
                model_id, _ = await repository.ensure_model(db, DEMO_MODELS[model_index].spec, now)
                telematics_id = uuid.uuid4()
                car = repository.NewCar(
                    model_id=model_id,
                    plate=f"{rng.randrange(10_000):04d} {''.join(rng.choices(_PLATE_LETTERS, k=2))}"
                    f"-{rng.randint(1, 6)}",
                    vin="".join(rng.choices(_VIN_CHARS, k=17)),
                    telematics_id=telematics_id,
                    status=status,
                )
                assert await repository.add_car_if_absent(db, car, now)
                if point is not None:
                    state = CarState(point, battery_pct, True, False, now - age)
                    await repository.apply_state(db, telematics_id, state, now)
                car_id = await db.scalar(
                    select(CarRow.id).where(CarRow.telematics_id == telematics_id)
                )
                assert car_id is not None
                return car_id
        finally:
            await engine.dispose()

    return str(asyncio.run(add()))


def _ids(response: Any) -> list[str]:
    assert response.status_code == HTTPStatus.OK, response.text
    return [car["id"] for car in response.json()["items"]]


def test_cars_in_the_area_come_with_only_the_map_fields(
    client: TestClient, headers: dict[str, str], integration_settings: Settings, spot: GeoPoint
) -> None:
    car_id = _add_car(integration_settings, spot, battery_pct=50)

    response = client.get(URL, params=_area(spot), headers=headers)

    assert _ids(response) == [car_id]
    car = response.json()["items"][0]
    assert set(car) == MAP_FIELDS
    spec = DEMO_MODELS[0].spec
    assert (car["brand"], car["model"], car["photo_url"]) == (spec.brand, spec.name, spec.photo_url)
    assert (car["lat"], car["lon"]) == pytest.approx((spot.lat, spot.lon))
    assert car["battery_pct"] == 50
    assert car["range_km"] == spec.range_km // 2
    assert car["distance_m"] is None


def test_cars_outside_the_area_are_not_shown(
    client: TestClient, headers: dict[str, str], integration_settings: Settings, spot: GeoPoint
) -> None:
    inside = _add_car(integration_settings, spot)
    _add_car(integration_settings, GeoPoint(spot.lat + 10 * STEP, spot.lon))

    assert _ids(client.get(URL, params=_area(spot), headers=headers)) == [inside]


@pytest.mark.parametrize(
    "unavailable",
    [
        {"status": CarStatus.RESERVED},
        {"status": CarStatus.SERVICE},
        {"battery_pct": 19},
        {"age": timedelta(minutes=10)},
    ],
    ids=["reserved", "in-service", "low-battery", "stale-telemetry"],
)
def test_only_rentable_cars_are_shown(
    client: TestClient,
    headers: dict[str, str],
    integration_settings: Settings,
    spot: GeoPoint,
    unavailable: dict[str, Any],
) -> None:
    rentable = _add_car(integration_settings, spot)
    hidden = _add_car(integration_settings, spot, **unavailable)

    assert _ids(client.get(URL, params=_area(spot), headers=headers)) == [rentable]
    assert client.get(f"{URL}/{hidden}", headers=headers).status_code == HTTPStatus.NOT_FOUND


def test_a_car_without_telemetry_is_not_shown(
    client: TestClient, headers: dict[str, str], integration_settings: Settings
) -> None:
    car_id = _add_car(integration_settings, None)

    response = client.get(f"{URL}/{car_id}", headers=headers)

    assert response.status_code == HTTPStatus.NOT_FOUND
    assert response.json()["type"] == "urn:flagman:problem:car-not-found"


def test_the_limit_counts_only_rentable_cars(
    client: TestClient, headers: dict[str, str], integration_settings: Settings, spot: GeoPoint
) -> None:
    for _ in range(3):
        _add_car(integration_settings, spot, status=CarStatus.SERVICE)
    rentable = {_add_car(integration_settings, spot) for _ in range(3)}

    shown = _ids(client.get(URL, params={**_area(spot), "limit": 2}, headers=headers))

    assert len(shown) == 2
    assert set(shown) <= rentable


def test_with_the_clients_point_cars_come_nearest_first(
    client: TestClient, headers: dict[str, str], integration_settings: Settings, spot: GeoPoint
) -> None:
    far = _add_car(integration_settings, GeoPoint(spot.lat + 3 * STEP, spot.lon))
    near = _add_car(integration_settings, GeoPoint(spot.lat + STEP, spot.lon))

    response = client.get(
        URL, params={**_area(spot), "lat": spot.lat, "lon": spot.lon}, headers=headers
    )

    assert _ids(response) == [near, far]
    distances = [car["distance_m"] for car in response.json()["items"]]
    assert distances == [pytest.approx(111, abs=2), pytest.approx(334, abs=3)]


def test_the_clients_point_alone_finds_the_nearest_cars(
    client: TestClient, headers: dict[str, str], integration_settings: Settings, spot: GeoPoint
) -> None:
    near = _add_car(integration_settings, GeoPoint(spot.lat, spot.lon + STEP))
    nearest = _add_car(integration_settings, spot)

    response = client.get(
        URL, params={"lat": spot.lat, "lon": spot.lon, "limit": 2}, headers=headers
    )

    assert _ids(response) == [nearest, near]
    assert response.json()["items"][0]["distance_m"] == 0


def test_the_card_shows_the_same_fields_and_the_distance(
    client: TestClient, headers: dict[str, str], integration_settings: Settings, spot: GeoPoint
) -> None:
    car_id = _add_car(integration_settings, GeoPoint(spot.lat + 2 * STEP, spot.lon))
    listed = client.get(
        URL, params={**_area(spot), "lat": spot.lat, "lon": spot.lon}, headers=headers
    )

    card = client.get(f"{URL}/{car_id}", params={"lat": spot.lat, "lon": spot.lon}, headers=headers)

    assert card.status_code == HTTPStatus.OK
    assert card.json() == listed.json()["items"][0]
    assert client.get(f"{URL}/{car_id}", headers=headers).json()["distance_m"] is None


def test_an_unknown_car_is_404(client: TestClient, headers: dict[str, str]) -> None:
    response = client.get(f"{URL}/{uuid.uuid4()}", headers=headers)

    assert response.status_code == HTTPStatus.NOT_FOUND


@pytest.mark.parametrize(
    "params",
    [
        {},
        {"min_lat": 53.8, "min_lon": 27.4, "max_lat": 54.0},
        {"lat": 53.9},
        {"min_lat": 54.0, "min_lon": 27.4, "max_lat": 53.8, "max_lon": 27.7},
        {"lat": 91, "lon": 27.5},
        {"lat": 53.9, "lon": 27.5, "limit": 201},
        {"lat": 53.9, "lon": 27.5, "limit": 0},
        {"lat": 53.9, "lon": 27.5, "radius": 100},
    ],
    ids=[
        "nothing",
        "three-edges",
        "lat-without-lon",
        "reversed-area",
        "lat-out-of-range",
        "limit-too-big",
        "limit-zero",
        "unknown-param",
    ],
)
def test_a_wrong_query_is_422(
    client: TestClient, headers: dict[str, str], params: dict[str, Any]
) -> None:
    response = client.get(URL, params=params, headers=headers)

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY, response.text


def test_the_card_needs_both_coordinates(client: TestClient, headers: dict[str, str]) -> None:
    response = client.get(f"{URL}/{uuid.uuid4()}", params={"lon": 27.5}, headers=headers)

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY


@pytest.mark.parametrize("path", [URL, f"{URL}/{uuid.uuid4()}"])
def test_the_map_is_only_for_signed_in_clients(
    client: TestClient, integration_settings: Settings, path: str
) -> None:
    secret = integration_settings.api_jwt_secret.get_secret_value()
    staff = issue_staff_access_token(
        StaffAccessClaims(uuid.uuid4(), uuid.uuid4(), StaffRole.ADMIN), secret, datetime.now(UTC)
    )
    params = {"lat": 53.9, "lon": 27.5}

    anonymous = client.get(path, params=params)
    as_staff = client.get(path, params=params, headers={"Authorization": f"Bearer {staff}"})

    assert anonymous.status_code == HTTPStatus.UNAUTHORIZED
    assert as_staff.status_code == HTTPStatus.UNAUTHORIZED


def test_map_requests_are_rate_limited(
    client: TestClient, headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(api, "MAP_LIMIT", Limit(f"map-cars-test-{uuid.uuid4()}", 2, 300))
    params = {"lat": 53.9, "lon": 27.5}

    assert client.get(URL, params=params, headers=headers).status_code == HTTPStatus.OK
    assert client.get(f"{URL}/{uuid.uuid4()}", headers=headers).status_code == HTTPStatus.NOT_FOUND
    limited = client.get(URL, params=params, headers=headers)

    assert limited.status_code == HTTPStatus.TOO_MANY_REQUESTS
    assert "Retry-After" in limited.headers


def test_the_clients_point_is_not_logged(
    integration_settings: Settings, spot: GeoPoint, capsys: pytest.CaptureFixture[str]
) -> None:
    _add_car(integration_settings, spot)
    # The app is created inside the test so the log handler writes to the captured stdout.
    with TestClient(create_app(integration_settings), base_url="https://testserver") as client:
        headers = _client_headers(integration_settings)
        point = {"lat": 53.912345, "lon": 27.554321}
        client.get(URL, params={**_area(spot), **point}, headers=headers)
        client.get(URL, params={"lat": 91, "lon": 27.554321}, headers=headers)
        client.get(f"{URL}/{uuid.uuid4()}", params=point, headers=headers)

    output = capsys.readouterr()
    assert "53.912345" not in output.err
    records = [json.loads(line) for line in output.out.splitlines() if line.startswith("{")]
    # The test client logs the URLs it calls; only what the API itself logs counts.
    api_records = [r for r in records if not r.get("logger", "").startswith("httpx")]
    assert any(record.get("event") == "request" for record in api_records)
    logged = json.dumps(api_records)
    assert "53.912345" not in logged
    assert "27.554321" not in logged
