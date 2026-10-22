"""Single error format for the whole API: RFC 9457 problem details, see docs/adr/0006."""

from collections.abc import Mapping
from http import HTTPStatus
from typing import Any, ClassVar, cast

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ConfigDict
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import JSONResponse
from starlette.types import HTTPExceptionHandler

PROBLEM_CONTENT_TYPE = "application/problem+json"
PROBLEM_TYPE_PREFIX = "urn:flagman:problem:"
VALIDATION_ERROR_TYPE = f"{PROBLEM_TYPE_PREFIX}validation-error"

logger = structlog.get_logger(__name__)


class FieldError(BaseModel):
    model_config = ConfigDict(extra="forbid")

    loc: list[str | int]
    message: str
    type: str


class Problem(BaseModel):
    """Error response body. Extension members are `request_id` and `errors`."""

    model_config = ConfigDict(extra="forbid")

    type: str = "about:blank"
    title: str
    status: int
    detail: str | None = None
    request_id: str | None = None
    errors: list[FieldError] | None = None


class AppError(Exception):
    """Base class for expected business errors; subclasses set the class attributes.

    `detail` is sent to the client as is, so it must not contain personal data or internals.
    """

    status: ClassVar[int] = HTTPStatus.BAD_REQUEST
    slug: ClassVar[str] = "bad-request"
    title: ClassVar[str] = "Bad request"

    def __init__(self, detail: str | None = None) -> None:
        super().__init__(detail or self.title)
        self.detail = detail

    @property
    def type(self) -> str:
        return f"{PROBLEM_TYPE_PREFIX}{self.slug}"


PROBLEM_RESPONSES: dict[int | str, dict[str, Any]] = {
    status: {"model": Problem}
    for status in (HTTPStatus.UNPROCESSABLE_ENTITY, HTTPStatus.INTERNAL_SERVER_ERROR)
}
"""OpenAPI entries so the generated client knows the error schema."""

_PROBLEM_REF = "#/components/schemas/Problem"


def use_problem_media_type(app: FastAPI) -> None:
    """FastAPI always documents a response `model` as application/json; error responses are
    actually sent as application/problem+json, and the generated client must see that."""
    generate = app.openapi

    def openapi() -> dict[str, Any]:
        if app.openapi_schema is not None:
            return app.openapi_schema
        # generate() caches the dict it returns in app.openapi_schema, so it is edited in place.
        schema = generate()
        for path_item in schema.get("paths", {}).values():
            for operation in path_item.values():
                for response in operation.get("responses", {}).values():
                    content = response.get("content", {})
                    json_body = content.get("application/json")
                    if json_body and json_body.get("schema", {}).get("$ref") == _PROBLEM_REF:
                        content[PROBLEM_CONTENT_TYPE] = content.pop("application/json")
        return schema

    app.openapi = openapi  # type: ignore[method-assign]


def get_request_id(request: Request) -> str | None:
    request_id = getattr(request.state, "request_id", None)
    return request_id if isinstance(request_id, str) else None


def problem_response(problem: Problem, headers: Mapping[str, str] | None = None) -> JSONResponse:
    return JSONResponse(
        problem.model_dump(mode="json", exclude_none=True),
        status_code=problem.status,
        headers=headers,
        media_type=PROBLEM_CONTENT_TYPE,
    )


def internal_error_problem(request_id: str | None) -> Problem:
    status = HTTPStatus.INTERNAL_SERVER_ERROR
    return Problem(title=status.phrase, status=status, request_id=request_id)


async def _app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    problem = Problem(
        type=exc.type,
        title=exc.title,
        status=exc.status,
        detail=exc.detail,
        request_id=get_request_id(request),
    )
    return problem_response(problem)


async def _validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    # Only location, message and type: pydantic's `input` and `ctx` may echo personal data back.
    errors = [
        FieldError(loc=list(error["loc"]), message=error["msg"], type=error["type"])
        for error in exc.errors()
    ]
    status = HTTPStatus.UNPROCESSABLE_ENTITY
    problem = Problem(
        type=VALIDATION_ERROR_TYPE,
        title="Validation error",
        status=status,
        detail="The request is invalid, see `errors`.",
        request_id=get_request_id(request),
        errors=errors,
    )
    return problem_response(problem)


async def _http_error_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    status = HTTPStatus(exc.status_code)
    detail = exc.detail if isinstance(exc.detail, str) and exc.detail != status.phrase else None
    problem = Problem(
        title=status.phrase,
        status=status,
        detail=detail,
        request_id=get_request_id(request),
    )
    return problem_response(problem, headers=exc.headers)


def register_error_handlers(app: FastAPI) -> None:
    """Unhandled exceptions are turned into 500 by RequestContextMiddleware, not here,
    so that the response still passes through the request id and security header middleware."""
    # Starlette picks the handler by exception class, so each handler only ever receives
    # its own type; the casts only widen the parameter type for the registry signature.
    app.add_exception_handler(AppError, cast(HTTPExceptionHandler, _app_error_handler))
    app.add_exception_handler(
        RequestValidationError, cast(HTTPExceptionHandler, _validation_error_handler)
    )
    app.add_exception_handler(
        StarletteHTTPException, cast(HTTPExceptionHandler, _http_error_handler)
    )
