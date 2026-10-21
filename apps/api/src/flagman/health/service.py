import asyncio
from collections.abc import Awaitable

import structlog
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from flagman.health.schemas import CheckStatus, ReadinessResponse

logger = structlog.get_logger(__name__)


async def _ping_database(engine: AsyncEngine) -> None:
    async with engine.connect() as connection:
        await connection.execute(text("SELECT 1"))


async def _ping_redis(redis: Redis) -> None:
    await redis.ping()


async def _run_check(name: str, check: Awaitable[None], timeout_seconds: float) -> CheckStatus:
    try:
        async with asyncio.timeout(timeout_seconds):
            await check
    except Exception as exc:
        # Only the exception class: messages of driver errors may contain hosts and user names.
        logger.warning("readiness_check_failed", check=name, error=type(exc).__name__)
        return CheckStatus.FAIL
    return CheckStatus.OK


async def check_readiness(
    engine: AsyncEngine, redis: Redis, timeout_seconds: float
) -> ReadinessResponse:
    database, redis_status = await asyncio.gather(
        _run_check("database", _ping_database(engine), timeout_seconds),
        _run_check("redis", _ping_redis(redis), timeout_seconds),
    )
    checks = {"database": database, "redis": redis_status}
    healthy = all(status is CheckStatus.OK for status in checks.values())
    return ReadinessResponse(
        status=CheckStatus.OK if healthy else CheckStatus.FAIL,
        checks=checks,
    )
