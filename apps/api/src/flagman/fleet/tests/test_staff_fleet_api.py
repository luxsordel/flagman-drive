import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from http import HTTPStatus
from typing import Any

import pytest
from fastapi.testclient import TestClient

from flagman.core.app import create_app
from flagman.core.config import Settings
from flagman.core.geo import GeoPoint
from flagman.fleet.domain import CarStatus
from flagman.fleet.seed import DEMO_MODELS
from flagman.fleet.tests.conftest import add_car, bearer, client_token, staff_token
from flagman.identity.domain import StaffRole

pytestmark = pytest.mark.integration

URL = "/admin/fleet/cars"
POINT = GeoPoint(lat=53.9023, lon=27.5615)


@pytest.fixture
def client(integration_settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(integration_settings), base_url="https://testserver") as client:
        yield client


@pytest.fixture
def operator(integration_settings: Settings) -> dict[str, str]:
    return bearer(staff_token(integration_settings, StaffRole.OPERATOR))


def _cars(response: Any) -> dict[str, dict[str, Any]]:
    assert response.status_code == HTTPStatus.OK, response.text
    return {car["id"]: car for car in response.json()["items"]}


@pytest.mark.parametrize("role", list(StaffRole))
def test_every_staff_role_sees_the_fleet(
    client: TestClient, integration_settings: Settings, role: StaffRole
) -> None:
    car_id = add_car(integration_settings, POINT)
    headers = bearer(staff_token(integration_settings, role))

    assert car_id in _cars(client.get(URL, headers=headers))
    assert client.get(f"{URL}/{car_id}", headers=headers).status_code == HTTPStatus.OK


@pytest.mark.parametrize("path", [URL, f"{URL}/{uuid.uuid4()}"])
def test_clients_and_strangers_do_not_see_the_fleet(
    client: TestClient, integration_settings: Settings, path: str
) -> None:
    as_client = client.get(path, headers=bearer(client_token(integration_settings)))
    anonymous = client.get(path)

    assert as_client.status_code == HTTPStatus.UNAUTHORIZED
    assert anonymous.status_code == HTTPStatus.UNAUTHORIZED


def test_a_car_comes_with_its_whole_state(
    client: TestClient, integration_settings: Settings, operator: dict[str, str]
) -> None:
    car_id = add_car(integration_settings, POINT, battery_pct=50, engine_locked=True)

    response = client.get(f"{URL}/{car_id}", headers=operator)

    assert response.status_code == HTTPStatus.OK, response.text
    car = response.json()
    spec = DEMO_MODELS[0].spec
    assert (car["brand"], car["model"], car["status"]) == (spec.brand, spec.name, "available")
    assert len(car["vin"]) == 17
    state = car["state"]
    assert (state["lat"], state["lon"]) == pytest.approx((POINT.lat, POINT.lon))
    assert state["battery_pct"] == 50
    assert state["range_km"] == spec.range_km // 2
    assert (state["doors_locked"], state["ignition_on"], state["engine_locked"]) == (
        True,
        False,
        True,
    )
    recorded_at = datetime.fromisoformat(state["recorded_at"])
    assert datetime.now(UTC) - recorded_at < timedelta(minutes=1)
    assert car == _cars(client.get(URL, headers=operator))[car_id]


def test_a_car_without_telemetry_is_listed_without_state(
    client: TestClient, integration_settings: Settings, operator: dict[str, str]
) -> None:
    car_id = add_car(integration_settings, None)

    assert _cars(client.get(URL, headers=operator))[car_id]["state"] is None


def test_unavailable_and_stale_cars_are_listed_too(
    client: TestClient, integration_settings: Settings, operator: dict[str, str]
) -> None:
    in_service = add_car(integration_settings, POINT, status=CarStatus.SERVICE)
    stale = add_car(integration_settings, POINT, age=timedelta(hours=1))

    cars = _cars(client.get(URL, headers=operator))

    assert cars[in_service]["status"] == "service"
    assert stale in cars


def test_the_list_carries_the_rules_and_the_servers_time(
    client: TestClient, integration_settings: Settings, operator: dict[str, str]
) -> None:
    body = client.get(URL, headers=operator).json()

    assert body["rules"] == {
        "telemetry_max_age_seconds": integration_settings.fleet_telemetry_max_age_seconds,
        "low_battery_pct": integration_settings.fleet_min_battery_pct,
    }
    assert abs(datetime.fromisoformat(body["as_of"]) - datetime.now(UTC)) < timedelta(minutes=1)


def test_cars_are_filtered_by_status(
    client: TestClient, integration_settings: Settings, operator: dict[str, str]
) -> None:
    available = add_car(integration_settings, POINT)
    in_service = add_car(integration_settings, POINT, status=CarStatus.SERVICE)
    offline = add_car(integration_settings, POINT, status=CarStatus.OFFLINE)

    cars = _cars(client.get(URL, params={"status": ["service", "offline"]}, headers=operator))

    assert {in_service, offline} <= set(cars)
    assert available not in cars
    assert {car["status"] for car in cars.values()} <= {"service", "offline"}


def test_cars_are_filtered_by_low_battery(
    client: TestClient, integration_settings: Settings, operator: dict[str, str]
) -> None:
    threshold = integration_settings.fleet_min_battery_pct
    low = add_car(integration_settings, POINT, battery_pct=threshold - 1)
    enough = add_car(integration_settings, POINT, battery_pct=threshold)
    unknown = add_car(integration_settings, None)

    cars = _cars(client.get(URL, params={"low_battery": True}, headers=operator))

    assert low in cars
    assert enough not in cars
    assert unknown not in cars


def test_both_filters_apply_together(
    client: TestClient, integration_settings: Settings, operator: dict[str, str]
) -> None:
    low_in_service = add_car(integration_settings, POINT, status=CarStatus.SERVICE, battery_pct=5)
    low_available = add_car(integration_settings, POINT, battery_pct=5)

    cars = _cars(
        client.get(URL, params={"status": "service", "low_battery": True}, headers=operator)
    )

    assert low_in_service in cars
    assert low_available not in cars


@pytest.mark.parametrize(
    "params",
    [{"status": "parked"}, {"low_battery": "maybe"}, {"plate": "1234 AB-7"}],
    ids=["unknown-status", "not-a-bool", "unknown-param"],
)
def test_a_wrong_filter_is_422(
    client: TestClient, operator: dict[str, str], params: dict[str, str]
) -> None:
    response = client.get(URL, params=params, headers=operator)

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY, response.text


def test_an_unknown_car_is_404(client: TestClient, operator: dict[str, str]) -> None:
    response = client.get(f"{URL}/{uuid.uuid4()}", headers=operator)

    assert response.status_code == HTTPStatus.NOT_FOUND
    assert response.json()["type"] == "urn:flagman:problem:car-not-found"
