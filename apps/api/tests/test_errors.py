from http import HTTPStatus
from typing import Annotated, Literal

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic_core import PydanticCustomError

from conftest import SettingsFactory
from flagman.core.app import create_app
from flagman.core.errors import (
    CUSTOM_ERROR_MESSAGE,
    PROBLEM_CONTENT_TYPE,
    VALIDATION_ERROR_TYPE,
    AppError,
    RateLimitedError,
)

PHONE = "+375291234567"
DOCUMENT_NUMBER = "MP1234567"


class CarNotFoundError(AppError):
    status = HTTPStatus.NOT_FOUND
    slug = "car-not-found"
    title = "Car not found"


class SignUpRequest(BaseModel):
    """Validators that put the input into their messages, as is easy to do by mistake."""

    model_config = ConfigDict(extra="forbid")

    phone: str
    document_number: str
    age: int

    @field_validator("phone")
    @classmethod
    def _check_phone(cls, value: str) -> str:
        raise ValueError(f"not a Belarusian phone: {value!r}")

    @field_validator("document_number")
    @classmethod
    def _check_document_number(cls, value: str) -> str:
        raise PydanticCustomError(
            "document_number_invalid", "bad document {number}", {"number": value}
        )

    @field_validator("age")
    @classmethod
    def _check_age(cls, value: int) -> int:
        assert value >= 18, f"too young: {value}"
        return value


class CardPayment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["card"]


class WalletPayment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["wallet"]


class PaymentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    method: Annotated[CardPayment | WalletPayment, Field(discriminator="kind")]


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

    @app.post("/test/sign-up")
    async def sign_up(body: SignUpRequest) -> None:
        pass

    @app.post("/test/payment")
    async def pay(body: PaymentRequest) -> None:
        pass

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
    # Pydantic's own messages are templates over the schema, not the input, so they are kept.
    assert body["errors"][0]["message"].startswith("Input should be a valid integer")


def test_validation_error_hides_messages_written_by_our_validators(client: TestClient) -> None:
    response = client.post(
        "/test/sign-up",
        json={"phone": PHONE, "document_number": DOCUMENT_NUMBER, "age": 16},
    )

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert response.json()["errors"] == [
        {"loc": ["body", "phone"], "message": CUSTOM_ERROR_MESSAGE, "type": "value_error"},
        {
            "loc": ["body", "document_number"],
            "message": CUSTOM_ERROR_MESSAGE,
            "type": "document_number_invalid",
        },
        {"loc": ["body", "age"], "message": CUSTOM_ERROR_MESSAGE, "type": "assertion_error"},
    ]
    assert PHONE not in response.text
    assert DOCUMENT_NUMBER not in response.text


def test_validation_error_hides_the_unknown_tag_of_a_tagged_union(client: TestClient) -> None:
    # Pydantic's own union_tag_invalid message quotes the tag the client sent.
    response = client.post("/test/payment", json={"method": {"kind": PHONE}})

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert response.json()["errors"] == [
        {"loc": ["body", "method"], "message": CUSTOM_ERROR_MESSAGE, "type": "union_tag_invalid"}
    ]
    assert PHONE not in response.text


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
