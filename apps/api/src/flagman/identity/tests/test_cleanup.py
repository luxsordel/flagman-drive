import asyncio
import secrets
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from http import HTTPStatus
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Row, text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from flagman.core.app import create_app
from flagman.core.config import Settings
from flagman.core.db import create_engine, create_session_factory
from flagman.identity import repository, service
from flagman.identity.domain import hash_refresh_token

pytestmark = pytest.mark.integration

RETENTION = timedelta(days=7)
# Clearly on either side of the retention, so the test does not race the clock.
LONG_AGO = RETENTION + timedelta(days=1)
RECENTLY = RETENTION - timedelta(days=1)


@pytest.fixture
def client(integration_settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(integration_settings), base_url="https://testserver") as client:
        yield client


def _query(settings: Settings, sql: str, **params: Any) -> list[Row[Any]]:
    async def run() -> list[Row[Any]]:
        engine = create_engine(settings)
        try:
            async with engine.begin() as connection:
                result = await connection.execute(text(sql), params)
                return list(result.all()) if result.returns_rows else []
        finally:
            await engine.dispose()

    return asyncio.run(run())


def _clean_up(
    settings: Settings, now: datetime, batch_size: int = 1000
) -> service.AuthCleanupResult:
    async def run() -> service.AuthCleanupResult:
        engine = create_engine(settings)
        try:
            async with create_session_factory(engine)() as db:
                return await service.clean_up_auth_data(db, now, RETENTION, batch_size)
        finally:
            await engine.dispose()

    return asyncio.run(run())


def _phone() -> str:
    return f"+37529{secrets.randbelow(10**7):07d}"


def _user(settings: Settings, now: datetime) -> uuid.UUID:
    user_id = uuid.uuid4()
    _query(
        settings,
        "INSERT INTO users (id, phone, created_at) VALUES (:id, :phone, :now)",
        id=user_id,
        phone=_phone(),
        now=now,
    )
    return user_id


def _session(
    settings: Settings,
    user_id: uuid.UUID,
    *,
    expires_at: datetime,
    revoked_at: datetime | None = None,
    device_id: uuid.UUID | None = None,
) -> uuid.UUID:
    session_id = uuid.uuid4()
    _query(
        settings,
        "INSERT INTO auth_sessions (id, user_id, device_id, created_at, expires_at, revoked_at)"
        " VALUES (:id, :user_id, :device_id, :created_at, :expires_at, :revoked_at)",
        id=session_id,
        user_id=user_id,
        device_id=device_id or uuid.uuid4(),
        created_at=expires_at - timedelta(days=30),
        expires_at=expires_at,
        revoked_at=revoked_at,
    )
    return session_id


def _token(settings: Settings, session_id: uuid.UUID, *, used_at: datetime | None = None) -> str:
    """Stores the hash of a new refresh token and returns the token itself."""
    token = secrets.token_urlsafe(32)
    _query(
        settings,
        "INSERT INTO refresh_tokens (token_hash, session_id, created_at, used_at)"
        " VALUES (:token_hash, :session_id, :created_at, :used_at)",
        token_hash=hash_refresh_token(token),
        session_id=session_id,
        created_at=(used_at or datetime.now(UTC)) - timedelta(minutes=1),
        used_at=used_at,
    )
    return token


def _staff_session(
    settings: Settings, *, expires_at: datetime, revoked_at: datetime | None = None
) -> uuid.UUID:
    staff_id, session_id = uuid.uuid4(), uuid.uuid4()
    _query(
        settings,
        "INSERT INTO staff_members (id, email, password_hash, role, created_at)"
        " VALUES (:id, :email, 'not-a-hash', 'operator', :created_at)",
        id=staff_id,
        email=f"cleanup-{staff_id}@flagman.by",
        created_at=expires_at - timedelta(days=60),
    )
    _query(
        settings,
        "INSERT INTO staff_sessions (id, staff_id, created_at, expires_at, revoked_at)"
        " VALUES (:id, :staff_id, :created_at, :expires_at, :revoked_at)",
        id=session_id,
        staff_id=staff_id,
        created_at=expires_at - timedelta(hours=12),
        expires_at=expires_at,
        revoked_at=revoked_at,
    )
    _query(
        settings,
        "INSERT INTO staff_refresh_tokens (token_hash, session_id, created_at)"
        " VALUES (:token_hash, :session_id, :created_at)",
        token_hash=hash_refresh_token(secrets.token_urlsafe(32)),
        session_id=session_id,
        created_at=expires_at - timedelta(hours=12),
    )
    return session_id


