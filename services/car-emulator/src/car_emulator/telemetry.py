import asyncio
import logging

import httpx

from car_emulator.fleet import FleetService
from car_emulator.ports import Clock, TelemetrySink
from car_emulator.schemas import CarState, TelemetryBatch
from car_emulator.signing import signed_headers

logger = logging.getLogger(__name__)


class LogSink:
    """Stub used until apps/api can receive telemetry webhooks."""

    async def send(self, batch: TelemetryBatch) -> None:
        logger.info("telemetry batch (log stub): %d cars", len(batch.cars))


class HttpWebhookSink:
    def __init__(self, client: httpx.AsyncClient, url: str, secret: bytes, clock: Clock) -> None:
        self._client = client
        self._url = httpx.URL(url)
        self._secret = secret
        self._clock = clock

    async def send(self, batch: TelemetryBatch) -> None:
        body = batch.model_dump_json().encode()
        headers = signed_headers(self._secret, "POST", self._url.raw_path, body, self._clock())
        headers["Content-Type"] = "application/json"
        response = await self._client.post(self._url, content=body, headers=headers)
        response.raise_for_status()


def build_batch(fleet: FleetService, clock: Clock) -> TelemetryBatch:
    return TelemetryBatch(
        sent_at=clock(), cars=[CarState.from_domain(car) for car in fleet.list_cars()]
    )


async def run_telemetry_loop(
    fleet: FleetService, sink: TelemetrySink, interval_s: float, clock: Clock
) -> None:
    while True:
        try:
            await sink.send(build_batch(fleet, clock))
        except httpx.HTTPError as exc:
            # A down API must not stop the emulator; the next tick sends a fresh snapshot anyway.
            logger.warning("telemetry delivery failed: %s", type(exc).__name__)
        await asyncio.sleep(interval_s)
