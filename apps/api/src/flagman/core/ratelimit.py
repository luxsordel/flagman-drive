"""Fixed-window rate limits in Redis for sensitive endpoints (docs/03-security.md)."""

from dataclasses import dataclass

from fastapi import Request
from redis.asyncio import Redis

from flagman.core.errors import RateLimitedError
from flagman.core.resources import get_resources

KEY_PREFIX = "rl"


@dataclass(frozen=True)
class Limit:
    name: str
    max_hits: int
    window_seconds: int


class RateLimiter:
    def __init__(self, redis: Redis) -> None:
        self.redis = redis

    async def hit(self, limit: Limit, key: str) -> None:
        """Counts one hit and raises RateLimitedError once the window is over the limit.

        `key` ends up in Redis as is, so callers pass a hash instead of a phone or an email.
        Hits over the limit are counted too: hammering an exhausted limit does not reset it sooner.
        """
        redis_key = f"{KEY_PREFIX}:{limit.name}:{key}"
        async with self.redis.pipeline(transaction=True) as pipe:
            pipe.incr(redis_key)
            # NX: only the first hit starts the window, later hits must not extend it.
            pipe.expire(redis_key, limit.window_seconds, nx=True)
            pipe.ttl(redis_key)
            hits, _, ttl = await pipe.execute()
        if hits > limit.max_hits:
            raise RateLimitedError(retry_after_seconds=max(int(ttl), 1))


def get_rate_limiter(request: Request) -> RateLimiter:
    return RateLimiter(get_resources(request).redis)
