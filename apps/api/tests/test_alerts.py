import secrets
from collections.abc import AsyncIterator, MutableMapping
from typing import Any

import pytest
from redis.asyncio import Redis

from flagman.core.alerts import SpikeMeter
from flagman.core.config import Settings
from flagman.core.ratelimit import Limit

pytestmark = pytest.mark.integration


@pytest.fixture
async def meter(integration_settings: Settings) -> AsyncIterator[SpikeMeter]:
    redis = Redis(
        host=integration_settings.redis_host,
        port=integration_settings.redis_port,
        password=integration_settings.redis_password.get_secret_value(),
    )
    try:
        yield SpikeMeter(redis)
    finally:
        await redis.aclose()


def _threshold(max_hits: int = 3, window_seconds: int = 60) -> Limit:
    # A fresh name per test: the Redis container is shared by the whole test session.
    return Limit(f"test-{secrets.token_hex(4)}", max_hits, window_seconds)


def _spikes(logs: list[MutableMapping[str, Any]]) -> list[MutableMapping[str, Any]]:
    return [entry for entry in logs if entry["event"] == "security_spike"]


async def test_warns_once_when_the_window_reaches_the_threshold(
    meter: SpikeMeter, alert_logs: list[MutableMapping[str, Any]]
) -> None:
    threshold = _threshold(max_hits=3, window_seconds=60)

    for _ in range(2):
        await meter.count(threshold)
    quiet = _spikes(alert_logs)
    for _ in range(4):
        await meter.count(threshold)

    assert quiet == []
    assert _spikes(alert_logs) == [
        {
            "event": "security_spike",
            "log_level": "warning",
            "metric": threshold.name,
            "count": 3,
            "window_seconds": 60,
        }
    ]


async def test_the_first_event_starts_a_window_that_later_ones_do_not_extend(
    meter: SpikeMeter,
) -> None:
    threshold = _threshold(window_seconds=60)
    await meter.count(threshold)
    await meter.redis.expire(f"alert:{threshold.name}", 5)

    await meter.count(threshold)

    assert 0 < await meter.redis.ttl(f"alert:{threshold.name}") <= 5


async def test_metrics_are_counted_separately(
    meter: SpikeMeter, alert_logs: list[MutableMapping[str, Any]]
) -> None:
    first, second = _threshold(max_hits=2), _threshold(max_hits=2)

    await meter.count(first)
    await meter.count(second)

    assert _spikes(alert_logs) == []


async def test_a_redis_failure_is_logged_not_raised(
    broken_spike_meter: SpikeMeter, alert_logs: list[MutableMapping[str, Any]]
) -> None:
    threshold = _threshold(max_hits=1)
    try:
        await broken_spike_meter.count(threshold)
    finally:
        await broken_spike_meter.redis.aclose()

    assert [entry["event"] for entry in alert_logs] == ["security_spike_meter_failed"]
    assert alert_logs[0]["metric"] == threshold.name
