from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi import FastAPI
from pydantic import SecretStr

from car_emulator.config import Settings
from car_emulator.main import create_app
from car_emulator.signing import signed_headers

# Test-only values, never used outside the test suite.
COMMAND_SECRET = "c" * 40
TELEMETRY_SECRET = "t" * 40
BASE_URL = "http://emulator.test"


class FakeClock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(datetime(2026, 10, 22, 12, 0, tzinfo=UTC))


@pytest.fixture
def settings() -> Settings:
    return Settings(
        command_secret=SecretStr(COMMAND_SECRET),
        telemetry_secret=SecretStr(TELEMETRY_SECRET),
        car_count=5,
        seed=7,
    )


@pytest.fixture
def app(settings: Settings, clock: FakeClock) -> FastAPI:
    return create_app(settings, clock)


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    # ASGITransport skips lifespan, so the telemetry loop does not run in API tests.
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE_URL) as c:
        yield c


def signed(
    method: str, path: str, body: bytes, now: datetime, secret: str = COMMAND_SECRET
) -> dict[str, str]:
    headers = signed_headers(secret.encode(), method, path.encode(), body, now)
    headers["Content-Type"] = "application/json"
    return headers
