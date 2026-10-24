import asyncio
import secrets
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from flagman import worker
from flagman.core.config import Settings, WorkerSettings
from flagman.core.db import create_engine, create_session_factory
from flagman.identity.service import AuthCleanupResult


def _worker_settings(settings: Settings) -> WorkerSettings:
    return WorkerSettings(
        _env_file=None,
        postgres_host=settings.postgres_host,
        postgres_port=settings.postgres_port,
        postgres_user=settings.postgres_user,
        postgres_password=settings.postgres_password,
        postgres_db=settings.postgres_db,
    )


def _offline_settings() -> WorkerSettings:
    # Creating an engine does not connect, so nothing has to listen there.
    return WorkerSettings(
        _env_file=None,
        postgres_user="test",
        postgres_password=SecretStr("test"),
        postgres_db="test",
    )


async def _add_expired_code(engine: AsyncEngine) -> str:
    phone = f"+37529{secrets.randbelow(10**7):07d}"
    expires_at = datetime.now(UTC) - timedelta(minutes=1)
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO pending_otps (phone, code_hash, attempts, expires_at, created_at)"
                " VALUES (:phone, :code_hash, 0, :expires_at, :created_at)"
            ),
            {
                "phone": phone,
                "code_hash": "0" * 64,
                "expires_at": expires_at,
                "created_at": expires_at - timedelta(minutes=5),
            },
        )
    return phone


async def _code_exists(engine: AsyncEngine, phone: str) -> bool:
    async with engine.connect() as connection:
        found = await connection.scalar(
            text("SELECT count(*) FROM pending_otps WHERE phone = :phone"), {"phone": phone}
        )
    return bool(found)


@pytest.mark.integration
async def test_cleanup_job_deletes_and_releases_the_lock(integration_settings: Settings) -> None:
    engine = create_engine(integration_settings)
    try:
        phone = await _add_expired_code(engine)

        result = await worker.run_auth_cleanup(
            engine,
            create_session_factory(engine),
            _worker_settings(integration_settings),
            datetime.now(UTC),
        )

        assert result is not None
        assert not await _code_exists(engine, phone)
        # pg_locks rather than trying the lock: the pool may hand back the very connection that
        # held it, and an advisory lock can be taken again on its own connection.
        async with engine.connect() as connection:
            held = await connection.scalar(
                text(
                    "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory'"
                    " AND classid = 0 AND objid = :key AND objsubid = 1"
                ),
                {"key": worker.AUTH_CLEANUP_LOCK},
            )
        assert held == 0
    finally:
        await engine.dispose()


@pytest.mark.integration
async def test_cleanup_job_is_skipped_while_another_worker_holds_it(
    integration_settings: Settings,
) -> None:
    engine = create_engine(integration_settings)
    try:
        phone = await _add_expired_code(engine)
        async with engine.connect() as other_worker:
            await other_worker.execute(
                text("SELECT pg_advisory_lock(:key)"), {"key": worker.AUTH_CLEANUP_LOCK}
            )
            await other_worker.commit()

            result = await worker.run_auth_cleanup(
                engine,
                create_session_factory(engine),
                _worker_settings(integration_settings),
                datetime.now(UTC),
            )

            await other_worker.execute(
                text("SELECT pg_advisory_unlock(:key)"), {"key": worker.AUTH_CLEANUP_LOCK}
            )
        assert result is None
        assert await _code_exists(engine, phone)
    finally:
        await engine.dispose()


async def test_a_failed_run_is_retried_sooner_and_does_not_stop_the_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The first run on a fresh database fails until the migrations are applied.
    stop = asyncio.Event()
    calls = 0

    async def job(*args: object) -> AuthCleanupResult | None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ConnectionError("relation does not exist")
        stop.set()
        return None

    monkeypatch.setattr(worker, "run_auth_cleanup", job)

    # An hour between runs: only the short retry lets the second run happen in time.
    run = worker.run(
        _offline_settings(), stop, interval=timedelta(hours=1), retry_after=timedelta(seconds=0)
    )
    await asyncio.wait_for(run, 5)

    assert calls == 2


async def test_worker_stops_without_waiting_for_the_next_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stop = asyncio.Event()

    async def job(*args: object) -> AuthCleanupResult | None:
        stop.set()
        return None

    monkeypatch.setattr(worker, "run_auth_cleanup", job)

    # The interval is an hour: the run must end on the stop, not on the timer.
    await asyncio.wait_for(worker.run(_offline_settings(), stop), 5)
