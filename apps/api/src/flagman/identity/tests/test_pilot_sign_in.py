"""The pilot in prod mode without an SMS gateway: test phones sign in with the shared code (#47)."""

import secrets
import uuid
from collections.abc import Iterator
from http import HTTPStatus
from typing import Any

import pytest
from fastapi.testclient import TestClient
from redis import Redis

from conftest import SettingsFactory
from flagman.core.app import create_app
from flagman.core.config import Settings

pytestmark = pytest.mark.integration

FIXED_CODE = "482913"


def _random_phone() -> str:
    # A new phone per test: codes stay in the shared database between tests.
    return f"+37544{secrets.randbelow(10**7):07d}"


@pytest.fixture
def test_phone() -> str:
    return _random_phone()


@pytest.fixture
def pilot_settings(
    integration_env: dict[str, str],
    migrated_database: None,
    make_settings: SettingsFactory,
    test_phone: str,
) -> Settings:
    values: dict[str, Any] = {name.lower(): value for name, value in integration_env.items()}
    values |= {
        "api_env": "prod",
        "sms_backend": "fixed",
        "sms_fixed_code": FIXED_CODE,
        "sms_fixed_phones": [test_phone],
    }
    return make_settings(**values)


@pytest.fixture
def client(pilot_settings: Settings, capsys: pytest.CaptureFixture[str]) -> Iterator[TestClient]:
    with Redis(
        host=pilot_settings.redis_host,
        port=pilot_settings.redis_port,
        password=pilot_settings.redis_password.get_secret_value(),
    ) as redis:
        redis.flushdb()
    # The real SMS sender from the settings, not a fake: this is what the pilot server runs.
    # Created inside the fixture so that the app's log handler writes to the captured stdout.
    with TestClient(create_app(pilot_settings), base_url="https://testserver") as client:
        yield client


def _request_code(client: TestClient, phone: str) -> Any:
    return client.post("/auth/otp/request", json={"phone": phone})


def _verify(client: TestClient, phone: str, code: str) -> Any:
    body = {"phone": phone, "code": code, "device_id": str(uuid.uuid4())}
    return client.post("/auth/otp/verify", json=body)


def test_test_phone_signs_in_with_the_fixed_code(client: TestClient, test_phone: str) -> None:
    requested = _request_code(client, test_phone)
    verified = _verify(client, test_phone, FIXED_CODE)

    assert requested.status_code == HTTPStatus.ACCEPTED, requested.text
    assert verified.status_code == HTTPStatus.OK, verified.text
    assert verified.json()["access_token"]
    # Prod protections stay on while signing in works.
    assert requested.headers["strict-transport-security"].startswith("max-age=")
    assert client.get("/docs").status_code == HTTPStatus.NOT_FOUND


def test_other_phones_cannot_get_a_code(client: TestClient) -> None:
    phone = _random_phone()

    requested = _request_code(client, phone)
    verified = _verify(client, phone, FIXED_CODE)

    assert requested.status_code == HTTPStatus.FORBIDDEN
    assert requested.json()["type"] == "urn:flagman:problem:sign-in-not-allowed"
    assert phone not in requested.text
    # The shared code is no master key: without a pending code there is nothing to match.
    assert verified.status_code == HTTPStatus.BAD_REQUEST
    assert verified.json()["type"] == "urn:flagman:problem:otp-expired"


def test_fixed_code_is_used_up_like_any_other(client: TestClient, test_phone: str) -> None:
    _request_code(client, test_phone)

    first = _verify(client, test_phone, FIXED_CODE)
    second = _verify(client, test_phone, FIXED_CODE)

    assert first.status_code == HTTPStatus.OK
    assert second.status_code == HTTPStatus.BAD_REQUEST
    assert second.json()["type"] == "urn:flagman:problem:otp-expired"


def test_wrong_codes_spend_the_attempts(client: TestClient, test_phone: str) -> None:
    _request_code(client, test_phone)

    problems = [_verify(client, test_phone, "000000").json()["type"] for _ in range(5)]
    after = _verify(client, test_phone, FIXED_CODE)

    assert problems == ["urn:flagman:problem:otp-invalid"] * 4 + ["urn:flagman:problem:otp-expired"]
    assert after.status_code == HTTPStatus.BAD_REQUEST


def test_probing_for_test_phones_is_rate_limited(client: TestClient) -> None:
    phone = _random_phone()

    first = _request_code(client, phone)
    second = _request_code(client, phone)

    assert first.status_code == HTTPStatus.FORBIDDEN
    assert second.status_code == HTTPStatus.TOO_MANY_REQUESTS


def test_the_code_never_reaches_the_log(
    client: TestClient, test_phone: str, capsys: pytest.CaptureFixture[str]
) -> None:
    _request_code(client, test_phone)
    _verify(client, test_phone, FIXED_CODE)

    output = capsys.readouterr()
    assert FIXED_CODE not in output.out + output.err
    assert test_phone not in output.out + output.err
