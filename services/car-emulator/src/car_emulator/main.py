import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import httpx
import uvicorn
from fastapi import FastAPI

from car_emulator.api import HEALTH_PATH, router
from car_emulator.config import Settings
from car_emulator.domain import seed_fleet
from car_emulator.fleet import FleetService
from car_emulator.ports import Clock, TelemetrySink
from car_emulator.security import SignatureMiddleware
from car_emulator.telemetry import HttpWebhookSink, LogSink, run_telemetry_loop

TELEMETRY_TIMEOUT_S = 5.0


def utc_now() -> datetime:
    return datetime.now(UTC)


def create_app(settings: Settings, clock: Clock = utc_now) -> FastAPI:
    # A signed timestamp is accepted from tolerance in the past to tolerance in the future,
    # so a command id must be remembered for the whole span to block replays.
    replay_window = timedelta(seconds=2 * settings.signature_tolerance_s)
    fleet = FleetService(seed_fleet(settings.car_count, settings.seed, clock()), replay_window)

    @contextlib.asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        async with httpx.AsyncClient(timeout=TELEMETRY_TIMEOUT_S) as client:
            sink: TelemetrySink = LogSink()
            if settings.telemetry_url is not None:
                secret = settings.telemetry_secret.get_secret_value().encode()
                sink = HttpWebhookSink(client, str(settings.telemetry_url), secret, clock)
            task = asyncio.create_task(
                run_telemetry_loop(fleet, sink, settings.telemetry_interval_s, clock)
            )
            try:
                yield
            finally:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

    # Interactive docs are off: every non-health route needs a signature, so they would be useless.
    app = FastAPI(
        title="FLAGMAN car emulator",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.fleet = fleet
    app.state.clock = clock
    app.include_router(router)
    app.add_middleware(
        SignatureMiddleware,
        secret=settings.command_secret.get_secret_value().encode(),
        tolerance_s=settings.signature_tolerance_s,
        clock=clock,
        exempt_paths=frozenset({HEALTH_PATH}),
    )
    return app


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = Settings()
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port)
