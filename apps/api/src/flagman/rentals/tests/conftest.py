import random
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from io import BytesIO
from typing import Any

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from redis import Redis
from sqlalchemy import Row, text
from sqlalchemy.ext.asyncio import AsyncSession

from flagman.core.app import create_app
from flagman.core.config import Settings
from flagman.core.geo import GeoPoint
from flagman.fleet import repository as fleet_repository
from flagman.fleet.domain import CarState
from flagman.fleet.seed import DEMO_MODELS
from flagman.fleet.tests.conftest import FakeGateway, bearer, run_in_db
from flagman.identity.domain import AccessClaims, issue_access_token
from flagman.kyc.domain import KycStatus
from flagman.pricing import service as pricing
from flagman.rentals import api

# The demo zone of the pilot is around here; distances are on the spheroid near Minsk.
CAR_POINT = GeoPoint(lat=53.9023, lon=27.5615)
# About 111.3 km per degree of latitude here: these are roughly 30 m and 70 m north of the car.
NEAR_CAR = GeoPoint(lat=CAR_POINT.lat + 0.00027, lon=CAR_POINT.lon)
FAR_FROM_CAR = GeoPoint(lat=CAR_POINT.lat + 0.00063, lon=CAR_POINT.lon)


def query(settings: Settings, sql: str, **params: Any) -> list[Row[Any]]:
    async def run(db: AsyncSession) -> list[Row[Any]]:
        async with db.begin():
            return list(await db.execute(text(sql), params))

    return run_in_db(settings, run)


def execute(settings: Settings, sql: str, **params: Any) -> None:
    async def run(db: AsyncSession) -> None:
        async with db.begin():
            await db.execute(text(sql), params)

    run_in_db(settings, run)


def reset_limits(settings: Settings) -> None:
    # The Redis container is shared by the test session.
    with Redis(
        host=settings.redis_host,
        port=settings.redis_port,
        password=settings.redis_password.get_secret_value(),
    ) as redis:
        redis.flushdb()


@dataclass
class FakeStorage:
    objects: dict[str, bytes] = field(default_factory=dict)
    fail_put: bool = False

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        if self.fail_put:
            raise ConnectionError("storage is down")
        self.objects[key] = data

    async def delete(self, key: str) -> None:
        self.objects.pop(key, None)

    async def signed_get_url(self, key: str, ttl: timedelta) -> str:
        return f"https://storage.test/{key}?ttl={int(ttl.total_seconds())}"


def opened_state(point: GeoPoint = CAR_POINT) -> CarState:
    return CarState(point, 80, False, False, False, datetime.now(UTC))


@pytest.fixture(autouse=True)
def demo_tariffs(integration_settings: Settings) -> None:
    """Every demo model has the demo tariff: cars without one are not rented out.

    Seeding again changes nothing, so every test may run it.
    """

    async def seed(db: AsyncSession) -> None:
        async with db.begin():
            for demo in DEMO_MODELS:
                await fleet_repository.ensure_model(db, demo.spec, datetime.now(UTC))
        await pricing.seed_demo_tariffs(db, datetime.now(UTC))

    run_in_db(integration_settings, seed)


@pytest.fixture
def gateway() -> FakeGateway:
    return FakeGateway(outcome=opened_state())


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
    reset_limits(integration_settings)
    with TestClient(app, base_url="https://testserver") as client:
        yield client


class Person:
    """A client in the database with a token; approved for rentals unless told otherwise."""

    def __init__(
        self, settings: Settings, client: TestClient, kyc: KycStatus | None = KycStatus.APPROVED
    ) -> None:
        self.settings = settings
        self.client = client
        self.id = uuid.uuid4()
        phone = f"+37529{random.SystemRandom().randrange(10**7):07d}"
        execute(
            settings,
            "INSERT INTO users (id, phone, created_at) VALUES (:id, :phone, now())",
            id=self.id,
            phone=phone,
        )
        if kyc is not None:
            execute(
                settings,
                "INSERT INTO kyc_applications (id, user_id, status, license_issued_on, "
                "submitted_at, decided_at, created_at, updated_at) VALUES (:id, :user_id, "
                ":status, '2015-01-01', now(), now(), now(), now())",
                id=uuid.uuid4(),
                user_id=self.id,
                status=kyc.value,
            )
        secret = settings.api_jwt_secret.get_secret_value()
        token = issue_access_token(AccessClaims(self.id, uuid.uuid4()), secret, datetime.now(UTC))
        self.headers = bearer(token)

    def block(self) -> None:
        execute(self.settings, "UPDATE users SET blocked_at = now() WHERE id = :id", id=self.id)

    def reserve(self, car_id: str) -> Any:
        return self.client.post("/rentals", json={"car_id": car_id}, headers=self.headers)

    def get(self, rental_id: str) -> Any:
        return self.client.get(f"/rentals/{rental_id}", headers=self.headers)

    def current(self) -> Any:
        return self.client.get("/rentals/current", headers=self.headers)

    def cancel(self, rental_id: str) -> Any:
        return self.client.post(f"/rentals/{rental_id}/cancel", headers=self.headers)

    def upload(self, rental_id: str, data: bytes, stage: str = "before") -> Any:
        return self.client.post(
            f"/rentals/{rental_id}/inspections/{stage}/photos",
            content=data,
            headers={**self.headers, "Content-Type": "image/jpeg"},
        )

    def damage(self, rental_id: str, side: str, comment: str, stage: str = "before") -> Any:
        return self.client.post(
            f"/rentals/{rental_id}/inspections/{stage}/damages",
            json={"side": side, "comment": comment},
            headers=self.headers,
        )

    def submit(self, rental_id: str, stage: str = "before") -> Any:
        return self.client.post(
            f"/rentals/{rental_id}/inspections/{stage}/submit", headers=self.headers
        )

    def inspect(self, rental_id: str, photos: int = 4) -> None:
        for index in range(photos):
            assert self.upload(rental_id, photo(index)).status_code == 200
        assert self.submit(rental_id).status_code == 200

    def command(self, rental_id: str, command: str, at: GeoPoint = NEAR_CAR) -> Any:
        return self.client.post(
            f"/rentals/{rental_id}/commands",
            json={"command": command, "lat": at.lat, "lon": at.lon},
            headers=self.headers,
        )


def photo(seed: int = 0) -> bytes:
    output = BytesIO()
    Image.new("RGB", (64, 48), (seed * 30 % 256, 120, 40)).save(output, format="JPEG")
    return output.getvalue()


def car_status(settings: Settings, car_id: str) -> str:
    rows = query(settings, "SELECT status FROM cars WHERE id = :id", id=uuid.UUID(car_id))
    return str(rows[0].status)


def problem_type(response: Any) -> str:
    return str(response.json()["type"]).removeprefix("urn:flagman:problem:")
