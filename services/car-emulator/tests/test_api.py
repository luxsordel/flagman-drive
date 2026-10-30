import json
import uuid

import httpx
import pytest
from fastapi import FastAPI

from car_emulator.fleet import FleetService
from car_emulator.security import MAX_BODY_BYTES
from tests.conftest import TELEMETRY_SECRET, FakeClock, signed


def _fleet(app: FastAPI) -> FleetService:
    fleet: FleetService = app.state.fleet
    return fleet


def _first_car_id(app: FastAPI) -> uuid.UUID:
    return _fleet(app).list_cars()[0].id


def _command_body(command_type: str, command_id: uuid.UUID | None = None) -> bytes:
    return json.dumps(
        {"command_id": str(command_id or uuid.uuid4()), "type": command_type}
    ).encode()


async def _post_command(
    client: httpx.AsyncClient, clock: FakeClock, car_id: uuid.UUID, body: bytes
) -> httpx.Response:
    path = f"/cars/{car_id}/commands"
    return await client.post(path, content=body, headers=signed("POST", path, body, clock()))


async def test_health_needs_no_signature(client: httpx.AsyncClient) -> None:
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_list_cars(client: httpx.AsyncClient, clock: FakeClock) -> None:
    response = await client.get("/cars", headers=signed("GET", "/cars", b"", clock()))
    assert response.status_code == 200
    cars = response.json()
    assert len(cars) == 5
    assert set(cars[0]) == {
        "id",
        "lat",
        "lon",
        "battery_pct",
        "doors_locked",
        "ignition_on",
        "engine_locked",
        "headlights_on",
        "gear",
        "updated_at",
    }


async def test_get_car(client: httpx.AsyncClient, app: FastAPI, clock: FakeClock) -> None:
    car_id = _first_car_id(app)
    path = f"/cars/{car_id}"
    response = await client.get(path, headers=signed("GET", path, b"", clock()))
    assert response.status_code == 200
    assert response.json()["id"] == str(car_id)


async def test_get_unknown_car(client: httpx.AsyncClient, clock: FakeClock) -> None:
    path = f"/cars/{uuid.uuid4()}"
    response = await client.get(path, headers=signed("GET", path, b"", clock()))
    assert response.status_code == 404


@pytest.mark.parametrize("path", ["/cars", "/docs", "/openapi.json", "/nonexistent"])
async def test_unsigned_requests_are_rejected(client: httpx.AsyncClient, path: str) -> None:
    response = await client.get(path)
    assert response.status_code == 401
    assert response.json() == {"detail": "invalid_signature"}


async def test_telemetry_secret_cannot_sign_commands(
    client: httpx.AsyncClient, clock: FakeClock
) -> None:
    headers = signed("GET", "/cars", b"", clock(), secret=TELEMETRY_SECRET)
    response = await client.get("/cars", headers=headers)
    assert response.status_code == 401


async def test_signature_bound_to_path(
    client: httpx.AsyncClient, app: FastAPI, clock: FakeClock
) -> None:
    car_ids = [car.id for car in _fleet(app).list_cars()]
    body = _command_body("open")
    headers = signed("POST", f"/cars/{car_ids[0]}/commands", body, clock())
    response = await client.post(f"/cars/{car_ids[1]}/commands", content=body, headers=headers)
    assert response.status_code == 401
    assert _fleet(app).get_car(car_ids[1]).doors_locked


async def test_signature_bound_to_query(client: httpx.AsyncClient, clock: FakeClock) -> None:
    response = await client.get("/cars?x=1", headers=signed("GET", "/cars", b"", clock()))
    assert response.status_code == 401


async def test_expired_signature_is_rejected(client: httpx.AsyncClient, clock: FakeClock) -> None:
    headers = signed("GET", "/cars", b"", clock())
    clock.advance(301)
    response = await client.get("/cars", headers=headers)
    assert response.status_code == 401


async def test_oversized_body_is_rejected_before_parsing(
    client: httpx.AsyncClient, app: FastAPI
) -> None:
    body = b"x" * (MAX_BODY_BYTES + 1)
    response = await client.post(f"/cars/{_first_car_id(app)}/commands", content=body)
    assert response.status_code == 413


@pytest.mark.parametrize(
    ("command", "field", "expected"),
    [
        ("open", "doors_locked", False),
        ("lock_engine", "engine_locked", True),
        ("unlock_engine", "engine_locked", False),
    ],
)
async def test_execute_command(
    client: httpx.AsyncClient,
    app: FastAPI,
    clock: FakeClock,
    command: str,
    field: str,
    expected: bool,
) -> None:
    car_id = _first_car_id(app)
    command_id = uuid.uuid4()
    response = await _post_command(client, clock, car_id, _command_body(command, command_id))
    assert response.status_code == 200
    result = response.json()
    assert result["command_id"] == str(command_id)
    assert result["car"][field] is expected
    assert getattr(_fleet(app).get_car(car_id), field) is expected


async def test_close_after_open(client: httpx.AsyncClient, app: FastAPI, clock: FakeClock) -> None:
    car_id = _first_car_id(app)
    await _post_command(client, clock, car_id, _command_body("open"))
    response = await _post_command(client, clock, car_id, _command_body("close"))
    assert response.status_code == 200
    assert response.json()["car"]["doors_locked"] is True


async def test_replayed_command_is_rejected(
    client: httpx.AsyncClient, app: FastAPI, clock: FakeClock
) -> None:
    car_id = _first_car_id(app)
    body = _command_body("open")
    path = f"/cars/{car_id}/commands"
    headers = signed("POST", path, body, clock())
    assert (await client.post(path, content=body, headers=headers)).status_code == 200
    response = await client.post(path, content=body, headers=headers)
    assert response.status_code == 409
    assert response.json() == {"detail": "duplicate_command"}


async def test_rejected_command(client: httpx.AsyncClient, app: FastAPI, clock: FakeClock) -> None:
    car_id = _first_car_id(app)
    _fleet(app)._cars[car_id].ignition_on = True
    response = await _post_command(client, clock, car_id, _command_body("close"))
    assert response.status_code == 409
    assert response.json() == {"detail": "command_rejected:ignition_on"}


async def test_command_for_unknown_car(client: httpx.AsyncClient, clock: FakeClock) -> None:
    response = await _post_command(client, clock, uuid.uuid4(), _command_body("open"))
    assert response.status_code == 404


@pytest.mark.parametrize(
    "payload",
    [
        {"command_id": str(uuid.uuid4()), "type": "self_destruct"},
        {"command_id": "not-a-uuid", "type": "open"},
        {"command_id": str(uuid.uuid4()), "type": "open", "extra": 1},
        {"type": "open"},
    ],
)
async def test_invalid_command_payload(
    client: httpx.AsyncClient, app: FastAPI, clock: FakeClock, payload: dict[str, object]
) -> None:
    body = json.dumps(payload).encode()
    response = await _post_command(client, clock, _first_car_id(app), body)
    assert response.status_code == 422
