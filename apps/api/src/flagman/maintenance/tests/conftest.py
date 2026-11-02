from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient

from flagman.core.app import create_app
from flagman.core.config import Settings
from flagman.fleet import api as fleet_api
from flagman.fleet.domain import CarState
from flagman.fleet.tests.conftest import FakeGateway, Staff
from flagman.identity.domain import StaffRole
from flagman.maintenance import api
from flagman.rentals.tests.conftest import CAR_POINT, NEAR_CAR, FakeStorage, reset_limits

BASE = "/admin/maintenance"


@pytest.fixture
def gateway() -> FakeGateway:
    return FakeGateway(outcome=CarState(CAR_POINT, 80, False, False, False, datetime.now(UTC)))


@pytest.fixture
def storage() -> FakeStorage:
    return FakeStorage()


@pytest.fixture
def app_client(
    integration_settings: Settings, gateway: FakeGateway, storage: FakeStorage
) -> Iterator[TestClient]:
    app = create_app(integration_settings)
    app.dependency_overrides[api.get_telematics_gateway] = lambda: gateway
    app.dependency_overrides[api.get_photo_storage] = lambda: storage
    app.dependency_overrides[fleet_api.get_telematics_gateway] = lambda: gateway
    reset_limits(integration_settings)
    with TestClient(app, base_url="https://testserver") as client:
        yield client


class Member:
    """A staff member with a real session, calling the maintenance endpoints."""

    def __init__(self, settings: Settings, client: TestClient, role: StaffRole) -> None:
        self.staff = Staff(settings, role)
        self.client = client

    @property
    def id(self) -> str:
        return str(self.staff.id)

    def get(self, path: str = "", **params: Any) -> Any:
        return self.client.get(f"{BASE}{path}", params=params, headers=self.staff.headers)

    def post(self, path: str, body: dict[str, Any] | None = None) -> Any:
        return self.client.post(f"{BASE}{path}", json=body or {}, headers=self.staff.headers)

    def create(self, car_id: str, task_type: str = "washing", **body: Any) -> Any:
        return self.post("/tasks", {"car_id": car_id, "type": task_type, **body})

    def start(self, task_id: str) -> Any:
        return self.post(f"/tasks/{task_id}/start")

    def complete(self, task_id: str, comment: str = "Washed inside and out") -> Any:
        return self.post(f"/tasks/{task_id}/complete", {"comment": comment})

    def command(self, task_id: str, command: str, lat: float = NEAR_CAR.lat) -> Any:
        return self.post(
            f"/tasks/{task_id}/commands", {"command": command, "lat": lat, "lon": NEAR_CAR.lon}
        )

    def upload(self, task_id: str, data: bytes) -> Any:
        return self.client.post(
            f"{BASE}/tasks/{task_id}/photos",
            content=data,
            headers={**self.staff.headers, "Content-Type": "image/jpeg"},
        )
