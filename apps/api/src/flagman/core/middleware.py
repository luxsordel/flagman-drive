import time
import uuid
from collections.abc import Collection

import structlog
from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from flagman.core.errors import internal_error_problem, problem_response

REQUEST_ID_HEADER = "X-Request-ID"

logger = structlog.get_logger(__name__)


def _parse_request_id(raw: str | None) -> str | None:
    # Only UUIDs are accepted from the client: arbitrary strings would end up in our logs.
    if raw is None:
        return None
    try:
        return str(uuid.UUID(raw))
    except ValueError:
        return None


class RequestContextMiddleware:
    """Assigns a request id, writes one access log line per request and turns
    unhandled exceptions into a problem+json 500 that still carries the request id."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = _parse_request_id(Headers(scope=scope).get(REQUEST_ID_HEADER)) or str(
            uuid.uuid4()
        )
        scope.setdefault("state", {})["request_id"] = request_id
        status_code = 500
        response_started = False

        async def send_with_request_id(message: Message) -> None:
            nonlocal status_code, response_started
            if message["type"] == "http.response.start":
                response_started = True
                status_code = message["status"]
                MutableHeaders(scope=message)[REQUEST_ID_HEADER] = request_id
            await send(message)

        started_at = time.perf_counter()
        with structlog.contextvars.bound_contextvars(request_id=request_id):
            try:
                await self.app(scope, receive, send_with_request_id)
            except Exception:
                logger.exception("unhandled_error")
                if response_started:
                    raise
                response = problem_response(internal_error_problem(request_id))
                await response(scope, receive, send_with_request_id)
            finally:
                logger.info(
                    "request",
                    method=scope["method"],
                    path=scope["path"],
                    status=status_code,
                    duration_ms=round((time.perf_counter() - started_at) * 1000, 1),
                )


class SecurityHeadersMiddleware:
    """Security headers from docs/03-security.md for every HTTP response."""

    def __init__(self, app: ASGIApp, *, hsts: bool, csp_exempt_paths: Collection[str] = ()) -> None:
        self.app = app
        self.hsts = hsts
        self.csp_exempt_paths = frozenset(csp_exempt_paths)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # Swagger UI loads its scripts from a CDN, so its pages cannot have the strict policy.
        strict_csp = scope["path"] not in self.csp_exempt_paths

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["X-Content-Type-Options"] = "nosniff"
                headers["Referrer-Policy"] = "no-referrer"
                headers["X-Frame-Options"] = "DENY"
                if strict_csp:
                    headers["Content-Security-Policy"] = (
                        "default-src 'none'; frame-ancestors 'none'"
                    )
                if self.hsts:
                    headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
            await send(message)

        await self.app(scope, receive, send_with_headers)