# Literal statements per table, so no SQL is put together from strings.
_EXISTING_IDS = {
    "auth_sessions": "SELECT id FROM auth_sessions WHERE id = ANY(:ids)",
    "staff_sessions": "SELECT id FROM staff_sessions WHERE id = ANY(:ids)",
}
_TOKEN_COUNTS = {
    "refresh_tokens": "SELECT count(*) AS n FROM refresh_tokens WHERE session_id = ANY(:ids)",
    "staff_refresh_tokens": (
        "SELECT count(*) AS n FROM staff_refresh_tokens WHERE session_id = ANY(:ids)"
    ),
}


def _existing(settings: Settings, table: str, ids: list[uuid.UUID]) -> set[uuid.UUID]:
    return {row.id for row in _query(settings, _EXISTING_IDS[table], ids=ids)}


def _token_count(settings: Settings, table: str, session_ids: list[uuid.UUID]) -> int:
    return int(_query(settings, _TOKEN_COUNTS[table], ids=session_ids)[0].n)


def _revoked(settings: Settings, session_id: uuid.UUID) -> bool:
    rows = _query(settings, "SELECT revoked_at FROM auth_sessions WHERE id = :id", id=session_id)
    return rows[0].revoked_at is not None


def _refresh(client: TestClient, refresh_token: str) -> Any:
    return client.post("/auth/token/refresh", json={"refresh_token": refresh_token})


def test_old_token_of_a_revoked_session_no_longer_signs_the_device_out(
    integration_settings: Settings, client: TestClient
) -> None:
    # Found by /security-review in #42: the token was rotated, then the device signed in again.
    # Replaying the old token used to revoke the device's new session every time.
    now = datetime.now(UTC)
    user_id, device_id = _user(integration_settings, now), uuid.uuid4()
    old_session = _session(
        integration_settings,
        user_id,
        device_id=device_id,
        expires_at=now + timedelta(days=20),
        revoked_at=now - LONG_AGO,
    )
    old_token = _token(integration_settings, old_session, used_at=now - LONG_AGO)
    new_session = _session(
        integration_settings, user_id, device_id=device_id, expires_at=now + timedelta(days=30)
    )
    new_token = _token(integration_settings, new_session)

    _clean_up(integration_settings, now)

    replay = _refresh(client, old_token)
    assert replay.status_code == HTTPStatus.UNAUTHORIZED
    assert replay.json()["type"] == "urn:flagman:problem:refresh-token-invalid"
    assert not _revoked(integration_settings, new_session)
    assert _refresh(client, new_token).status_code == HTTPStatus.OK


def test_reuse_in_a_live_session_is_still_recognized_after_cleanup(
    integration_settings: Settings, client: TestClient
) -> None:
    # A used token of a live session is what proves a copy later, however old it is.
    now = datetime.now(UTC)
    session_id = _session(
        integration_settings, _user(integration_settings, now), expires_at=now + timedelta(days=1)
    )
    copied = _token(integration_settings, session_id, used_at=now - timedelta(days=29))
    _token(integration_settings, session_id)

    _clean_up(integration_settings, now)

    assert _refresh(client, copied).status_code == HTTPStatus.UNAUTHORIZED
    assert _revoked(integration_settings, session_id)


def test_only_sessions_ended_before_the_retention_are_deleted(
    integration_settings: Settings,
) -> None:
    now = datetime.now(UTC)
    user_id = _user(integration_settings, now)
    live = _session(integration_settings, user_id, expires_at=now + timedelta(days=1))
    expired_long_ago = _session(integration_settings, user_id, expires_at=now - LONG_AGO)
    expired_recently = _session(integration_settings, user_id, expires_at=now - RECENTLY)
    revoked_long_ago = _session(
        integration_settings, user_id, expires_at=now + timedelta(days=1), revoked_at=now - LONG_AGO
    )
    revoked_recently = _session(
        integration_settings, user_id, expires_at=now + timedelta(days=1), revoked_at=now - RECENTLY
    )
    sessions = [live, expired_long_ago, expired_recently, revoked_long_ago, revoked_recently]
    for session_id in sessions:
        _token(integration_settings, session_id, used_at=now - LONG_AGO)
        _token(integration_settings, session_id)

    _clean_up(integration_settings, now)

    kept = [live, expired_recently, revoked_recently]
    assert _existing(integration_settings, "auth_sessions", sessions) == set(kept)
    assert _token_count(integration_settings, "refresh_tokens", [expired_long_ago]) == 0
    assert _token_count(integration_settings, "refresh_tokens", [revoked_long_ago]) == 0
    assert _token_count(integration_settings, "refresh_tokens", kept) == 2 * len(kept)


