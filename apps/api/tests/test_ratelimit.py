import secrets
from collections.abc import AsyncIterator

import pytest
from redis.asyncio import Redis

from flagman.core.config import Settings
from flagman.core.errors import RateLimitedError
from flagman.core.ratelimit import Limit, RateLimiter

pytestmark = pytest.mark.integration


@pytest.fixture
async def limiter(integration_settings: Settings) -> AsyncIterator[RateLimiter]:
    redis = Redis(
        host=integration_settings.redis_host,
        port=integration_settings.redis_port,
        password=integration_settings.redis_password.get_secret_value(),
    )
    try:
        yield RateLimiter(redis)
    finally:
        await redis.aclose()


def _limit(max_hits: int = 2, window_seconds: int = 60) -> Limit:
    # A fresh name per test: the Redis container is shared by the whole test session.
    return Limit(f"test-{secrets.token_hex(4)}", max_hits, window_seconds)


async def test_hits_within_the_limit_pass(limiter: RateLimiter) -> None:
    limit = _limit(max_hits=2)

    await limiter.hit(limit, "key")
    await limiter.hit(limit, "key")


async def test_hit_over_the_limit_tells_when_the_window_ends(limiter: RateLimiter) -> None:
    limit = _limit(max_hits=1, window_seconds=60)
    await limiter.hit(limit, "key")

    with pytest.raises(RateLimitedError) as error:
        await limiter.hit(limit, "key")

    assert 0 < error.value.retry_after_seconds <= 60


async def test_keys_are_counted_separately(limiter: RateLimiter) -> None:
    limit = _limit(max_hits=1)

    await limiter.hit(limit, "first")
    await limiter.hit(limit, "second")


async def test_hits_over_the_limit_do_not_extend_the_window(limiter: RateLimiter) -> None:
    limit = _limit(max_hits=1, window_seconds=60)
    await limiter.hit(limit, "key")
    await limiter.redis.expire(f"rl:{limit.name}:key", 5)

    for _ in range(3):
        with pytest.raises(RateLimitedError) as error:
            await limiter.hit(limit, "key")

    assert error.value.retry_after_seconds <= 5
