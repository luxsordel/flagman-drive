"""Signature check for every incoming request.

Done as ASGI middleware rather than a FastAPI dependency so that it runs before the body is parsed
and cannot be forgotten on a new route: everything except the explicit exempt paths is protected.
"""

from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from car_emulator.ports import Clock
from car_emulator.signing import SIGNATURE_HEADER, TIMESTAMP_HEADER, SignatureError, verify

MAX_BODY_BYTES = 64 * 1024


class _BodyTooLargeError(Exception):
    pass


async def _read_body(receive: Receive) -> bytes:
    chunks: list[bytes] = []
    size = 0
    while True:
        message = await receive()
        if message["type"] != "http.request":
            break
        chunk: bytes = message.get("body", b"")
        size += len(chunk)
        if size > MAX_BODY_BYTES:
            raise _BodyTooLargeError
        chunks.append(chunk)
        if not message.get("more_body", False):
            break
    return b"".join(chunks)


def _request_target(scope: Scope) -> bytes:
    raw_path: bytes = scope.get("raw_path") or scope["path"].encode()
    query: bytes = scope.get("query_string", b"")
    return raw_path + b"?" + query if query else raw_path


class SignatureMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        *,
        secret: bytes,
        tolerance_s: int,
        clock: Clock,
        exempt_paths: frozenset[str],
    ) -> None:
        self._app = app
        self._secret = secret
        self._tolerance_s = tolerance_s
        self._clock = clock
        self._exempt_paths = exempt_paths

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["path"] in self._exempt_paths:
            await self._app(scope, receive, send)
            return

        try:
            body = await _read_body(receive)
        except _BodyTooLargeError:
            await JSONResponse({"detail": "body_too_large"}, status_code=413)(scope, receive, send)
            return

        headers = Headers(scope=scope)
        try:
            verify(
                self._secret,
                timestamp_header=headers.get(TIMESTAMP_HEADER),
                signature_header=headers.get(SIGNATURE_HEADER),
                method=scope["method"],
                target=_request_target(scope),
                body=body,
                now=self._clock(),
                tolerance_s=self._tolerance_s,
            )
        except SignatureError:
            # The reason is not returned to the caller so it cannot be used to probe the check.
            await JSONResponse({"detail": "invalid_signature"}, status_code=401)(
                scope, receive, send
            )
            return

        body_sent = False

        async def replay_body() -> Message:
            nonlocal body_sent
            if body_sent:
                return await receive()
            body_sent = True
            return {"type": "http.request", "body": body, "more_body": False}

        await self._app(scope, replay_body, send)
