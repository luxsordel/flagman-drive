import secrets
import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from conftest import SettingsFactory
from flagman.core.app import create_app
from flagman.core.config import Settings
from flagman.core.db import create_engine, create_session_factory
from flagman.identity import repository as identity_repository
from flagman.identity.domain import AccessClaims, Language, issue_access_token


@pytest.fixture
def notifications_settings(
    integration_env: dict[str, str], migrated_database: None, make_settings: SettingsFactory
) -> Settings:
    return make_settings(**{name.lower(): value for name, value in integration_env.items()})


@pytest.fixture
def client(notifications_settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(notifications_settings), base_url="https://testserver") as client:
        yield client


@pytest.fixture
async def sessions(
    notifications_settings: Settings,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_engine(notifications_settings)
    try:
        yield create_session_factory(engine)
    finally:
        await engine.dispose()


def push_token() -> str:
    return f"ExponentPushToken[{secrets.token_urlsafe(16)}]"


class Caller:
    """A signed-in client: a row in `users` and a live session, as after a real sign-in."""

    def __init__(self, settings: Settings, user_id: uuid.UUID, session_id: uuid.UUID) -> None:
        self.id = user_id
        self.session_id = session_id
        token = issue_access_token(
            AccessClaims(user_id, session_id),
            settings.api_jwt_secret.get_secret_value(),
            datetime.now(UTC),
        )
        self.headers = {"Authorization": f"Bearer {token}"}


async def sign_in(
    sessions: async_sessionmaker[AsyncSession],
    settings: Settings,
    language: Language = Language.RU,
    user_id: uuid.UUID | None = None,
) -> Caller:
    """A new client, or a second session of `user_id`."""
    now = datetime.now(UTC)
    async with sessions() as db, db.begin():
        if user_id is None:
            phone = "+37529" + "".join(secrets.choice("0123456789") for _ in range(7))
            user_id = await identity_repository.get_or_create_user(db, phone, language, now)
        session_id = await identity_repository.create_session(
            db, user_id, uuid.uuid4(), now + timedelta(days=30), now
        )
    return Caller(settings, user_id, session_id)


@pytest.fixture
async def caller(
    sessions: async_sessionmaker[AsyncSession], notifications_settings: Settings
) -> Caller:
    return await sign_in(sessions, notifications_settings)


@pytest.fixture
async def other(
    sessions: async_sessionmaker[AsyncSession], notifications_settings: Settings
) -> Caller:
    return await sign_in(sessions, notifications_settings)
