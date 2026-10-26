import asyncio
import json
import time
import uuid
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime
from typing import Any

import httpx2
import pytest

from flagman.core.geo import GeoPoint
from flagman.fleet.domain import (
    CarCommand,
    CarCommandDuplicateError,
    CarCommandRejectedError,
    CarState,
    TelematicsTimeoutError,
    TelematicsUnavailableError,
)
from flagman.fleet.emulator import EmulatorTelematicsGateway
from flagman.fleet.signing import SIGNATURE_HEADER, TIMESTAMP_HEADER, SignedRequest, verify

SECRET = b"c" * 40
NOW = datetime(2026, 10, 26, 12, 0, tzinfo=UTC)
BASE_URL = "http://emulator.test:8100"
TELEMATICS_ID = uuid.UUID("3f1c6a52-6f0e-4b8e-9d55-2f0b7c9a1e10")
COMMAND_ID = uuid.UUID("9b2d1f4e-0c3a-4e6b-8f7d-5a1c2e3b4d5f")

Handler = Callable[[httpx2.Request], httpx2.Response]
AsyncHandler = Callable[[httpx2.Request], Coroutine[None, None, httpx2.Response]]


def _car_state(**overrides: Any) -> dict[str, Any]:
    """car_emulator.schemas.CarState as the emulator answers it."""
    return {
        "id": str(TELEMATICS_ID),
        "lat": 53.9006,
        "lon": 27.559,
        "battery_pct": 80,
        "doors_locked": False,
        "ignition_on": False,
        "engine_locked": False,
        "updated_at": NOW.isoformat(),
    } | overrides


def _send(
    handler: Handler | AsyncHandler, base_url: str = BASE_URL, timeout_s: float = 5.0
) -> CarState | None:
    async def send() -> CarState | None:
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
            gateway = EmulatorTelematicsGateway(
                client, base_url, SECRET, timeout_s=timeout_s, clock=lambda: NOW
            )
            return await gateway.send(TELEMATICS_ID, COMMAND_ID, CarCommand.OPEN)

    return asyncio.run(send())


def _answer(status: int, body: Any) -> Handler:
    return lambda request: httpx2.Response(status, json=body)


def test_a_command_is_signed_for_its_car_and_carries_its_id() -> None:
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(200, json={"command_id": str(COMMAND_ID), "car": _car_state()})

    _send(handler)

    request = seen[0]
    assert request.method == "POST"
    assert str(request.url) == f"{BASE_URL}/cars/{TELEMATICS_ID}/commands"
    assert json.loads(request.content) == {"command_id": str(COMMAND_ID), "type": "open"}
    # The emulator's own check: the same function the API uses for telemetry it receives.
    verify(
        SECRET,
        SignedRequest(
            method="POST",
            target=request.url.raw_path,
            body=request.content,
            timestamp_header=request.headers.get(TIMESTAMP_HEADER),
            signature_header=request.headers.get(SIGNATURE_HEADER),
        ),
        now=NOW,
        tolerance_s=300,
    )


def test_a_path_in_the_base_url_is_signed_too() -> None:
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(200, json={"command_id": str(COMMAND_ID), "car": _car_state()})

    _send(handler, base_url=f"{BASE_URL}/telematics")

    assert seen[0].url.raw_path == f"/telematics/cars/{TELEMATICS_ID}/commands".encode()


def test_the_car_state_after_the_command_is_returned() -> None:
    answer = {"command_id": str(COMMAND_ID), "car": _car_state(engine_locked=True)}

    state = _send(_answer(200, answer))

    assert state == CarState(
        location=GeoPoint(lat=53.9006, lon=27.559),
        battery_pct=80,
        doors_locked=False,
        ignition_on=False,
        engine_locked=True,
        recorded_at=NOW,
    )


def test_a_refusal_carries_the_cars_reason() -> None:
    with pytest.raises(CarCommandRejectedError) as refused:
        _send(_answer(409, {"detail": "command_rejected:ignition_on"}))

    assert refused.value.reason == "ignition_on"
    assert refused.value.detail == "ignition_on"


def test_a_strange_reason_does_not_reach_the_client() -> None:
    with pytest.raises(CarCommandRejectedError) as refused:
        _send(_answer(409, {"detail": "command_rejected:<script>alert(1)</script>"}))

    assert refused.value.reason == "unknown"


def test_a_seen_command_id_is_a_duplicate() -> None:
    with pytest.raises(CarCommandDuplicateError):
        _send(_answer(409, {"detail": "duplicate_command"}))


@pytest.mark.parametrize(
    "handler",
    [
        _answer(401, {"detail": "invalid_signature"}),
        _answer(404, {"detail": "car_not_found"}),
        _answer(409, {"detail": "something_else"}),
        _answer(422, {"detail": "validation"}),
    ],
    ids=["bad-secret", "unknown-car", "unknown-conflict", "invalid-request"],
)
def test_a_refusal_before_the_command_runs_means_unavailable(handler: Handler) -> None:
    with pytest.raises(TelematicsUnavailableError):
        _send(handler)


@pytest.mark.parametrize(
    "handler",
    [
        _answer(500, "oops"),
        _answer(502, "bad gateway"),
        _answer(200, {"command_id": str(COMMAND_ID)}),
        lambda request: httpx2.Response(200, content=b"not json"),
    ],
    ids=["server-error", "bad-gateway", "no-car", "not-json"],
)
def test_an_answer_after_the_command_may_have_run_has_an_unknown_outcome(
    handler: Handler,
) -> None:
    # The emulator applies the command before it answers: a broken answer is no proof of nothing.
    with pytest.raises(TelematicsTimeoutError):
        _send(handler)


@pytest.mark.parametrize(
    "error", [httpx2.ConnectError("refused"), httpx2.ConnectTimeout("slow"), httpx2.PoolTimeout("")]
)
def test_a_command_that_never_left_means_unavailable(error: Exception) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        raise error

    with pytest.raises(TelematicsUnavailableError):
        _send(handler)


@pytest.mark.parametrize(
    "error", [httpx2.ReadTimeout("slow"), httpx2.RemoteProtocolError("cut"), httpx2.ReadError("")]
)
def test_a_command_without_an_answer_has_an_unknown_outcome(error: Exception) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        raise error

    with pytest.raises(TelematicsTimeoutError):
        _send(handler)


def test_a_command_that_hangs_ends_at_the_timeout() -> None:
    async def hang(request: httpx2.Request) -> httpx2.Response:
        await asyncio.sleep(10)
        return httpx2.Response(200)

    started = time.monotonic()
    with pytest.raises(TelematicsTimeoutError):
        _send(hang, timeout_s=0.2)

    assert time.monotonic() - started < 2
