from http import HTTPStatus

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from conftest import SettingsFactory
from flagman.core.app import create_app
from flagman.core.errors import (
    PROBLEM_CONTENT_TYPE,
    VALIDATION_ERROR_TYPE,
    AppError,
    RateLimitedError,
)


class CarNotFoundError(AppError):
    status = HTTPStatus.NOT_FOUND
    slug = "car-not-found"
    title = "Car not found"


@pytest.fixture
def app(make_settings: SettingsFactory) -> FastAPI:
    app = create_app(make_settings())

    @app.get("/test/app-error")
    async def raise_app_error() -> None:
        raise CarNotFoundError("No car with this id.")

    @app.get("/test/rate-limited")
    async def raise_rate_limited() -> None:
        raise RateLimitedError(retry_after_seconds=42)

    @app.get("/test/http-error")
    async def raise_http_error() -> None:
        raise HTTPException(status_code=HTTPStatus.FORBIDDEN, detail="Not your rental.")

    @app.get("/test/crash")
    async def crash() -> None:
        raise RuntimeError("secret internals: postgres://user:password@db")

    @app.get("/test/validation")
    async def validate(phone: int) -> int:
        return phone

    return app


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


def test_app_error_is_a_problem_with_its_own_type(client: TestClient) -> None:
    response = client.get("/test/app-error")

    assert response.status_code == HTTPStatus.NOT_FOUND
    assert response.headers["content-type"] == PROBLEM_CONTENT_TYPE
    assert response.json() == {
        "type": "urn:flagman:problem:car-not-found",
        "title": "Car not found",
        "status": 404,
        "detail": "No car with this id.",
        "request_id": response.headers["x-request-id"],
    }


def test_rate_limited_error_tells_when_to_retry(client: TestClient) -> None:
    response = client.get("/test/rate-limited")

    assert response.status_code == HTTPStatus.TOO_MANY_REQUESTS
    assert response.headers["content-type"] == PROBLEM_CONTENT_TYPE
    assert response.headers["retry-after"] == "42"
    assert response.json()["type"] == "urn:flagman:problem:rate-limited"


def test_http_exception_uses_about_blank_and_the_status_phrase(client: TestClient) -> None:
    response = client.get("/test/http-error")

    assert response.status_code == HTTPStatus.FORBIDDEN
    assert response.headers["content-type"] == PROBLEM_CONTENT_TYPE
    body = response.json()
    assert body["type"] == "about:blank"
    assert body["title"] == "Forbidden"
    assert body["detail"] == "Not your rental."


def test_unknown_route_is_a_problem(client: TestClient) -> None:
    response = client.get("/no-such-route")

    assert response.status_code == HTTPStatus.NOT_FOUND
    assert response.headers["content-type"] == PROBLEM_CONTENT_TYPE
    assert response.json()["title"] == "Not Found"
    assert "detail" not in response.json()


def test_wrong_method_keeps_the_allow_header(client: TestClient) -> None:
    response = client.post("/health")

    assert response.status_code == HTTPStatus.METHOD_NOT_ALLOWED
    assert response.headers["allow"] == "GET"
    assert response.headers["content-type"] == PROBLEM_CONTENT_TYPE


def test_validation_error_lists_fields_without_echoing_the_input(client: TestClient) -> None:
    response = client.get("/test/validation", params={"phone": "+375291234567x"})

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert response.headers["content-type"] == PROBLEM_CONTENT_TYPE
    body = response.json()
    assert body["type"] == VALIDATION_ERROR_TYPE
    assert body["errors"] == [
        {"loc": ["query", "phone"], "message": body["errors"][0]["message"], "type": "int_parsing"}
    ]
    assert "375291234567" not in response.text


def test_unhandled_exception_is_a_generic_500_with_request_id(client: TestClient) -> None:
    response = client.get("/test/crash")

    assert response.status_code == HTTPStatus.INTERNAL_SERVER_ERROR
    assert response.headers["content-type"] == PROBLEM_CONTENT_TYPE
    assert response.json() == {
        "type": "about:blank",
        "title": "Internal Server Error",
        "status": 500,
        "request_id": response.headers["x-request-id"],
    }
    assert "password" not in response.text
    # The 500 is produced inside the middleware stack, so it still gets the security headers.
    assert response.headers["x-content-type-options"] == "nosniff"


def test_openapi_documents_errors_as_problem_json(app: FastAPI) -> None:
    responses = app.openapi()["paths"]["/test/validation"]["get"]["responses"]

    for status in ("422", "500"):
        assert responses[status]["content"] == {
            PROBLEM_CONTENT_TYPE: {"schema": {"$ref": "#/components/schemas/Problem"}}
        }
