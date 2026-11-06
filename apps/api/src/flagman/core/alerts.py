"""Warnings about spikes of security events across all users (docs/adr/0029)."""

import structlog
from redis.asyncio import Redis
from redis.exceptions import RedisError

from flagman.core.ratelimit import Limit

logger = structlog.get_logger(__name__)

KEY_PREFIX = "alert"


class SpikeMeter:
    def __init__(self, redis: Redis) -> None:
        self.redis = redis

    async def count(self, threshold: Limit) -> None:
        """Counts one event and logs `security_spike` when the window reaches the threshold.

        The counter is one per metric, not per user: nothing personal goes to Redis or the log.
        Only the hit that reaches the threshold warns, so a spike gives one line per window.
        Called after the event has happened, so a Redis failure is logged instead of raised:
        a command the car already carried out or a rejected sign-in must not turn into a 500.
        """
        key = f"{KEY_PREFIX}:{threshold.name}"
        try:
            async with self.redis.pipeline(transaction=True) as pipe:
                pipe.incr(key)
                # NX: only the first event starts the window, later ones must not extend it.
                pipe.expire(key, threshold.window_seconds, nx=True)
                hits, _ = await pipe.execute()
        except RedisError as exc:
            logger.warning(
                "security_spike_meter_failed", metric=threshold.name, error=type(exc).__name__
            )
            return
        if hits == threshold.max_hits:
            logger.warning(
                "security_spike",
                metric=threshold.name,
                count=hits,
                window_seconds=threshold.window_seconds,
            )
