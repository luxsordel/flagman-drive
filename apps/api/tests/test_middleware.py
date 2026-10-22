import json
import uuid
from http import HTTPStatus
from typing import Any

import pytest
from fastapi.testclient import TestClient

from conftest import SettingsFactory
from flagman.core.app import create_app

ADMIN_ORIGIN = "http://localhost:5173"


@pytest.fixture
def client(make_settings: SettingsFactory) -> TestClient:
    return TestClient(create_app(make_settings(api_cors_origins=[ADMIN_ORIGIN])))


def test_request_id_is_generated_when_missing(client: TestClient) -> None:
    response = client.get("/health")

    assert uuid.UUID(response.headers["x-request-id"])


def test_valid_request_id_from_the_client_is_kept(client: TestClient) -> None:
    request_id = str(uuid.uuid4())

    response = client.get("/health", headers={"X-Request-ID": request_id})

    assert response.headers["x-request-id"] == request_id


def test_arbitrary_request_id_from_the_client_is_replaced(client: TestClient) -> None:
    response = client.get("/health", headers={"X-Request-ID": "<script>alert(1)</script>"})

    assert uuid.UUID(response.headers["x-request-id"])


def test_security_headers_are_set(client: TestClient) -> None:
    headers = client.get("/health").headers

    assert headers["x-content-type-options"] == "nosniff"
    assert headers["referrer-policy"] == "no-referrer"
    assert headers["x-frame-options"] == "DENY"
    assert headers["content-security-policy"] == "default-src 'none'; frame-ancestors 'none'"
    assert "strict-transport-security" not in headers


def test_swagger_ui_is_exempt_from_the_strict_csp(client: TestClient) -> None:
    response = client.get("/docs")

    assert response.status_code == HTTPStatus.OK
    assert "content-security-policy" not in response.headers
    assert response.headers["x-frame-options"] == "DENY"


@pytest.mark.parametrize(
    "sms",
    [
        {"sms_backend": None},
        {"sms_backend": "fixed", "sms_fixed_code": "482913", "sms_fixed_phones": ["+375291111111"]},
    ],
)
def test_prod_enables_hsts_and_hides_the_docs(
    make_settings: SettingsFactory, sms: dict[str, Any]
) -> None:
    # Whatever the pilot does instead of SMS, it must not cost the prod protections.
    client = TestClient(create_app(make_settings(api_env="prod", **sms)))

    assert client.get("/health").headers["strict-transport-security"].startswith("max-age=")
    assert client.get("/docs").status_code == HTTPStatus.NOT_FOUND
    assert client.get("/openapi.json").status_code == HTTPStatus.NOT_FOUND


def test_cors_allows_configured_origin(client: TestClient) -> None:
    response = client.options(
        "/health",
        headers={"Origin": ADMIN_ORIGIN, "Access-Control-Request-Method": "GET"},
    )

    assert response.status_code == HTTPStatus.OK
    assert response.headers["access-control-allow-origin"] == ADMIN_ORIGIN
    assert response.headers["access-control-allow-credentials"] == "true"


def test_cors_lets_the_browser_read_retry_after(client: TestClient) -> None:
    response = client.get("/health", headers={"Origin": ADMIN_ORIGIN})

    exposed = response.headers["access-control-expose-headers"].lower().split(", ")
    assert "retry-after" in exposed


def test_cors_rejects_unknown_origin(client: TestClient) -> None:
    response = client.options(
        "/health",
        headers={"Origin": "https://evil.example.com", "Access-Control-Request-Method": "GET"},
    )

    assert "access-control-allow-origin" not in response.headers


def test_access_log_is_json_with_request_id_and_without_query_string(
    make_settings: SettingsFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    # The app is created inside the test so the log handler writes to the captured stdout.
    client = TestClient(create_app(make_settings()))

    response = client.get("/health", params={"phone": "+375291234567"})

    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line]
    access = [line for line in lines if line.get("event") == "request"]
    assert access == [
        {
            "event": "request",
            "method": "GET",
            "path": "/health",
            "status": 200,
            "duration_ms": access[0]["duration_ms"],
            "request_id": response.headers["x-request-id"],
            "level": "info",
            "logger": "flagman.core.middleware",
            "timestamp": access[0]["timestamp"],
        }
    ]
    assert "375291234567" not in capsys.readouterr().out
