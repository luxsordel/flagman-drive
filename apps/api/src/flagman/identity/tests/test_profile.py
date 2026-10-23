import asyncio
import secrets
import uuid
from collections.abc import Callable, Iterator
from datetime import UTC, date, datetime, timedelta
from http import HTTPStatus
from typing import Any

import jwt
import pytest
from fastapi.testclient import TestClient
from redis import Redis

from flagman.core.app import create_app
from flagman.core.config import Settings
from flagman.core.db import create_engine, create_session_factory
from flagman.identity import service
from flagman.identity.api import get_sms_sender
from flagman.identity.domain import (
    BirthDateInvalidError,
    StaffAccessClaims,
    StaffRole,
    generate_otp_code,
    issue_staff_access_token,
)
from flagman.identity.schemas import ClientProfile, ClientProfileUpdate

pytestmark = pytest.mark.integration


class FakeSms:
    def __init__(self) -> None:
        self.sent: dict[str, str] = {}

    def choose_code(self, phone: str) -> str:
        return generate_otp_code()

    async def send_otp(self, phone: str, code: str) -> None:
        self.sent[phone] = code


@pytest.fixture
def client(integration_settings: Settings) -> Iterator[TestClient]:
    # The Redis container is shared by the test session; sign-ins here must not hit its limits.
    with Redis(
        host=integration_settings.redis_host,
        port=integration_settings.redis_port,
        password=integration_settings.redis_password.get_secret_value(),
    ) as redis:
        redis.flushdb()
    with TestClient(create_app(integration_settings), base_url="https://testserver") as client:
        yield client


class Person:
    """A signed-in client with a fresh phone: users stay in the shared database between tests."""

    def __init__(self, client: TestClient) -> None:
        self.client = client
        self.phone = f"+37529{secrets.randbelow(10**7):07d}"
        sms = FakeSms()
        client.app.dependency_overrides[get_sms_sender] = lambda: sms  # type: ignore[attr-defined]
        accepted = client.post("/auth/otp/request", json={"phone": self.phone})
        assert accepted.status_code == HTTPStatus.ACCEPTED, accepted.text
        verified = client.post(
            "/auth/otp/verify",
            json={
                "phone": self.phone,
                "code": sms.sent[self.phone],
                "device_id": str(uuid.uuid4()),
            },
        )
        assert verified.status_code == HTTPStatus.OK, verified.text
        self.headers = {"Authorization": f"Bearer {verified.json()['access_token']}"}
        self.user_id = uuid.UUID(_token_subject(verified.json()["access_token"]))

    def profile(self) -> Any:
        return self.client.get("/me", headers=self.headers)

    def update(self, body: dict[str, Any]) -> Any:
        return self.client.patch("/me", json=body, headers=self.headers)


def _token_subject(token: str) -> str:
    subject: str = jwt.decode(token, options={"verify_signature": False})["sub"]
    return subject


@pytest.fixture
def person(client: TestClient) -> Callable[[], Person]:
    return lambda: Person(client)


def _birth_date(settings: Settings, user_id: uuid.UUID) -> date | None:
    async def run() -> date | None:
        engine = create_engine(settings)
        try:
            async with create_session_factory(engine)() as db, db.begin():
                return await service.get_client_birth_date(db, user_id)
        finally:
            await engine.dispose()

    return asyncio.run(run())


def _update_at(
    settings: Settings, user_id: uuid.UUID, birth_date: str, now: datetime
) -> ClientProfile:
    async def run() -> ClientProfile:
        engine = create_engine(settings)
        try:
            async with create_session_factory(engine)() as db:
                update = ClientProfileUpdate.model_validate({"birth_date": birth_date})
                return await service.ClientProfileService(db=db).update_profile(
                    user_id, update, now
                )
        finally:
            await engine.dispose()

    return asyncio.run(run())


def test_new_client_has_an_empty_profile(person: Callable[[], Person]) -> None:
    anna = person()

    response = anna.profile()

    assert response.status_code == HTTPStatus.OK, response.text
    assert response.headers["Cache-Control"] == "no-store"
    assert response.json() == {
        "first_name": None,
        "last_name": None,
        "birth_date": None,
        "email": None,
        "language": "ru",
        "phone": anna.phone,
    }


def test_update_changes_only_the_fields_sent(person: Callable[[], Person]) -> None:
    anna = person()

    first = anna.update({"first_name": "  Анна ", "birth_date": "1990-05-17"})
    second = anna.update({"last_name": "Иванова", "language": "en"})

    assert first.status_code == HTTPStatus.OK, first.text
    assert first.headers["Cache-Control"] == "no-store"
    assert second.status_code == HTTPStatus.OK, second.text
    expected = {
        "first_name": "Анна",
        "last_name": "Иванова",
        "birth_date": "1990-05-17",
        "email": None,
        "language": "en",
        "phone": anna.phone,
    }
    assert second.json() == expected
    assert anna.profile().json() == expected


