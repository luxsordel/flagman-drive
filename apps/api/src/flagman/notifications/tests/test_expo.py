import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import httpx2
import pytest
import structlog

from flagman.notifications.domain import PushContent
from flagman.notifications.expo import MAX_BATCH, MAX_RECEIPTS_BATCH, ExpoPushProvider
from flagman.notifications.ports import (
    PushMessage,
    PushReceipt,
    PushReceiptStatus,
    PushTicket,
    PushTicketStatus,
)

CONTENT = PushContent(title="Title", body="Body", data={"event": "trip_finished"})
EXPIRES_AT = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)


def _message(n: int = 0) -> PushMessage:
    return PushMessage(token=f"ExponentPushToken[token{n}]", content=CONTENT, expires_at=EXPIRES_AT)


def _provider(
    handler: Callable[[httpx2.Request], httpx2.Response], access_token: str | None = None
) -> ExpoPushProvider:
    client = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
    return ExpoPushProvider(client=client, access_token=access_token, timeout_s=5)


def _ok(request: httpx2.Request) -> httpx2.Response:
    sent = json.loads(request.content)
    return httpx2.Response(
        200, json={"data": [{"status": "ok", "id": f"ticket{n}"} for n in range(len(sent))]}
    )


async def test_sends_title_body_and_data_to_each_token() -> None:
    requests: list[Any] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(json.loads(request.content))
        assert "Authorization" not in request.headers
        return _ok(request)

    tickets = await _provider(handler).send([_message(1), _message(2)])

    assert tickets == [
        PushTicket(PushTicketStatus.OK, "ticket0"),
        PushTicket(PushTicketStatus.OK, "ticket1"),
    ]
    assert requests == [
        [
            {
                "to": f"ExponentPushToken[token{n}]",
                "title": "Title",
                "body": "Body",
                "data": {"event": "trip_finished"},
                "sound": "default",
                "priority": "high",
                "channelId": "default",
                "expiration": int(EXPIRES_AT.timestamp()),
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

    tickets = await _provider(handler).send([_message(n) for n in range(MAX_BATCH + 1)])

    assert sizes == [MAX_BATCH, 1]
    assert len(tickets) == MAX_BATCH + 1


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
        tickets = await _provider(handler).send([_message(0), _message(1)])

    assert tickets == [
        PushTicket(PushTicketStatus.DEVICE_NOT_REGISTERED),
        PushTicket(PushTicketStatus.REJECTED),
    ]
    # Expo's message quotes the token: only the error code is logged.
    assert "ExponentPushToken" not in repr(logs)


NOT_SENT = [PushTicket(PushTicketStatus.NOT_SENT)]


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

    tickets = await _provider(handler).send([_message(n) for n in range(MAX_BATCH + 1)])

    # Only the second request is to be tried again, not the hundred already delivered.
    assert [ticket.status for ticket in tickets] == [PushTicketStatus.OK] * MAX_BATCH + [
        PushTicketStatus.NOT_SENT
    ]


async def test_the_configured_timeout_holds_for_each_phase() -> None:
    # The client's own default of 5 s would otherwise cut a slow Expo short.
    def handler(request: httpx2.Request) -> httpx2.Response:
        assert request.extensions["timeout"]["read"] == 5
        return _ok(request)

    assert await _provider(handler).send([_message()]) == [
        PushTicket(PushTicketStatus.OK, "ticket0")
    ]


async def test_an_ok_ticket_without_an_id_has_no_receipt_to_ask_for() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json={"data": [{"status": "ok"}]})

    assert await _provider(handler).send([_message()]) == [PushTicket(PushTicketStatus.OK)]


def _receipts(
    answer: dict[str, Any], seen: list[Any] | None = None
) -> Callable[[httpx2.Request], httpx2.Response]:
    def handler(request: httpx2.Request) -> httpx2.Response:
        assert request.url == "https://exp.host/--/api/v2/push/getReceipts"
        if seen is not None:
            seen.append(json.loads(request.content))
        return httpx2.Response(200, json={"data": answer})

    return handler


async def test_asks_for_the_receipts_of_the_tickets() -> None:
    seen: list[Any] = []

    receipts = await _provider(_receipts({"a": {"status": "ok"}}, seen)).get_receipts(["a", "b"])

    assert seen == [{"ids": ["a", "b"]}]
    # "b" is not ready yet: it is simply not in the answer.
    assert receipts == {"a": PushReceipt(PushReceiptStatus.OK)}


async def test_receipt_errors_are_told_apart() -> None:
    answer = {
        "a": {
            "status": "error",
            "message": "ExponentPushToken[token0] is not a registered push token",
            "details": {"error": "DeviceNotRegistered"},
        },
        "b": {
            "status": "error",
            "message": "slow down",
            "details": {"error": "MessageRateExceeded"},
        },
        "c": {"status": "error", "message": "something"},
    }

    with structlog.testing.capture_logs() as logs:
        receipts = await _provider(_receipts(answer)).get_receipts(["a", "b", "c"])

    assert receipts == {
        "a": PushReceipt(PushReceiptStatus.DEVICE_NOT_REGISTERED),
        "b": PushReceipt(PushReceiptStatus.FAILED, "MessageRateExceeded"),
        "c": PushReceipt(PushReceiptStatus.FAILED),
    }
    assert "ExponentPushToken" not in repr(logs)


async def test_receipts_are_asked_for_a_thousand_at_a_time() -> None:
    sizes: list[int] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        ids = json.loads(request.content)["ids"]
        sizes.append(len(ids))
        return httpx2.Response(200, json={"data": {i: {"status": "ok"} for i in ids}})

    ids = [f"t{n}" for n in range(MAX_RECEIPTS_BATCH + 1)]
    receipts = await _provider(handler).get_receipts(ids)

    assert sizes == [MAX_RECEIPTS_BATCH, 1]
    assert receipts.keys() == set(ids)


async def test_receipts_ask_with_the_access_token() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        assert request.headers["Authorization"] == "Bearer secret-token"
        assert request.extensions["timeout"]["read"] == 5
        return httpx2.Response(200, json={"data": {}})

    assert await _provider(handler, access_token="secret-token").get_receipts(["a"]) == {}


@pytest.mark.parametrize(
    "response",
    [
        httpx2.Response(429),
        httpx2.Response(500),
        httpx2.Response(200, content=b"not json"),
        httpx2.Response(200, json={"data": []}),
        httpx2.Response(200, json={"errors": [{"code": "X"}]}),
    ],
)
async def test_no_usable_answer_leaves_every_receipt_for_later(response: httpx2.Response) -> None:
    assert await _provider(lambda request: response).get_receipts(["a"]) == {}


async def test_an_unreachable_expo_leaves_every_receipt_for_later() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("down", request=request)

    assert await _provider(handler).get_receipts(["a"]) == {}
