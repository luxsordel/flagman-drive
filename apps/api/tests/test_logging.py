import asyncio
import json
import logging
import socket
from collections.abc import Iterator

import pytest
import structlog
import uvicorn
from starlette.types import Receive, Scope, Send
from websockets.asyncio.client import connect
from websockets.exceptions import InvalidStatus

from flagman.core.logging import configure_logging

CLIENT_HOST = "127.0.0.1"
_LOGGERS = ("", "uvicorn", "uvicorn.error", "uvicorn.access")


@pytest.fixture(autouse=True)
def _restore_logging() -> Iterator[None]:
    """configure_logging and uvicorn rewire global loggers; later tests must not write to the
    stdout this test captured."""
    saved = [
        (lg, lg.handlers[:], lg.filters[:], lg.level, lg.propagate, lg.disabled)
        for lg in map(logging.getLogger, _LOGGERS)
    ]
    yield
    for lg, handlers, filters, level, propagate, disabled in saved:
        lg.handlers, lg.filters, lg.level = handlers, filters, level
        lg.propagate, lg.disabled = propagate, disabled


async def _stream(scope: Scope, receive: Receive, send: Send) -> None:
    assert scope["type"] == "websocket"
    await receive()
    if scope["path"] == "/rejected":
        await send({"type": "websocket.close", "code": 1008})
        return
    await send({"type": "websocket.accept"})
    await send({"type": "websocket.send", "text": "hi"})
    await send({"type": "websocket.close", "code": 1000})


def _app_factory() -> object:
    # The API is started by uvicorn with --factory, so its logging is configured after
    # uvicorn has installed its own: the same order as here.
    configure_logging("INFO")
    return _stream


async def _open_socket(ws: str, path: str) -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind((CLIENT_HOST, 0))
    # Listening before uvicorn starts queues the client's connection until the server takes it.
    sock.listen()
    url = f"ws://{CLIENT_HOST}:{sock.getsockname()[1]}{path}"
    config = uvicorn.Config(
        _app_factory, factory=True, ws=ws, lifespan="off", access_log=False, server_header=False
    )
    server = uvicorn.Server(config)
    serving = asyncio.create_task(server.serve(sockets=[sock]))
    try:
        if path.startswith("/rejected"):
            with pytest.raises(InvalidStatus) as rejected:
                await connect(url)
            assert rejected.value.response.status_code == 403
        else:
            async with connect(url) as client:
                assert await client.recv() == "hi"
    finally:
        server.should_exit = True
        await serving


@pytest.mark.parametrize("ws", ["websockets", "websockets-sansio"])
@pytest.mark.parametrize(
    ("path", "logged_path", "outcome"),
    [
        ("/stream", "/stream", "[accepted]"),
        ("/rejected", "/rejected", "403"),
        # A client may put a token in the query string; it must not reach the log.
        ("/stream?access_token=at-secret", "/stream", "[accepted]"),
        ("/rejected?access_token=at-secret", "/rejected", "403"),
    ],
)
async def test_websocket_handshake_is_logged_without_the_client_address(
    capsys: pytest.CaptureFixture[str], ws: str, path: str, logged_path: str, outcome: str
) -> None:
    await _open_socket(ws, path)

    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line]
    handshakes = [
        line["event"]
        for line in lines
        if line["logger"] == "uvicorn.error" and "WebSocket" in line["event"]
    ]
    assert handshakes == [f'"WebSocket {logged_path}" {outcome}']


def test_personal_data_and_secrets_are_redacted_from_logs(
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_logging("INFO")
    structlog.contextvars.bind_contextvars(email="anna@example.com")
    try:
        structlog.get_logger("flagman.test").info(
            "signed_in",
            phone="+375291234567",
            refresh_token="rt-secret",
            Password="hunter2",
            headers={"Authorization": "Bearer at-secret", "accept": "application/json"},
            rental_id="r-1",
            code="LOW_BATTERY",
            first_name="Anna",
            birth_date="1990-01-01",
            lat=53.9,
            lon=27.56,
        )
        # Records of libraries pass through the same chain, with the bound context merged in.
        logging.getLogger("uvicorn.error").info("library record")
    finally:
        structlog.contextvars.clear_contextvars()

    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line]
    signed_in, library = lines
    assert signed_in["phone"] == "[redacted]"
    assert signed_in["refresh_token"] == "[redacted]"
    assert signed_in["Password"] == "[redacted]"
    assert signed_in["email"] == "[redacted]"
    assert signed_in["headers"] == {"Authorization": "[redacted]", "accept": "application/json"}
    for key in ("first_name", "birth_date", "lat", "lon"):
        assert signed_in[key] == "[redacted]"
    assert signed_in["rental_id"] == "r-1"
    # Error and catalog codes are not secrets; the sign-in code never reaches a log in prod.
    assert signed_in["code"] == "LOW_BATTERY"
    assert library["event"] == "library record"
    assert library["email"] == "[redacted]"
