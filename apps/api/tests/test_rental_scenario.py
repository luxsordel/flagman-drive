"""The whole rental through the API, with the real car emulator and the fake payment provider:
reserve, inspect, open, drive, finish, receipt (#111).

The emulator runs as its own process, as in Docker Compose; the test relays its telemetry to the
API, signed like the emulator's webhooks, so the API sees where the car really is.
"""

import base64
import json
import os
import secrets
import shutil
import socket
import subprocess
import time
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx2
import pytest
from fastapi.testclient import TestClient

from conftest import SettingsFactory
from flagman.core.app import create_app
from flagman.core.config import Settings
from flagman.core.geo import GeoPoint
from flagman.core.signing import SIGNATURE_HEADER, TIMESTAMP_HEADER, sign
from flagman.fleet.tests.conftest import run_in_db
from flagman.geo import service as geo
from flagman.rentals import api as rentals_api
from flagman.rentals.tests.conftest import (
    FakeStorage,
    Person,
    add_priced_car,
    execute,
    photo,
    query,
)

pytestmark = pytest.mark.integration

EMULATOR_PROJECT = Path(__file__).resolve().parents[3] / "services" / "car-emulator"
# Inside the demo zone, far from its edge: the car's short hops stay within it.
CENTER = GeoPoint(lat=53.905, lon=27.55)
_STARTUP_TIMEOUT_S = 120


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port: int = probe.getsockname()[1]
        return port


@dataclass(frozen=True)
class Emulator:
    url: str
    command_secret: str
    telemetry_secret: str

    def request(self, method: str, path: str) -> Any:
        """Signed like the API's commands: every route but /health needs it."""
        timestamp = int(time.time())
        signature = sign(self.command_secret.encode(), timestamp, method, path.encode(), b"")
        headers = {TIMESTAMP_HEADER: str(timestamp), SIGNATURE_HEADER: signature}
        response = httpx2.request(method, self.url + path, headers=headers, timeout=10)
        response.raise_for_status()
        return response.json()