def test_staff_sessions_are_cleaned_up_the_same_way(integration_settings: Settings) -> None:
    now = datetime.now(UTC)
    live = _staff_session(integration_settings, expires_at=now + timedelta(hours=1))
    expired_long_ago = _staff_session(integration_settings, expires_at=now - LONG_AGO)
    expired_recently = _staff_session(integration_settings, expires_at=now - RECENTLY)
    revoked_long_ago = _staff_session(
        integration_settings, expires_at=now + timedelta(hours=1), revoked_at=now - LONG_AGO
    )
    revoked_recently = _staff_session(
        integration_settings, expires_at=now + timedelta(hours=1), revoked_at=now - RECENTLY
    )
    sessions = [live, expired_long_ago, expired_recently, revoked_long_ago, revoked_recently]

    _clean_up(integration_settings, now)

    kept = {live, expired_recently, revoked_recently}
    assert _existing(integration_settings, "staff_sessions", sessions) == kept
    assert _token_count(integration_settings, "staff_refresh_tokens", [expired_long_ago]) == 0
    assert _token_count(integration_settings, "staff_refresh_tokens", [revoked_long_ago]) == 0


def test_only_expired_codes_are_deleted(integration_settings: Settings) -> None:
    now = datetime.now(UTC)
    expired, pending = _phone(), _phone()
    for phone, expires_at in (
        (expired, now - timedelta(seconds=1)),
        (pending, now + timedelta(minutes=4)),
    ):
        _query(
            integration_settings,
            "INSERT INTO pending_otps (phone, code_hash, attempts, expires_at, created_at)"
            " VALUES (:phone, :code_hash, 0, :expires_at, :created_at)",
            phone=phone,
            code_hash="0" * 64,
            expires_at=expires_at,
            created_at=expires_at - timedelta(minutes=5),
        )

    _clean_up(integration_settings, now)

    rows = _query(
        integration_settings,
        "SELECT phone FROM pending_otps WHERE phone = ANY(:phones)",
        phones=[expired, pending],
    )
    assert {row.phone for row in rows} == {pending}


def test_cleanup_goes_through_every_batch(integration_settings: Settings) -> None:
    now = datetime.now(UTC)
    user_id = _user(integration_settings, now)
    ended = [_session(integration_settings, user_id, expires_at=now - LONG_AGO) for _ in range(5)]
    staff_ended = [
        _staff_session(integration_settings, expires_at=now - LONG_AGO) for _ in range(3)
    ]

    # Other tests share the database, so the counts are a floor rather than exact.
    result = _clean_up(integration_settings, now, batch_size=2)

    assert _existing(integration_settings, "auth_sessions", ended) == set()
    assert _existing(integration_settings, "staff_sessions", staff_ended) == set()
    assert result.client_sessions >= len(ended)
    assert result.staff_sessions >= len(staff_ended)


async def _hold_row_lock(engine: AsyncEngine, sql: str, **params: Any) -> AsyncConnection:
    """Opens a transaction that keeps the row locked until the returned connection is closed."""
    connection = await engine.connect()
    await connection.execute(text(sql), params)
    return connection


