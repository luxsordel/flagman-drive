import uuid
from http import HTTPStatus
from typing import Any

import pytest
from fastapi.testclient import TestClient

from conftest import SettingsFactory
from flagman.core.app import create_app
from flagman.core.errors import PROBLEM_CONTENT_TYPE, VALIDATION_ERROR_TYPE

PHONE = "+375291234567"
REFRESH_TOKEN = "r" * 43


@pytest.fixture
def client(make_settings: SettingsFactory) -> TestClient:
    # Not entered as a context manager: the contract does not need the database or Redis.
    return TestClient(create_app(make_settings()))


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("/auth/otp/request", {"phone": PHONE}),
        ("/auth/otp/verify", {"phone": PHONE, "code": "123456", "device_id": str(uuid.uuid4())}),
        ("/auth/token/refresh", {"refresh_token": REFRESH_TOKEN}),
        ("/auth/logout", {"refresh_token": REFRESH_TOKEN}),
    ],
)
def test_valid_requests_reach_the_stub(client: TestClient, path: str, body: dict[str, Any]) -> None:
    response = client.post(path, json=body)

    assert response.status_code == HTTPStatus.NOT_IMPLEMENTED
    assert response.headers["content-type"] == PROBLEM_CONTENT_TYPE


@pytest.mark.parametrize(
    "phone",
    [
        "375291234567",  # no plus
        "+375 29 123 45 67",  # not normalized
        "+375171234567",  # Minsk landline, cannot receive SMS
        "+37529123456",  # one digit short
        "+79161234567",  # not Belarus
        "+375２９1234567",  # full-width digits  # noqa: RUF001
    ],
)
def test_otp_request_rejects_phones_outside_the_pilot(client: TestClient, phone: str) -> None:
    response = client.post("/auth/otp/request", json={"phone": phone})

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert response.json()["type"] == VALIDATION_ERROR_TYPE
    # The phone is personal data: it is never echoed back.
    assert phone not in response.text


@pytest.mark.parametrize("code", ["12345", "1234567", "12345a", "１２３４５６"])  # noqa: RUF001
def test_otp_verify_accepts_only_six_ascii_digits(client: TestClient, code: str) -> None:
    body = {"phone": PHONE, "code": code, "device_id": str(uuid.uuid4())}

    response = client.post("/auth/otp/verify", json=body)

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert response.json()["errors"][0]["loc"] == ["body", "code"]


def test_otp_verify_requires_a_uuid_device_id(client: TestClient) -> None:
    body = {"phone": PHONE, "code": "123456", "device_id": "my-phone"}

    response = client.post("/auth/otp/verify", json=body)

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert response.json()["errors"][0]["loc"] == ["body", "device_id"]


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("/auth/otp/request", {"phone": PHONE, "user_id": str(uuid.uuid4())}),
        ("/auth/token/refresh", {"refresh_token": REFRESH_TOKEN, "device_id": "x"}),
    ],
)
def test_unknown_fields_are_rejected(client: TestClient, path: str, body: dict[str, Any]) -> None:
    response = client.post(path, json=body)

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert response.json()["errors"][0]["type"] == "extra_forbidden"


def test_refresh_rejects_tokens_of_absurd_length(client: TestClient) -> None:
    for token in ("short", "x" * 1000):
        response = client.post("/auth/token/refresh", json={"refresh_token": token})

        assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY


def test_openapi_documents_the_error_responses(client: TestClient) -> None:
    paths = client.app.openapi()["paths"]  # type: ignore[attr-defined]
    problem = {PROBLEM_CONTENT_TYPE: {"schema": {"$ref": "#/components/schemas/Problem"}}}

    rate_limited = paths["/auth/otp/request"]["post"]["responses"]["429"]
    assert rate_limited["content"] == problem
    assert "Retry-After" in rate_limited["headers"]

    verify = paths["/auth/otp/verify"]["post"]["responses"]
    assert verify["400"]["content"] == problem
    assert "otp-expired" in verify["400"]["description"]

    refresh = paths["/auth/token/refresh"]["post"]["responses"]
    assert refresh["401"]["content"] == problem