def test_empty_update_changes_nothing(person: Callable[[], Person]) -> None:
    anna = person()
    anna.update({"first_name": "Анна"})

    response = anna.update({})

    assert response.status_code == HTTPStatus.OK, response.text
    assert response.json()["first_name"] == "Анна"


def test_email_is_lowercased_and_can_be_removed(person: Callable[[], Person]) -> None:
    anna = person()

    added = anna.update({"email": "Anna.Ivanova@Example.COM"})
    removed = anna.update({"email": None})

    assert added.json()["email"] == "anna.ivanova@example.com"
    assert removed.status_code == HTTPStatus.OK, removed.text
    assert removed.json()["email"] is None


@pytest.mark.parametrize(
    ("body", "field"),
    [
        ({"phone": "+375291111111"}, "phone"),
        ({"is_verified": True}, "is_verified"),
        ({"first_name": None}, "first_name"),
        ({"last_name": "   "}, "last_name"),
        ({"first_name": "x" * 101}, "first_name"),
        ({"birth_date": None}, "birth_date"),
        ({"birth_date": "1990-05-17T00:00:00"}, "birth_date"),
        ({"birth_date": "1990-05-17T00:00:00Z"}, "birth_date"),
        ({"birth_date": 631152000}, "birth_date"),
        ({"birth_date": "17.05.1990"}, "birth_date"),
        ({"language": "de"}, "language"),
        ({"language": None}, "language"),
        ({"email": "not an email"}, "email"),
    ],
)
def test_invalid_updates_are_rejected(
    person: Callable[[], Person], body: dict[str, Any], field: str
) -> None:
    anna = person()

    response = anna.update(body)

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY, response.text
    problem = response.json()
    assert problem["type"] == "urn:flagman:problem:validation-error"
    # A field that may be left out is a union with None, so pydantic adds the branch to loc.
    assert {tuple(error["loc"][:2]) for error in problem["errors"]} == {("body", field)}
    assert anna.profile().json()["phone"] == anna.phone


@pytest.mark.parametrize(
    "birth_date",
    [
        # Two days ahead: tomorrow in UTC may still be today somewhere.
        (datetime.now(UTC) + timedelta(days=2)).date().isoformat(),
        "1890-01-01",
    ],
)
def test_implausible_birth_date_is_rejected(person: Callable[[], Person], birth_date: str) -> None:
    anna = person()

    response = anna.update({"birth_date": birth_date, "first_name": "Anna"})

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY, response.text
    assert response.json()["type"] == "urn:flagman:problem:birth-date-invalid"
    # Nothing of the rejected request is saved.
    assert anna.profile().json()["first_name"] is None


def test_birth_date_bound_is_today(
    person: Callable[[], Person], integration_settings: Settings
) -> None:
    anna = person()
    now = datetime(2026, 10, 23, 23, 59, tzinfo=UTC)

    saved = _update_at(integration_settings, anna.user_id, "2026-10-23", now)

    assert saved.birth_date == date(2026, 10, 23)
    with pytest.raises(BirthDateInvalidError):
        _update_at(integration_settings, anna.user_id, "2026-10-24", now)


def test_rejected_values_are_not_echoed(person: Callable[[], Person]) -> None:
    anna = person()

    response = anna.update({"first_name": f"Секрет{chr(0x200B)}Имя"})

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY, response.text
    assert "Секрет" not in response.text


def test_clients_see_and_change_only_their_own_profile(person: Callable[[], Person]) -> None:
    anna, boris = person(), person()

    anna.update({"first_name": "Анна", "birth_date": "1990-05-17"})
    boris.update({"first_name": "Борис"})

    assert anna.profile().json()["first_name"] == "Анна"
    assert boris.profile().json() == {
        "first_name": "Борис",
        "last_name": None,
        "birth_date": None,
        "email": None,
        "language": "ru",
        "phone": boris.phone,
    }


@pytest.mark.parametrize("method", ["GET", "PATCH"])
def test_profile_needs_a_client_token(
    client: TestClient, integration_settings: Settings, method: str
) -> None:
    staff_token = issue_staff_access_token(
        StaffAccessClaims(staff_id=uuid.uuid4(), session_id=uuid.uuid4(), role=StaffRole.ADMIN),
        integration_settings.api_jwt_secret.get_secret_value(),
        datetime.now(UTC),
    )

    for headers in ({}, {"Authorization": f"Bearer {staff_token}"}):
        response = client.request(method, "/me", json={}, headers=headers)

        assert response.status_code == HTTPStatus.UNAUTHORIZED, response.text
        assert response.json()["type"] == "urn:flagman:problem:access-token-invalid"


def test_birth_date_is_available_to_other_modules(
    person: Callable[[], Person], integration_settings: Settings
) -> None:
    anna = person()
    assert _birth_date(integration_settings, anna.user_id) is None

    anna.update({"birth_date": "1990-05-17"})

    assert _birth_date(integration_settings, anna.user_id) == date(1990, 5, 17)
    assert _birth_date(integration_settings, uuid.uuid4()) is None
