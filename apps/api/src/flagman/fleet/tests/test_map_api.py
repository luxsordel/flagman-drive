import asyncio
import itertools
import json
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from http import HTTPStatus
from typing import Any

import pytest
from fastapi.testclient import TestClient

from flagman.core.app import create_app
from flagman.core.config import Settings
from flagman.core.db import create_engine, create_session_factory
from flagman.core.geo import GeoPoint
from flagman.core.ratelimit import Limit
from flagman.fleet import api, repository
from flagman.fleet.domain import CarModelSpec, CarStatus
from flagman.fleet.seed import DEMO_MODELS
from flagman.fleet.tests.conftest import add_car
from flagman.identity.domain import (
    AccessClaims,
    StaffAccessClaims,
    StaffRole,
    issue_access_token,
    issue_staff_access_token,
)
from flagman.pricing import service as pricing
from flagman.pricing.seed import DEMO_PRICES

pytestmark = pytest.mark.integration

URL = "/map/cars"
# About 111 m per 0.001 degree of latitude.
STEP = 0.001
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
    "tariff",
}


@pytest.fixture(autouse=True)
def demo_tariffs(integration_settings: Settings) -> None:
    """The map offers only cars of models with a tariff: the demo models get the demo one.

    Seeding again changes nothing, so every test may run it.
    """
    settings = integration_settings

    async def seed() -> None:
        engine = create_engine(settings)
        try:
            async with create_session_factory(engine)() as db:
                async with db.begin():
                    for demo in DEMO_MODELS:
                        await repository.ensure_model(db, demo.spec, datetime.now(UTC))
                await pricing.seed_demo_tariffs(db, datetime.now(UTC))
        finally:
            await engine.dispose()

    asyncio.run(seed())


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


# Rows stay in the shared database between tests and their cars stay rentable for minutes, so each
# test gets a cell of its own: 0.1 degree apart, far wider than any area or distance a test checks,
# and far from Minsk, where the other modules' tests put their cars.
_cells = itertools.count()
CELL_ORIGIN = GeoPoint(lat=-40.0, lon=170.0)
CELL_SIZE = 0.1


@pytest.fixture
def spot() -> GeoPoint:
    return GeoPoint(lat=CELL_ORIGIN.lat + next(_cells) * CELL_SIZE, lon=CELL_ORIGIN.lon)


def _area(center: GeoPoint, half: float = 5 * STEP) -> dict[str, float]:
    return {
        "min_lat": center.lat - half,
        "min_lon": center.lon - half,
        "max_lat": center.lat + half,
        "max_lon": center.lon + half,
    }


def _ids(response: Any) -> list[str]:
    assert response.status_code == HTTPStatus.OK, response.text
    return [car["id"] for car in response.json()["items"]]


def test_cars_in_the_area_come_with_only_the_map_fields(
    client: TestClient, headers: dict[str, str], integration_settings: Settings, spot: GeoPoint
) -> None:
    car_id = add_car(integration_settings, spot, battery_pct=50)

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
    inside = add_car(integration_settings, spot)
    add_car(integration_settings, GeoPoint(spot.lat + 10 * STEP, spot.lon))

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
    rentable = add_car(integration_settings, spot)
    hidden = add_car(integration_settings, spot, **unavailable)

    assert _ids(client.get(URL, params=_area(spot), headers=headers)) == [rentable]
    assert client.get(f"{URL}/{hidden}", headers=headers).status_code == HTTPStatus.NOT_FOUND


def test_a_car_without_telemetry_is_not_shown(
    client: TestClient, headers: dict[str, str], integration_settings: Settings
) -> None:
    car_id = add_car(integration_settings, None)

    response = client.get(f"{URL}/{car_id}", headers=headers)

    assert response.status_code == HTTPStatus.NOT_FOUND
    assert response.json()["type"] == "urn:flagman:problem:car-not-found"


def test_the_limit_counts_only_rentable_cars(
    client: TestClient, headers: dict[str, str], integration_settings: Settings, spot: GeoPoint
) -> None:
    for _ in range(3):
        add_car(integration_settings, spot, status=CarStatus.SERVICE)
    rentable = {add_car(integration_settings, spot) for _ in range(3)}

    shown = _ids(client.get(URL, params={**_area(spot), "limit": 2}, headers=headers))

    assert len(shown) == 2
    assert set(shown) <= rentable


def test_with_the_clients_point_cars_come_nearest_first(
    client: TestClient, headers: dict[str, str], integration_settings: Settings, spot: GeoPoint
) -> None:
    far = add_car(integration_settings, GeoPoint(spot.lat + 3 * STEP, spot.lon))
    near = add_car(integration_settings, GeoPoint(spot.lat + STEP, spot.lon))

    response = client.get(
        URL, params={**_area(spot), "lat": spot.lat, "lon": spot.lon}, headers=headers
    )

    assert _ids(response) == [near, far]
    distances = [car["distance_m"] for car in response.json()["items"]]
    assert distances == [pytest.approx(111, abs=2), pytest.approx(334, abs=3)]


def test_the_clients_point_alone_finds_the_nearest_cars(
    client: TestClient, headers: dict[str, str], integration_settings: Settings, spot: GeoPoint
) -> None:
    near = add_car(integration_settings, GeoPoint(spot.lat, spot.lon + STEP))
    nearest = add_car(integration_settings, spot)

    response = client.get(
        URL, params={"lat": spot.lat, "lon": spot.lon, "limit": 2}, headers=headers
    )

    assert _ids(response) == [nearest, near]
    assert response.json()["items"][0]["distance_m"] == 0


def test_the_card_shows_the_same_fields_and_the_distance(
    client: TestClient, headers: dict[str, str], integration_settings: Settings, spot: GeoPoint
) -> None:
    car_id = add_car(integration_settings, GeoPoint(spot.lat + 2 * STEP, spot.lon))
    listed = client.get(
        URL, params={**_area(spot), "lat": spot.lat, "lon": spot.lon}, headers=headers
    )

    card = client.get(f"{URL}/{car_id}", params={"lat": spot.lat, "lon": spot.lon}, headers=headers)

    assert card.status_code == HTTPStatus.OK
    assert card.json() == listed.json()["items"][0]
    assert client.get(f"{URL}/{car_id}", headers=headers).json()["distance_m"] is None


def test_cars_come_with_the_prices_of_their_model(
    client: TestClient, headers: dict[str, str], integration_settings: Settings, spot: GeoPoint
) -> None:
    car_id = add_car(integration_settings, spot)

    listed = client.get(URL, params=_area(spot), headers=headers).json()["items"]
    card = client.get(f"{URL}/{car_id}", headers=headers).json()

    expected = {
        "driving_per_minute": DEMO_PRICES.driving_per_minute,
        "parked_per_minute": DEMO_PRICES.parked_per_minute,
    }
    assert listed[0]["tariff"] == expected
    assert card["tariff"] == expected


def test_a_car_of_a_model_without_a_tariff_is_not_offered(
    client: TestClient, headers: dict[str, str], integration_settings: Settings, spot: GeoPoint
) -> None:
    spec = CarModelSpec("Test", f"No tariff {uuid.uuid4().hex[:8]}", 5, 50_000, 400, None)
    hidden = add_car(integration_settings, spot, model=spec)
    shown = add_car(integration_settings, GeoPoint(spot.lat + STEP, spot.lon))

    listed = client.get(URL, params=_area(spot), headers=headers)
    card = client.get(f"{URL}/{hidden}", headers=headers)

    assert _ids(listed) == [shown]
    assert card.status_code == HTTPStatus.NOT_FOUND


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
    add_car(integration_settings, spot)
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