def test_rows_locked_by_a_sign_in_are_left_for_the_next_run(
    integration_settings: Settings,
) -> None:
    now = datetime.now(UTC)
    user_id = _user(integration_settings, now)
    locked_session, free_session = (
        _session(integration_settings, user_id, expires_at=now - LONG_AGO) for _ in range(2)
    )
    locked_phone, free_phone = _phone(), _phone()
    for phone in (locked_phone, free_phone):
        _query(
            integration_settings,
            "INSERT INTO pending_otps (phone, code_hash, attempts, expires_at, created_at)"
            " VALUES (:phone, :code_hash, 0, :expires_at, :created_at)",
            phone=phone,
            code_hash="0" * 64,
            expires_at=now - timedelta(minutes=1),
            created_at=now - timedelta(minutes=6),
        )

    async def clean_up_while_locked() -> None:
        engine = create_engine(integration_settings)
        try:
            # As a code check and a refresh do: SELECT ... FOR UPDATE on the row.
            code_check = await _hold_row_lock(
                engine,
                "SELECT 1 FROM pending_otps WHERE phone = :phone FOR UPDATE",
                phone=locked_phone,
            )
            refresh = await _hold_row_lock(
                engine, "SELECT 1 FROM auth_sessions WHERE id = :id FOR UPDATE", id=locked_session
            )
            try:
                async with create_session_factory(engine)() as db:
                    # SKIP LOCKED: the cleanup goes past the rows instead of waiting for them.
                    await asyncio.wait_for(service.clean_up_auth_data(db, now, RETENTION), 5)
            finally:
                await code_check.close()
                await refresh.close()
        finally:
            await engine.dispose()

    asyncio.run(clean_up_while_locked())

    sessions = [locked_session, free_session]
    assert _existing(integration_settings, "auth_sessions", sessions) == {locked_session}
    rows = _query(
        integration_settings,
        "SELECT phone FROM pending_otps WHERE phone = ANY(:phones)",
        phones=[locked_phone, free_phone],
    )
    assert {row.phone for row in rows} == {locked_phone}


def test_cleanup_gives_up_on_a_token_a_refresh_holds(integration_settings: Settings) -> None:
    # A refresh locks the token before the session, the cleanup the other way round. Without its
    # lock wait the cleanup would hang here; the wait itself is checked against deadlock_timeout in
    # test_cleanup_lock_wait_is_shorter_than_deadlock_detection.
    now = datetime.now(UTC)
    session_id = _session(
        integration_settings, _user(integration_settings, now), expires_at=now - LONG_AGO
    )
    token = _token(integration_settings, session_id, used_at=now - LONG_AGO)
    staff_session = _staff_session(integration_settings, expires_at=now - LONG_AGO)

    async def clean_up_while_locked() -> service.AuthCleanupResult:
        engine = create_engine(integration_settings)
        try:
            refresh = await _hold_row_lock(
                engine,
                "SELECT 1 FROM refresh_tokens WHERE token_hash = :token_hash FOR UPDATE",
                token_hash=hash_refresh_token(token),
            )
            try:
                async with create_session_factory(engine)() as db:
                    result = await asyncio.wait_for(
                        service.clean_up_auth_data(db, now, RETENTION), 5
                    )
                # The refresh was not chosen as a victim: its transaction still works.
                await refresh.execute(text("SELECT 1"))
            finally:
                await refresh.close()
        finally:
            await engine.dispose()
        return result

    result = asyncio.run(clean_up_while_locked())

    assert _existing(integration_settings, "auth_sessions", [session_id]) == {session_id}
    # The other kinds are still cleaned up in the same run.
    assert _existing(integration_settings, "staff_sessions", [staff_session]) == set()
    assert result.staff_sessions >= 1


def test_cleanup_lock_wait_is_shorter_than_deadlock_detection(
    integration_settings: Settings,
) -> None:
    # The side whose wait runs out first gives up, so the cleanup must time out before Postgres
    # looks for a deadlock and possibly cancels the refresh instead.
    async def read_timeouts() -> tuple[str, str]:
        engine = create_engine(integration_settings)
        try:
            async with create_session_factory(engine)() as db, db.begin():
                await repository.limit_lock_wait(db)
                lock_timeout = await db.scalar(text("SHOW lock_timeout"))
                deadlock_timeout = await db.scalar(text("SHOW deadlock_timeout"))
        finally:
            await engine.dispose()
        return str(lock_timeout), str(deadlock_timeout)

    lock_timeout, deadlock_timeout = asyncio.run(read_timeouts())

    assert lock_timeout == "100ms"
    assert _postgres_duration(deadlock_timeout) > repository.CLEANUP_LOCK_WAIT


def _postgres_duration(value: str) -> timedelta:
    """Parses what SHOW prints for a duration setting, e.g. "1s" or "100ms"."""
    units = {
        "ms": timedelta(milliseconds=1),
        "s": timedelta(seconds=1),
        "min": timedelta(minutes=1),
    }
    for suffix, unit in sorted(units.items(), key=lambda item: -len(item[0])):
        if value.endswith(suffix):
            return int(value.removesuffix(suffix)) * unit
    raise ValueError(f"unexpected duration {value!r}")
