from dataclasses import dataclass
from typing import cast

from fastapi import Request
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from flagman.core.config import Settings


@dataclass(frozen=True)
class Resources:
    """Process-wide clients, created in the app lifespan and shared by all requests."""

    settings: Settings
    engine: AsyncEngine
    session_factory: async_sessionmaker[AsyncSession]
    redis: Redis


def get_resources(request: Request) -> Resources:
    return cast(Resources, request.app.state.resources)
