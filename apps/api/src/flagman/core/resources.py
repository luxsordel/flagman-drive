from dataclasses import dataclass
from typing import cast

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from starlette.requests import HTTPConnection

from flagman.core.config import Settings
from flagman.core.storage import ObjectStorage


@dataclass(frozen=True)
class Resources:
    """Process-wide clients, created in the app lifespan and shared by all requests."""

    settings: Settings
    engine: AsyncEngine
    session_factory: async_sessionmaker[AsyncSession]
    redis: Redis
    # None without storage keys: photo endpoints answer 503.
    storage: ObjectStorage | None


def get_resources(connection: HTTPConnection) -> Resources:
    """For HTTP requests and WebSockets alike."""
    return cast(Resources, connection.app.state.resources)
