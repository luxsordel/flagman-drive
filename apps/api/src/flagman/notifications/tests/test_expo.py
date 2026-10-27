import json
from collections.abc import Callable
from typing import Any

import httpx2
import pytest
import structlog

from flagman.notifications.domain import PushContent
from flagman.notifications.expo import MAX_BATCH, ExpoPushProvider
from flagman.notifications.ports import PushMessage, PushTicketStatus

CONTENT = PushContent(title="Title", body="Body", data={"event": "trip_finished"})


def _message(n: int = 0) -> PushMessage:
    return PushMessage(token=f"ExponentPushToken[token{n}]", content=CONTENT)


def _provider(
    handler: Callable[[httpx2.Request], httpx2.Response], access_token: str | None = None
) -> ExpoPushProvider:
    client = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
    return ExpoPushProvider(client=client, access_token=access_token, timeout_s=5)


def _ok(request: httpx2.Request) -> httpx2.Response:
    sent = json.loads(request.content)
    return httpx2.Response(200, json={"data": [{"status": "ok", "id": "x"} for _ in sent]})


async def test_sends_title_body_and_data_to_each_token() -> None:
    requests: list[Any] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(json.loads(request.content))
        assert "Authorization" not in request.headers
        return _ok(request)

    statuses = await _provider(handler).send([_message(1), _message(2)])

    assert statuses == [PushTicketStatus.OK, PushTicketStatus.OK]
    assert requests == [
        [
            {
                "to": f"ExponentPushToken[token{n}]",
                "title": "Title",
                "body": "Body",
                "data": {"event": "trip_finished"},
                "sound": "default",
            }
            for n in (1, 2)
        ]
    ]


async def test_access_token_goes_in_the_header() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        assert request.headers["Authorization"] == "Bearer secret-token"
        return _ok(request)

    await _provider(handler, access_token="secret-token").send([_message()])


async def test_large_batches_are_split() -> None:
    sizes: list[int] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        sizes.append(len(json.loads(request.content)))
        return _ok(request)

    statuses = await _provider(handler).send([_message(n) for n in range(MAX_BATCH + 1)])

    assert sizes == [MAX_BATCH, 1]
    assert len(statuses) == MAX_BATCH + 1


async def test_ticket_errors_are_told_apart() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            200,
            json={
                "data": [
                    {
                        "status": "error",
                        "message": "ExponentPushToken[token0] is not a registered push token",
                        "details": {"error": "DeviceNotRegistered"},
                    },
                    {
                        "status": "error",
                        "message": "too big",
                        "details": {"error": "MessageTooBig"},
                    },
                ]
            },
        )

    with structlog.testing.capture_logs() as logs:
        statuses = await _provider(handler).send([_message(0), _message(1)])

    assert statuses == [PushTicketStatus.DEVICE_NOT_REGISTERED, PushTicketStatus.REJECTED]
    # Expo's message quotes the token: only the error code is logged.
    assert "ExponentPushToken" not in repr(logs)


NOT_SENT = [PushTicketStatus.NOT_SENT]


@pytest.mark.parametrize("status", [401, 429, 500, 503])
async def test_a_refused_request_sends_nothing(status: int) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(status, json={"errors": [{"code": "X", "message": "no"}]})

    assert await _provider(handler).send([_message()]) == NOT_SENT


async def test_an_unreachable_expo_sends_nothing() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("down", request=request)

    assert await _provider(handler).send([_message()]) == NOT_SENT


@pytest.mark.parametrize(
    "body",
    [b"not json", b'{"data": "x"}', b'{"data": []}', b'{"errors": []}'],
)
async def test_a_garbled_answer_counts_as_not_sent(body: bytes) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, content=body)

    assert await _provider(handler).send([_message()]) == NOT_SENT


async def test_a_failed_request_leaves_the_earlier_ones_delivered() -> None:
    requests = 0

    def handler(request: httpx2.Request) -> httpx2.Response:
        nonlocal requests
        requests += 1
        return _ok(request) if requests == 1 else httpx2.Response(429)

    statuses = await _provider(handler).send([_message(n) for n in range(MAX_BATCH + 1)])

    # Only the second request is to be tried again, not the hundred already delivered.
    assert statuses == [PushTicketStatus.OK] * MAX_BATCH + NOT_SENT
