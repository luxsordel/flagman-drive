import asyncio
import secrets
from datetime import UTC, datetime, timedelta

import httpx2
import pytest
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from flagman import worker
from flagman.core.config import Settings, WorkerSettings
from flagman.core.db import create_engine, create_session_factory
from flagman.identity.service import AuthCleanupResult
from flagman.notifications.expo import ExpoPushProvider
from flagman.notifications.fake_provider import FakePushProvider


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


async def _idle_job(*args: object) -> None:
    return None


async def test_each_job_keeps_its_own_interval(monkeypatch: pytest.MonkeyPatch) -> None:
    stop = asyncio.Event()
    runs: list[str] = []

    def job(name: str) -> object:
        async def run(*args: object) -> None:
            runs.append(name)
            if runs.count(worker.RESERVATION_EXPIRY) == 3:
                stop.set()

        return run

    monkeypatch.setattr(worker, "run_auth_cleanup", job(worker.AUTH_CLEANUP))
    monkeypatch.setattr(worker, "run_reservation_expiry", job(worker.RESERVATION_EXPIRY))
    monkeypatch.setattr(worker, "run_outbox_cleanup", _idle_job)
    monkeypatch.setattr(worker, "run_trip_battery_watch", _idle_job)
    intervals = {
        worker.AUTH_CLEANUP: timedelta(hours=1),
        worker.RESERVATION_EXPIRY: timedelta(milliseconds=50),
        worker.OUTBOX_CLEANUP: timedelta(hours=1),
        worker.TRIP_BATTERY_WATCH: timedelta(hours=1),
    }

    await asyncio.wait_for(worker.run(_offline_settings(), stop, intervals=intervals), 5)

    assert runs.count(worker.AUTH_CLEANUP) == 1
    assert runs.count(worker.RESERVATION_EXPIRY) == 3


@pytest.mark.integration
async def test_reservation_expiry_is_skipped_while_another_worker_holds_it(
    integration_settings: Settings,
) -> None:
    engine = create_engine(integration_settings)
    try:
        factory = create_session_factory(engine)
        settings = _worker_settings(integration_settings)
        assert (
            await worker.run_reservation_expiry(engine, factory, settings, datetime.now(UTC)) == 0
        )
        async with engine.connect() as other_worker:
            await other_worker.execute(
                text("SELECT pg_advisory_lock(:key)"), {"key": worker.RESERVATION_EXPIRY_LOCK}
            )
            await other_worker.commit()

            result = await worker.run_reservation_expiry(
                engine, factory, settings, datetime.now(UTC)
            )

            await other_worker.execute(
                text("SELECT pg_advisory_unlock(:key)"), {"key": worker.RESERVATION_EXPIRY_LOCK}
            )
        assert result is None
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
    monkeypatch.setattr(worker, "run_reservation_expiry", _idle_job)
    monkeypatch.setattr(worker, "run_outbox_cleanup", _idle_job)

    # An hour between runs: only the short retry lets the second run happen in time.
    hourly = dict.fromkeys(worker.INTERVALS, timedelta(hours=1))
    run = worker.run(_offline_settings(), stop, intervals=hourly, retry_after=timedelta(seconds=0))
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
    monkeypatch.setattr(worker, "run_reservation_expiry", _idle_job)
    monkeypatch.setattr(worker, "run_outbox_cleanup", _idle_job)

    # The interval is an hour: the run must end on the stop, not on the timer.
    await asyncio.wait_for(worker.run(_offline_settings(), stop), 5)


@pytest.mark.integration
async def test_dispatch_job_is_skipped_while_another_worker_holds_it(
    integration_settings: Settings,
) -> None:
    engine = create_engine(integration_settings)
    try:
        async with engine.connect() as other_worker:
            await other_worker.execute(
                text("SELECT pg_advisory_lock(:key)"), {"key": worker.NOTIFICATIONS_DISPATCH_LOCK}
            )
            await other_worker.commit()

            result = await worker.run_notifications_dispatch(
                engine, create_session_factory(engine), FakePushProvider(), datetime.now(UTC)
            )

            await other_worker.execute(
                text("SELECT pg_advisory_unlock(:key)"), {"key": worker.NOTIFICATIONS_DISPATCH_LOCK}
            )
        assert result is None
    finally:
        await engine.dispose()


async def test_notifications_go_out_on_their_own_short_interval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stop = asyncio.Event()
    dispatches = 0

    async def dispatch(*args: object) -> None:
        nonlocal dispatches
        dispatches += 1
        if dispatches == 3:
            stop.set()

    monkeypatch.setattr(worker, "run_auth_cleanup", _idle_job)
    monkeypatch.setattr(worker, "run_reservation_expiry", _idle_job)
    monkeypatch.setattr(worker, "run_outbox_cleanup", _idle_job)
    monkeypatch.setattr(worker, "run_notifications_dispatch", dispatch)
    settings = _offline_settings().model_copy(
        update={"notifications_provider": "fake", "notifications_dispatch_interval_seconds": 0.01}
    )
    hourly = dict.fromkeys(worker.INTERVALS, timedelta(hours=1))

    await asyncio.wait_for(worker.run(settings, stop, intervals=hourly), 5)

    assert dispatches == 3


async def test_without_a_provider_nothing_is_sent(monkeypatch: pytest.MonkeyPatch) -> None:
    stop = asyncio.Event()

    async def idle_then_stop(*args: object) -> None:
        stop.set()

    async def dispatch(*args: object) -> None:
        raise AssertionError("must not run")

    monkeypatch.setattr(worker, "run_auth_cleanup", idle_then_stop)
    monkeypatch.setattr(worker, "run_reservation_expiry", _idle_job)
    monkeypatch.setattr(worker, "run_outbox_cleanup", _idle_job)
    monkeypatch.setattr(worker, "run_notifications_dispatch", dispatch)

    await asyncio.wait_for(worker.run(_offline_settings(), stop), 5)


async def test_provider_follows_the_setting() -> None:
    async with httpx2.AsyncClient() as http:
        expo = _offline_settings().model_copy(
            update={"notifications_provider": "expo", "expo_access_token": SecretStr("t")}
        )
        fake = _offline_settings().model_copy(update={"notifications_provider": "fake"})

        expo_provider = worker.push_provider(expo, http)
        assert isinstance(expo_provider, ExpoPushProvider)
        assert expo_provider.access_token == "t"
        assert isinstance(worker.push_provider(fake, http), FakePushProvider)
        assert worker.push_provider(_offline_settings(), http) is None