@pytest.fixture(scope="module")
def emulator() -> Iterator[Emulator]:
    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("uv is needed to run the car emulator")
    port = _free_port()
    emulator = Emulator(
        url=f"http://127.0.0.1:{port}",
        command_secret=secrets.token_hex(32),
        telemetry_secret=secrets.token_hex(32),
    )
    env = {
        **os.environ,
        "EMULATOR_HOST": "127.0.0.1",
        "EMULATOR_PORT": str(port),
        "EMULATOR_COMMAND_SECRET": emulator.command_secret,
        "EMULATOR_TELEMETRY_SECRET": emulator.telemetry_secret,
        "EMULATOR_TELEMETRY_URL": "",
        "EMULATOR_CAR_COUNT": "10",
        "EMULATOR_SEED": "111",
        # A minute of the city in a second, and a rented car sets off right away.
        "EMULATOR_TIME_SCALE": "60",
        "EMULATOR_TELEMETRY_INTERVAL_S": "0.2",
        "EMULATOR_TRIP_SHARE": "0",
        "EMULATOR_RENTAL_REST_S": "0",
        "EMULATOR_DEMO_ENDPOINTS": "true",
    }
    process = subprocess.Popen(  # noqa: S603 - our own emulator, by the uv found above
        [uv, "run", "--project", str(EMULATOR_PROJECT), "car-emulator"],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        _wait_until(lambda: _healthy(emulator.url), _STARTUP_TIMEOUT_S, "the emulator to start")
        yield emulator
    finally:
        process.terminate()
        process.wait(timeout=10)


def _healthy(url: str) -> bool:
    try:
        return httpx2.get(url + "/health", timeout=1).status_code == 200
    except httpx2.HTTPError:
        return False


def _wait_until(check: Callable[[], bool], timeout_s: float, what: str) -> None:
    deadline = time.monotonic() + timeout_s
    while not check():
        if time.monotonic() > deadline:
            pytest.fail(f"timed out waiting for {what}")
        time.sleep(0.2)


@pytest.fixture
def scenario_settings(
    integration_env: dict[str, str],
    migrated_database: None,
    make_settings: SettingsFactory,
    emulator: Emulator,
) -> Settings:
    return make_settings(
        **{name.lower(): value for name, value in integration_env.items()},
        payments_provider="fake",
        payments_webhook_secret=secrets.token_hex(32),
        payments_token_encryption_key=base64.b64encode(secrets.token_bytes(32)).decode(),
        emulator_url=emulator.url,
        emulator_command_secret=emulator.command_secret,
        emulator_telemetry_secret=emulator.telemetry_secret,
    )


def _relay_telemetry(client: TestClient, emulator: Emulator) -> list[dict[str, Any]]:
    """What the emulator's webhook would send now: every car's state as of this moment."""
    now = datetime.now(UTC).isoformat()
    cars = [{**car, "updated_at": now} for car in emulator.request("GET", "/cars")]
    body = json.dumps({"sent_at": now, "cars": cars}).encode()
    timestamp = int(time.time())
    headers = {
        "Content-Type": "application/json",
        TIMESTAMP_HEADER: str(timestamp),
        SIGNATURE_HEADER: sign(
            emulator.telemetry_secret.encode(), timestamp, "POST", b"/fleet/telemetry", body
        ),
    }
    response = client.post("/fleet/telemetry", content=body, headers=headers)
    assert response.status_code < 300, response.text
    return cars


def _seed(settings: Settings) -> None:
    run_in_db(settings, lambda db: geo.seed_demo_zone(db, datetime.now(UTC)))


def _nearest_to_center(cars: list[dict[str, Any]]) -> dict[str, Any]:
    return min(
        cars, key=lambda car: (car["lat"] - CENTER.lat) ** 2 + (car["lon"] - CENTER.lon) ** 2
    )


def test_a_whole_rental_from_the_map_to_the_receipt(
    scenario_settings: Settings, emulator: Emulator
) -> None:
    _seed(scenario_settings)
    app = create_app(scenario_settings)
    # The object storage is not part of this scenario: the photos stay in memory.
    storage = FakeStorage()
    app.dependency_overrides[rentals_api.get_photo_storage] = lambda: storage
    with TestClient(app, base_url="https://testserver") as client:
        emulated = _nearest_to_center(emulator.request("GET", "/cars"))
        car_id = add_priced_car(scenario_settings, None)
        # The API's car is the emulator's: telemetry and commands are matched by this id.
        execute(
            scenario_settings,
            "UPDATE cars SET telematics_id = :telematics_id WHERE id = :id",
            telematics_id=uuid.UUID(emulated["id"]),
            id=uuid.UUID(car_id),
        )
        _relay_telemetry(client, emulator)
        [state] = query(
            scenario_settings,
            "SELECT status, battery_pct, state_at FROM cars WHERE id = :id",
            id=uuid.UUID(car_id),
        )
        assert state.state_at is not None, "the relayed telemetry did not reach the car"
        person = Person(scenario_settings, client)

        reserved = person.reserve(car_id)
        assert reserved.status_code == 201, reserved.text
        rental_id = str(reserved.json()["id"])
        person.inspect(rental_id)
        at_car = GeoPoint(lat=emulated["lat"], lon=emulated["lon"])
        opened = person.command(rental_id, "open", at=at_car)
        assert opened.status_code == 200, opened.text
        assert opened.json()["status"] == "in_trip"

        # The emulator takes the rented car for a drive by itself.
        path = f"/cars/{emulated['id']}"
        _wait_until(lambda: emulator.request("GET", path)["ignition_on"], 30, "the car to drive")
        _relay_telemetry(client, emulator)
        driving = person.get(rental_id).json()
        assert driving["car"]["lat"] is not None
        while_driving = client.post(f"/rentals/{rental_id}/finish", headers=person.headers)
        assert while_driving.status_code == 409

        # The demo stops it; the client photographs the car and finishes.
        stopped = emulator.request("POST", f"/demo/cars/{emulated['id']}/park")
        assert stopped["ignition_on"] is False
        _relay_telemetry(client, emulator)
        for index in range(4):
            uploaded = person.upload(rental_id, photo(index), stage="after")
            assert uploaded.status_code == 200
        assert person.submit(rental_id, stage="after").status_code == 200
        finished = client.post(f"/rentals/{rental_id}/finish", headers=person.headers)

        assert finished.status_code == 200, finished.text
        body = finished.json()
        assert (body["status"], body["charge_status"]) == ("paid", "paid")
        receipt = client.get(f"/rentals/{rental_id}/receipt", headers=person.headers).json()
        assert receipt == body["receipt"]
        assert receipt["card_last4"] == "4242"
        assert receipt["cost"]["total"] == sum(line["amount"] for line in receipt["cost"]["lines"])
        # The finish locked the real car, and the car is back on the map.
        assert emulator.request("GET", path)["doors_locked"] is True
        back = client.get(f"/rentals/{rental_id}", headers=person.headers).json()
        assert back["status"] == "paid"
