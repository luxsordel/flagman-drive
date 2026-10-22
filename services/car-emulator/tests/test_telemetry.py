import asyncio
import json
from datetime import timedelta

import httpx
import pytest
from fastapi import FastAPI

from car_emulator.domain import seed_fleet
from car_emulator.fleet import FleetService
from car_emulator.schemas import TelemetryBatch
from car_emulator.signing import SIGNATURE_HEADER, TIMESTAMP_HEADER, verify
from car_emulator.telemetry import HttpWebhookSink, build_batch, run_telemetry_loop
from tests.conftest import TELEMETRY_SECRET, FakeClock

WEBHOOK_URL = "http://api.test/telematics/emulator/webhook?v=1"


@pytest.fixture
def fleet(clock: FakeClock) -> FleetService:
    return FleetService(seed_fleet(3, seed=1, now=clock()), timedelta(minutes=10))


async def test_webhook_is_signed(fleet: FleetService, clock: FakeClock) -> None:
    received: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        received.append(request)
        return httpx.Response(204)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        sink = HttpWebhookSink(client, WEBHOOK_URL, TELEMETRY_SECRET.encode(), clock)
        await sink.send(build_batch(fleet, clock))

    [request] = received
    assert request.method == "POST"
    assert request.headers["Content-Type"] == "application/json"
    verify(
        TELEMETRY_SECRET.encode(),
        timestamp_header=request.headers[TIMESTAMP_HEADER],
        signature_header=request.headers[SIGNATURE_HEADER],
        method="POST",
        target=b"/telematics/emulator/webhook?v=1",
        body=request.content,
        now=clock(),
        tolerance_s=300,
    )
    batch = TelemetryBatch.model_validate(json.loads(request.content))
    assert [car.id for car in batch.cars] == [car.id for car in fleet.list_cars()]


async def test_webhook_error_status_raises(fleet: FleetService, clock: FakeClock) -> None:
    transport = httpx.MockTransport(lambda _: httpx.Response(500))
    async with httpx.AsyncClient(transport=transport) as client:
        sink = HttpWebhookSink(client, WEBHOOK_URL, TELEMETRY_SECRET.encode(), clock)
        with pytest.raises(httpx.HTTPStatusError):
            await sink.send(build_batch(fleet, clock))


class _FlakySink:
    def __init__(self) -> None:
        self.calls = 0
        self.delivered = asyncio.Event()

    async def send(self, batch: TelemetryBatch) -> None:
        self.calls += 1
        if self.calls == 1:
            raise httpx.ConnectError("api is down")
        self.delivered.set()


async def test_loop_survives_delivery_errors(fleet: FleetService, clock: FakeClock) -> None:
    sink = _FlakySink()
    task = asyncio.create_task(run_telemetry_loop(fleet, sink, 0.001, clock))
    try:
        await asyncio.wait_for(sink.delivered.wait(), timeout=2)
    finally:
        task.cancel()
    assert sink.calls >= 2


async def test_app_lifespan_starts_and_stops_telemetry(app: FastAPI) -> None:
    async with app.router.lifespan_context(app):
        await asyncio.sleep(0.01)
