import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from redis import Redis

from conftest import SettingsFactory
from flagman.core.app import create_app
from flagman.core.config import Settings
from flagman.core.ratelimit import KEY_PREFIX, Limit
from flagman.fleet.tests.conftest import FakeGateway, Staff, run_in_db
from flagman.identity import service as identity
from flagman.identity.domain import StaffRole
from flagman.rentals import api as rentals_api
from flagman.rentals.tests.conftest import (
    FakeStorage,
    Person,
    demo_tariffs,  # noqa: F401 - autouse: the rentals the tests reserve need the tariffs
    opened_state,
    query,
    reset_limits,
    with_payments,
)
from flagman.support import api


@pytest.fixture
def integration_settings(
    integration_env: dict[str, str], migrated_database: None, make_settings: SettingsFactory
) -> Settings:
    # A trip the tests open takes a hold, so the rentals' payment provider is needed here too.
    return with_payments(integration_env, make_settings)


@pytest.fixture
def storage() -> FakeStorage:
    return FakeStorage()


@pytest.fixture
def app_client(integration_settings: Settings, storage: FakeStorage) -> Iterator[TestClient]:
    app = create_app(integration_settings)
    app.dependency_overrides[api.get_photo_storage] = lambda: storage
    app.dependency_overrides[rentals_api.get_photo_storage] = lambda: storage
    app.dependency_overrides[rentals_api.get_telematics_gateway] = lambda: FakeGateway(
        outcome=opened_state()
    )
    reset_limits(integration_settings)
    with TestClient(app, base_url="https://testserver") as client:
        yield client


@pytest.fixture
def person(integration_settings: Settings, app_client: TestClient) -> Person:
    return Person(integration_settings, app_client)


@pytest.fixture
def operator(integration_settings: Settings) -> Staff:
    return Staff(integration_settings, StaffRole.OPERATOR)


def write(person: Person, text: str, rental_id: str | None = None) -> Any:
    body: dict[str, Any] = {"text": text}
    if rental_id is not None:
        body["rental_id"] = rental_id
    return person.client.post("/support/messages", json=body, headers=person.headers)


def send_photo(person: Person, data: bytes) -> Any:
    return person.client.post(
        "/support/messages/photos",
        content=data,
        headers={**person.headers, "Content-Type": "image/jpeg"},
    )


def audit_actions(settings: Settings, target_id: str) -> list[str]:
    rows = query(
        settings,
        "SELECT action FROM audit_events WHERE target_id = :id ORDER BY occurred_at, id",
        id=uuid.UUID(target_id),
    )
    return [str(row.action) for row in rows]


def name_staff(staff: Staff, name: str) -> None:
    run_in_db(staff.settings, lambda db: identity.set_staff_display_name(db, staff.email, name))


def reply(client: TestClient, staff: Staff, conversation: str, text: str, seen_seq: int) -> Any:
    return client.post(
        f"/admin/support/conversations/{conversation}/messages",
        json={"text": text, "seen_seq": seen_seq},
        headers=staff.headers,
    )


def exhaust(settings: Settings, limit: Limit, key: str) -> None:
    """Uses up the limit without sending dozens of real requests."""
    with Redis(
        host=settings.redis_host,
        port=settings.redis_port,
        password=settings.redis_password.get_secret_value(),
    ) as redis:
        redis.set(f"{KEY_PREFIX}:{limit.name}:{key}", limit.max_hits, ex=limit.window_seconds)
