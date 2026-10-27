import base64
import secrets
import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from http import HTTPStatus
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from conftest import SettingsFactory
from flagman.core.app import create_app
from flagman.core.config import Settings
from flagman.core.db import create_engine, create_session_factory
from flagman.identity import repository as identity_repository
from flagman.identity.domain import AccessClaims, Language, issue_access_token

WEB_ORIGIN = "http://localhost:8081"
RETURN_URL = "flagmandrive://payment-methods"


@pytest.fixture
def payments_settings(
    integration_env: dict[str, str], migrated_database: None, make_settings: SettingsFactory
) -> Settings:
    return make_settings(
        **{name.lower(): value for name, value in integration_env.items()},
        api_cors_origins=[WEB_ORIGIN],
        payments_provider="fake",
        payments_webhook_secret=secrets.token_hex(32),
        payments_token_encryption_key=base64.b64encode(secrets.token_bytes(32)).decode(),
    )


@pytest.fixture
def client(payments_settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(payments_settings), base_url="https://testserver") as client:
        yield client


@pytest.fixture
async def db(payments_settings: Settings) -> AsyncIterator[AsyncSession]:
    engine = create_engine(payments_settings)
    try:
        async with create_session_factory(engine)() as session:
            yield session
    finally:
        await engine.dispose()


@pytest.fixture
async def sessions(
    payments_settings: Settings,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """For calls that run at once: each needs a connection of its own."""
    engine = create_engine(payments_settings)
    try:
        yield create_session_factory(engine)
    finally:
        await engine.dispose()


async def add_user(settings: Settings) -> uuid.UUID:
    phone = "+37529" + "".join(secrets.choice("0123456789") for _ in range(7))
    engine = create_engine(settings)
    try:
        async with create_session_factory(engine)() as db, db.begin():
            return await identity_repository.get_or_create_user(
                db, phone, Language.RU, datetime.now(UTC)
            )
    finally:
        await engine.dispose()


class Caller:
    """A signed-in client with a row in `users`: cards and bindings point at it."""

    def __init__(self, settings: Settings, user_id: uuid.UUID) -> None:
        self.id = user_id
        token = issue_access_token(
            AccessClaims(user_id, uuid.uuid4()),
            settings.api_jwt_secret.get_secret_value(),
            datetime.now(UTC),
        )
        self.headers = {"Authorization": f"Bearer {token}"}


@pytest.fixture
async def caller(payments_settings: Settings) -> Caller:
    return Caller(payments_settings, await add_user(payments_settings))


@pytest.fixture
async def other(payments_settings: Settings) -> Caller:
    return Caller(payments_settings, await add_user(payments_settings))


def start_binding(client: TestClient, caller: Caller, return_url: str = RETURN_URL) -> Any:
    response = client.post(
        "/payment-cards/bindings", json={"return_url": return_url}, headers=caller.headers
    )
    assert response.status_code == HTTPStatus.CREATED, response.text
    return response.json()


def page_session(page_url: str) -> str:
    return parse_qs(urlsplit(page_url).query)["session"][0]


def submit_card(
    client: TestClient, page_url: str, number: str = "4242424242424242", **fields: str
) -> Any:
    form = {
        "session": page_session(page_url),
        "number": number,
        "expiry": "12/30",
        "cvc": "123",
        "action": "bind",
        **fields,
    }
    return client.post("/fake-provider/cards", data=form, follow_redirects=False)


def bind_card(client: TestClient, caller: Caller, number: str = "4242424242424242") -> Any:
    """Binds a card the way the app does and returns the finished binding."""
    started = start_binding(client, caller)
    response = submit_card(client, started["page_url"], number)
    assert response.status_code == HTTPStatus.SEE_OTHER, response.text
    binding = client.get(f"/payment-cards/bindings/{started['id']}", headers=caller.headers)
    assert binding.status_code == HTTPStatus.OK
    return binding.json()
